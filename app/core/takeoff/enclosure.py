"""房间围合：双线墙栅格化 + 门洞桥接 + 连通域 → 房间多边形。

算法逐行搬自 modeling/base_extract.build()（227 真实图纸验证过的实现），
仅把取线来源、边界、竖向参数与垭口分界改为入参：段集来自 LayerRoles，
标签来自 roles.labels，边界/res/seals/竖向参数由调用方给定。判据与常数
（栅格 20mm、线宽 180、门膨胀 700、桥接 1200/2200/80、CLOSE×2）一律未改。

吊顶标高（+2xxx 文字）自 T8 起也由本模块按**传入 doc/bounds** 提取并映射到
房间（此前 modeling/ceiling_levels.py 硬读 227 图），映射口径逐字对齐旧实现。

本模块只依赖 numpy/cv2/re/math，不导入 flask/app/modeling/layers/types。
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

import cv2
import numpy as np

RES = 20.0
MIN_ROOM_AREA_M2 = 0.4
OUT_KEYS = ("风井", "强电井", "弱电井", "水井", "消防水管井", "消防电梯", "空调搁板")
WALL_WIDTH_MM = 180.0     # 围合线栅格化线宽
DOOR_ARC_WIDTH_MM = 120.0  # 门标记线宽
DOOR_REACH_MM = 700.0     # 门附近桥接的膨胀半径
AUTO_BRIDGE_MM = 1200.0   # 无条件桥接的端点间距上限
MAX_BRIDGE_MM = 2200.0    # 桥接允许的最大缺口（超过视为真开口/漏房）
BRIDGE_TOL_MM = 80.0      # 端点对齐容差

# 吊顶标高：+2xxx(mm) 完成面标高文字，落点在窗口外扩 200mm 内才收
LEVEL_RE = re.compile(r"^\+\s?(2\d{3})$")
LEVEL_WINDOW_MM = 200.0
LEVEL_MAP_TOL_MM = 3000.0   # 标签不在 poly 内时按质心兜底
LEVEL_MAX_DEPTH = 6         # 块内 INSERT 展开深度上限（与旧实现一致）


def _decode_text(s: str) -> str:
    """还原 \\M+5xxxx 形式的 GBK 双字节转义（天正/理正文字常见）。"""
    return re.sub(r"\\M\+5([0-9A-Fa-f]{4})",
                  lambda m: bytes.fromhex(m.group(1)).decode("gbk", "replace"), s or "")


def _harvest_levels(doc, bounds) -> list:
    """从传入 doc 的 modelspace 收集 +2xxx 标高及其落点（mm）。

    口径对齐 modeling/ceiling_levels.harvest_levels：INSERT 的 attribs 用
    insert 坐标、块内 virtual_entities 递归（depth<6）；TEXT/MTEXT 用 dxf.insert，
    MTEXT 先 plain_text() 失败再 .text。doc 为 None（纯几何单测）→ 返回 []。
    """
    if doc is None:
        return []
    X0, Y0, X1, Y1 = bounds
    pad = LEVEL_WINDOW_MM
    out = []

    def add(text, x, y):
        t = _decode_text((text or "").strip())
        m = LEVEL_RE.match(t)
        if not m:
            return
        if not (X0 - pad <= x <= X1 + pad and Y0 - pad <= y <= Y1 + pad):
            return
        out.append({"elev_mm": float(m.group(1)), "x": float(x), "y": float(y)})

    def walk(ents, depth):
        for e in ents:
            t = e.dxftype()
            if t == "INSERT" and depth < LEVEL_MAX_DEPTH:
                ins = e.dxf.insert
                for a in e.attribs:
                    add(a.dxf.text, ins.x, ins.y)
                try:
                    walk(list(e.virtual_entities()), depth + 1)
                except Exception:
                    pass
            elif t == "TEXT":
                p = e.dxf.insert
                add(e.dxf.text, p.x, p.y)
            elif t == "MTEXT":
                p = e.dxf.insert
                try:
                    add(e.plain_text(), p.x, p.y)
                except Exception:
                    add(e.text, p.x, p.y)

    walk(doc.modelspace(), 0)
    return out


def _in_poly(x, y, poly) -> bool:
    """射线法点在多边形内（不依赖 shapely；poly 为 [(x,y),...]）。"""
    n = len(poly or [])
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            if x < xi + (y - yi) * (xj - xi) / (yj - yi):
                inside = not inside
        j = i
    return inside


def _annotate_ceilings(rooms, levels, clear):
    """按落点把标高映射到房间（含 is_out），挂吊顶四字段（口径逐字对齐 modeling）。

    第一个 poly 包含落点的房间胜出；都不包含 → 最近质心且 ≤LEVEL_MAP_TOL_MM。
    有标高：soffit=max(主顶)、drop=round(clear−max,1)、min=最低跌级；
    无标高：三字段兜底结构净高。标高 > clear 时 drop 为负是真实披露，不 clamp。
    """
    clear = float(clear)
    hits = [[] for _ in rooms]
    for lv in levels:
        x, y = lv["x"], lv["y"]
        idx = None
        for i, r in enumerate(rooms):
            if r.get("poly") and _in_poly(x, y, r["poly"]):
                idx = i
                break
        if idx is None:
            bd, cand = 1e18, None
            for i, r in enumerate(rooms):
                d = math.hypot(x - r["cx"], y - r["cy"])
                if d < bd:
                    bd, cand = d, i
            if cand is not None and bd <= LEVEL_MAP_TOL_MM:
                idx = cand
        if idx is not None:
            hits[idx].append(lv["elev_mm"])
    for r, hs in zip(rooms, hits):
        lvs = sorted(set(hs))
        r["ceiling_levels"] = lvs
        if lvs:
            r["soffit_mm"] = max(lvs)
            r["ceiling_drop_mm"] = round(clear - max(lvs), 1)
            r["ceiling_min_mm"] = min(lvs)
        else:
            r["soffit_mm"] = clear
            r["ceiling_drop_mm"] = 0.0
            r["ceiling_min_mm"] = clear


def _vertical(params) -> dict:
    """竖向参数：floor_height_mm 是结构层高（story），不是净高。

    227 口径 story 3170 / slab 100 / finish 50 → clear 3020；缺省 2800 净高。
    """
    story = float(params.effective_floor_height_mm() or 2800.0)
    slab = float(params.slab_mm or 0.0)
    finish = float(params.floor_finish_mm or 0.0)
    return {"story_height": story, "slab": slab, "floor_finish": finish,
            "ceiling_drop": 0.0, "clear_to_soffit": story - slab - finish}


def build_rooms(doc, roles, bounds, params, seals=(), res=RES, strict_enc=False) -> dict:
    """把角色段集围合成房间，并按 doc 内的 +2xxx 标高挂吊顶字段。

    doc 只用于吊顶标高提取（几何早已由 roles 取好），可为 None（纯几何单测）。
    bounds=(x0,y0,x1,y1) 是栅格窗口，units 与段坐标一致（mm）。roles 必须已按
    调用方 scope 裁过：本函数**不**再按 bounds 二次裁剪段集。bounds 会先按 res
    外扩对齐（floor/ceil 到 res 整数倍），返回的 data["bounds"] 是**对齐后**的窗口；
    这样同一几何在不同原点窗口下栅格相位一致，面积不随窗口漂移。吊顶标高的
    收集窗口即对齐后的 bounds 各向外扩 200mm。
    """
    if bounds is None:
        raise ValueError("bounds 不能为空：需要 (x0, y0, x1, y1)")
    X0, Y0, X1, Y1 = bounds
    res = float(res)
    if not res > 0:
        raise ValueError("res 必须为正数，实得 %r" % (res,))
    if not (X1 > X0 and Y1 > Y0):
        raise ValueError("bounds 必须满足 x1>x0 且 y1>y0，实得 %r" % (tuple(bounds),))
    # 栅格相位对齐：窗口原点/右上角外扩到 res 整数倍；227 窗口本身即为
    # 20 的整数倍，floor/ceil 是无操作，数值逐位不变。
    def _aligned(v, fn): return fn(v / res) * res
    def _number(v): return int(v) if float(v).is_integer() else float(v)
    X0 = _number(_aligned(X0, math.floor))
    Y0 = _number(_aligned(Y0, math.floor))
    X1 = _number(_aligned(X1, math.ceil))
    Y1 = _number(_aligned(Y1, math.ceil))
    seg = roles.segments
    wall_solid = seg.get("wall_solid", [])
    fin_axial = seg.get("wall_fin_axial", [])
    fin_part = seg.get("wall_fin_part", [])
    doors = seg.get("door", [])
    windows = seg.get("window", [])
    curbs = seg.get("curb", [])
    seals = list(seals)
    V = _vertical(params)

    def _px(x): return int(round((x - X0) / res))
    def _py(y): return int(round((Y1 - y) / res))
    def _wx(gx): return X0 + gx * res
    def _wy(gy): return Y1 - gy * res

    WALLS_STRICT = wall_solid + fin_part
    ENC = WALLS_STRICT if strict_enc else (wall_solid + fin_axial)

    # 门标记栅格(膨胀 700mm)
    W = int(round((X1 - X0) / res)); H = int(round((Y1 - Y0) / res))
    door_grid = np.zeros((H, W), np.uint8)
    for x0, y0, x1, y1 in doors:
        cv2.line(door_grid, (_px(x0), _py(y0)), (_px(x1), _py(y1)), 1,
                 max(1, int(round(DOOR_ARC_WIDTH_MM / res))), lineType=8)
    door_grid = cv2.dilate(door_grid, np.ones(
        (int(round(DOOR_REACH_MM / res)) | 1, int(round(DOOR_REACH_MM / res)) | 1), np.uint8))

    def door_near(gx, gy):
        return door_grid[min(max(gy, 0), H - 1), min(max(gx, 0), W - 1)] > 0

    grid = np.zeros((H, W), np.uint8)
    tw = max(1, int(round(WALL_WIDTH_MM / res)))
    for x0, y0, x1, y1 in ENC + windows + doors + curbs + seals:
        cv2.line(grid, (_px(x0), _py(y0)), (_px(x1), _py(y1)), 1, tw, lineType=8)

    nodes = []
    for x0, y0, x1, y1 in ENC:
        nodes.append((_px(x0), _py(y0))); nodes.append((_px(x1), _py(y1)))
    AUTO = int(AUTO_BRIDGE_MM / res); GMAX = int(MAX_BRIDGE_MM / res); TOL = int(BRIDGE_TOL_MM / res)
    cell = max(1, GMAX)
    hm = defaultdict(list)
    for gx, gy in nodes: hm[(gx // cell, gy // cell)].append((gx, gy))
    for gx, gy in nodes:
        cx, cy = gx // cell, gy // cell
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                for (hx, hy) in hm[(cx + ddx, cy + ddy)]:
                    dx = abs(hx - gx); dy = abs(hy - gy)
                    aligned_h = dy <= TOL and 1 <= dx <= GMAX
                    aligned_v = dx <= TOL and 1 <= dy <= GMAX
                    if not (aligned_h or aligned_v): continue
                    gap = dx if aligned_h else dy
                    if gap <= AUTO or door_near((gx + hx) // 2, (gy + hy) // 2):
                        cv2.line(grid, (gx, gy), (hx, hy), 1, tw, lineType=8)
    grid = cv2.morphologyEx(grid, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=2)
    n, lab, _, cent = cv2.connectedComponentsWithStats((1 - grid).astype(np.uint8), connectivity=4)
    border = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1]); border.discard(0)

    # 锚点取像素：x==X1 / y==Y0 会落在 W/H（越界一格），clamp 到最后一格，
    # 边界标签既不崩也不静默丢（压墙线的会进 unplaced_anchors）。
    def _gx(x): return min(max(_px(x), 0), W - 1)
    def _gy(y): return min(max(_py(y), 0), H - 1)

    # 房间标签锚点：roles.labels 已按调用方 scope 取好；这里只保留落在栅格窗口内的
    anchors = [(x, y, t) for x, y, t in roles.labels if X0 <= x <= X1 and Y0 <= y <= Y1]

    def mask_to_poly(mask):
        cnts, _ = cv2.findContours((mask * 255).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts: return None
        c = max(cnts, key=cv2.contourArea)
        c = cv2.approxPolyDP(c, 2.0, True)
        pts = [(float(_wx(p[0][0])), float(_wy(p[0][1]))) for p in c]
        return pts

    rooms = []
    assigned = set()
    placed = set()
    for i in range(1, n):
        if i in border: continue
        comp = (lab == i)
        area = comp.sum() * (res * res) / 1e6
        if area < MIN_ROOM_AREA_M2: continue
        anc = [(x, y, t) for x, y, t in anchors if comp[_gy(y), _gx(x)]]
        for x, y, t in anc:
            assigned.add(t); placed.add((x, y, t))
        names = [t for x, y, t in anc]
        isout = any(any(k in t for k in OUT_KEYS) for t in names)
        cx = X0 + (cent[i, 0] + 0.5) * res; cy = Y1 - (cent[i, 1] + 0.5) * res
        rooms.append({"names": names, "area": round(area, 2), "cx": cx, "cy": cy,
                      "is_out": isout, "poly": mask_to_poly(comp), "comp_id": i})

    # Minibar 并入主人卧室：只做标签归属，**不把龛位面积并进房间净面积**——
    # 该域 < MIN_ROOM_AREA 且是柜体龛，封账口径主卧地面/天棚按 17.15㎡ 计；
    # 加面积会把 227 地面从 163.75 虚增到 163.92。并入记录留给下游披露。
    # 无主人卧室时该锚点留给 unplaced 显式披露，不得假装归房。
    for x, y, t in anchors:
        if t == "Minibar" and t not in assigned:
            ci = lab[_gy(y), _gx(x)]
            if ci not in border and ci != 0:
                pk = (lab == ci); add = pk.sum() * (res * res) / 1e6
                master = next((r for r in rooms
                               if any("主人卧室" in nm for nm in r["names"])), None)
                if master is not None:
                    master.setdefault("merged_labels", []).append(
                        {"label": t, "area": round(add, 2)})
                    assigned.add("Minibar"); placed.add((x, y, t))

    # 落不到房间的锚点显式披露：压墙线(comp 0)或连通域被面积过滤未成房。
    # border 域锚点不在内——那是 detect_leaks 的漏房信号。
    # 井道/户外名天生不是房间，与 detect_leaks 的 isout 同口径过滤。
    room_comps = {r["comp_id"] for r in rooms}
    unplaced = []
    for x, y, t in anchors:
        if (x, y, t) in placed: continue
        if any(k in t for k in OUT_KEYS): continue
        ci = int(lab[_gy(y), _gx(x)])
        if ci in border: continue
        if ci == 0 or ci not in room_comps:
            unplaced.append((x, y, t))

    # 吊顶标高：按传入 doc + 对齐后的窗口提取并逐间映射（含 is_out；口径同旧 modeling）
    _annotate_ceilings(rooms, _harvest_levels(doc, (X0, Y0, X1, Y1)),
                       V["clear_to_soffit"])

    return {
        "bounds": (X0, Y0, X1, Y1), "res": res, "V": V,
        "rooms": rooms,
        "walls_strict": WALLS_STRICT,
        "enc": ENC,
        "doors": doors,
        "windows": windows,
        "seals": seals,
        "grid": grid, "lab": lab, "n": n, "border": border, "anchors": anchors,
        "unplaced_anchors": unplaced,
        "counts": {"wall_solid": len(wall_solid), "FIN_axial": len(fin_axial),
                   "FIN_part": len(fin_part), "door": len(doors),
                   "window": len(windows), "curb": len(curbs)},
    }


def detect_leaks(data) -> list:
    """房间标签落在 border 连通域里 → 该房间整间漏出图外，需要补垭口分界。

    rooms 只收非 border 连通域，故不能拿 room["comp_id"] 判；改看标签锚点的
    像素归属。井道/户外名（OUT_KEYS）天生在图外，不算漏房。
    """
    X0, Y0, X1, Y1 = data["bounds"]
    res = float(data["res"])
    lab = data["lab"]
    border = data["border"]
    h, w = lab.shape
    leaks = set()
    for x, y, t in data["anchors"]:
        if any(k in t for k in OUT_KEYS):
            continue
        # 与 build_rooms 同口径 clamp：边界锚点取最后一格，不因越界静默跳过
        gx = min(max(int(round((x - X0) / res)), 0), w - 1)
        gy = min(max(int(round((Y1 - y) / res)), 0), h - 1)
        if int(lab[gy, gx]) in border:
            leaks.add(t)
    return sorted(leaks)
