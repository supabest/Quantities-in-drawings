"""房间围合：双线墙栅格化 + 门洞桥接 + 连通域 → 房间多边形。

算法逐行搬自 modeling/base_extract.build()（227 真实图纸验证过的实现），
仅把取线来源、边界、竖向参数与垭口分界改为入参：段集来自 LayerRoles，
标签来自 roles.labels，边界/res/seals/竖向参数由调用方给定。判据与常数
（栅格 20mm、线宽 180、门膨胀 700、桥接 1200/2200/80、CLOSE×2）一律未改。

本模块是纯几何：只依赖 numpy/cv2 与 .layers/.types，不导入 flask/app/modeling。
"""
from __future__ import annotations

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


def _vertical(params) -> dict:
    """竖向参数：floor_height_mm 是结构层高（story），不是净高。

    227 口径 story 3170 / slab 100 / finish 50 → clear 3020；缺省 2800 净高。
    """
    story = float(params.floor_height_mm or 2800.0)
    slab = float(params.slab_mm or 0.0)
    finish = float(params.floor_finish_mm or 0.0)
    return {"story_height": story, "slab": slab, "floor_finish": finish,
            "ceiling_drop": 0.0, "clear_to_soffit": story - slab - finish}


def build_rooms(doc, roles, bounds, params, seals=(), res=RES, strict_enc=False) -> dict:
    """把角色段集围合成房间。

    doc 只为签名对称（数据早已由 roles 取好，不再读图）；bounds=(x0,y0,x1,y1)
    是栅格窗口，units 与段坐标一致（mm）。roles 必须已按调用方 scope 裁过：
    本函数**不**再按 bounds 二次裁剪段集。
    """
    X0, Y0, X1, Y1 = bounds
    res = float(res)
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
    W = int((X1 - X0) / res); H = int((Y1 - Y0) / res)
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
    n, lab, stats, cent = cv2.connectedComponentsWithStats((1 - grid).astype(np.uint8), connectivity=4)
    border = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1]); border.discard(0)

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
    for i in range(1, n):
        if i in border: continue
        comp = (lab == i)
        area = comp.sum() * (res * res) / 1e6
        if area < MIN_ROOM_AREA_M2: continue
        anc = [(x, y, t) for x, y, t in anchors if comp[_py(y), _px(x)]]
        for x, y, t in anc: assigned.add(t)
        names = [t for x, y, t in anc]
        isout = any(any(k in t for k in OUT_KEYS) for t in names)
        cx = X0 + (cent[i, 0] + 0.5) * res; cy = Y1 - (cent[i, 1] + 0.5) * res
        rooms.append({"names": names, "area": round(area, 2), "cx": cx, "cy": cy,
                      "is_out": isout, "poly": mask_to_poly(comp), "comp_id": i})

    # Minibar 并入主人卧室
    for x, y, t in anchors:
        if t == "Minibar" and t not in assigned:
            ci = lab[_py(y), _px(x)]
            if ci not in border and ci != 0:
                pk = (lab == ci); add = pk.sum() * (res * res) / 1e6
                for r in rooms:
                    if any("主人卧室" in nm for nm in r["names"]):
                        r["area"] = round(r["area"] + add, 2); break
                assigned.add("Minibar")

    return {
        "bounds": (X0, Y0, X1, Y1), "res": res, "V": V,
        "rooms": rooms,
        "walls_strict": WALLS_STRICT,
        "enc": ENC,
        "doors": doors,
        "windows": windows,
        "seals": seals,
        "grid": grid, "lab": lab, "n": n, "border": border, "anchors": anchors,
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
        gx = int(round((x - X0) / res))
        gy = int(round((Y1 - y) / res))
        if not (0 <= gy < h and 0 <= gx < w):
            continue
        if int(lab[gy, gx]) in border:
            leaks.add(t)
    return sorted(leaks)
