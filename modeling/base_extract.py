"""modeling.base_extract — 从定稿底图(DXF)提取构件级建模数据。

复用 _final2.py 已定稿的围合逻辑：
  墙量 STRICT = WALL+S-WALL+FIN双线间墙(68)  —— 造型/门扇/玻璃/反坎不计入墙
  围合 LOOSE  = WALL+S-WALL+方向FIN + 门窗洞口线 + 反坎 + 垭口分界(SEALS) + 门洞封开桥接
输出：房间(多边形+净面积+质心)、真墙段、洞口(门/窗)、竖向参数。
"""
import ezdxf
import re
import math
import numpy as np
import cv2
from collections import defaultdict

DXF = "装修图纸/227户型dxf/05.227户型平面图 （施工图纸） rev-33.dxf"
X0, Y0, X1, Y1 = 52161000, 227791000, 52186500, 227815500   # 母图带(含边距), 单位 mm
RES = 20.0                                                   # 栅格分辨率 mm
# 竖向参数(记忆确认)
V = {"story_height": 3170.0, "slab": 100.0, "floor_finish": 50.0, "ceiling_drop": 0.0}
V["clear_to_soffit"] = V["story_height"] - V["slab"] - V["floor_finish"]   # 板底净高 3020

OUT_KEYS = ("风井", "强电井", "弱电井", "水井", "消防水管井", "消防电梯", "空调搁板")
SEALS = [(52176828, 227805421, 52179628, 227805421)]         # 卧室一北 2800 垭口地面分界(非墙)

TRUE = {"WALL", "S-WALL"}; CLOSE = {"WINDOW", "DOOR", "door_plan"}

# p11(平面砌筑图) 标注净面积(参考口径, 与几何围合互相独立); 单位 m2。
# 模型以几何为真值, p11 仅作对账参考列; 无对应标注的房间(如电梯厅/公共区)不在此表。
P11_NET = {
    "客厅/茶室": 65.6, "餐厅": 16.3, "玄关": 7.3,
    "主人卧室": 14.9, "主人衣帽间": 11.2, "主人浴室": 9.8,
    "卧室一": 10.8, "卧室二": 12.4, "中厨": 11.6, "生活阳台": 5.6,
    "浴室一": 4.7, "浴室二": 4.5, "浴室三": 5.6,
    "收纳间": 3.3, "储物间": 2.8, "过厅": 1.8,
}


def p11_ref(names):
    """按房间名匹配 p11 标注净面积; 合并房间取各标注名之和; 无任何匹配返回 None。"""
    vals = [P11_NET[n] for n in names if n in P11_NET]
    return round(sum(vals), 2) if vals else None


def gross_area(poly, walls):
    """含墙外包面积(最宽的合理口径): 用夹住房间四侧真墙的最外线定界。
    房间净轮廓边=墙内面; 向外 300mm 内取该侧墙最外侧线=外面。
    作为 p11 的上界: p11 若落在[净, 外包]之间→属净↔含墙口径差; 若超外包→真异常。
    无法定界时返回 None。"""
    if not poly or len(walls) == 0:
        return None
    P = np.array(poly)
    rx0, rx1, ry0, ry1 = P[:, 0].min(), P[:, 0].max(), P[:, 1].min(), P[:, 1].max()
    W = np.array(walls, dtype=float)
    vert = W[np.abs(W[:, 2] - W[:, 0]) < 40]
    horiz = W[np.abs(W[:, 3] - W[:, 1]) < 40]

    def outer(vals, face, sgn):   # sgn=-1 左/下取最小(外); +1 右/上取最大(外)
        sel = vals[(vals >= face - 300) & (vals <= face + 40)] if sgn < 0 \
            else vals[(vals >= face - 40) & (vals <= face + 300)]
        if len(sel) == 0:
            return None
        return float(sel.min() if sgn < 0 else sel.max())
    L = outer(vert[:, 0], rx0, -1); R = outer(vert[:, 0], rx1, 1)
    B = outer(horiz[:, 1], ry0, -1); T = outer(horiz[:, 1], ry1, 1)
    if None in (L, R, B, T):
        return None
    a = (R - L) * (T - B) / 1e6
    return round(a, 2) if a > 0 else None


def _px(x): return int(round((x - X0) / RES))
def _py(y): return int(round((Y1 - y) / RES))
def _wx(gx): return X0 + gx * RES
def _wy(gy): return Y1 - gy * RES
def _inb(x, y, p=200): return X0 - p <= x <= X1 + p and Y0 - p <= y <= Y1 + p


def _segs_of(e):
    st = e.dxftype(); out = []
    if st == "LINE":
        s, t = e.dxf.start, e.dxf.end; out.append((s.x, s.y, t.x, t.y))
    elif st in ("LWPOLYLINE", "POLYLINE"):
        try:
            pts = [(p[0], p[1]) for p in e.get_points()]
            for a, b in zip(pts, pts[1:]): out.append((a[0], a[1], b[0], b[1]))
        except Exception:
            pass
    return out


def _decode(s):
    return re.sub(r"\\M\+5([0-9A-Fa-f]{4})", lambda m: bytes.fromhex(m.group(1)).decode("gbk"), s)


def _txt_of(e):
    if e.dxftype() == "TEXT": return e.dxf.text
    if e.dxftype() == "MTEXT":
        try: return e.plain_text()
        except Exception: return e.text
    return None


def _ang(dx, dy): return math.degrees(math.atan2(dy, dx)) % 180


def build(strict_enc=False):
    doc = ezdxf.readfile(DXF); msp = doc.modelspace()
    byL = defaultdict(list)

    def rec(ents, d):
        for e in ents:
            st = e.dxftype()
            if st in ("LINE", "LWPOLYLINE", "POLYLINE"):
                L = e.dxf.layer
                if L in TRUE or L == "FIN" or L in CLOSE or L == "CURB":
                    for s in _segs_of(e):
                        if _inb(s[0], s[1]) or _inb(s[2], s[3]): byL[L].append(s)
            elif st == "INSERT" and d < 8:
                try: rec(list(e.virtual_entities()), d + 1)
                except Exception: pass
    rec(msp, 0)

    # FIN 方向 + 双线间墙判定
    tdir = set()
    for a, b, c, d in byL["WALL"] + byL["S-WALL"]:
        if (c - a) or (d - b): tdir.add(round(_ang(c - a, d - b)))
    prim = []
    for a in sorted(tdir):
        if not any(abs(a - x) <= 5 or abs(a - x) >= 175 for x in prim): prim.append(a)

    def qual(fa):
        for p in prim:
            d = min(abs(fa - p) % 180, 180 - (abs(fa - p) % 180)); d2 = min(abs(fa - (p + 90)) % 180, 180 - (abs(fa - (p + 90)) % 180))
            if d <= 5 or d2 <= 5: return True
        return False
    fin_axial = [s for s in byL["FIN"] if ((s[2] - s[0]) or (s[3] - s[1])) and qual(_ang(s[2] - s[0], s[3] - s[1]))]

    def horizontal(s): return abs(s[3] - s[1]) <= abs(s[2] - s[0])
    FH = [s for s in fin_axial if horizontal(s)]; FV = [s for s in fin_axial if not horizontal(s)]

    def partner(s, g, h):
        if h: a0, a1 = sorted((s[0], s[2])); fx = (s[1] + s[3]) / 2.0
        else: a0, a1 = sorted((s[1], s[3])); fx = (s[0] + s[2]) / 2.0
        for t in g:
            if t is s: continue
            if h: tf = (t[1] + t[3]) / 2.0; b0, b1 = sorted((t[0], t[2]))
            else: tf = (t[0] + t[2]) / 2.0; b0, b1 = sorted((t[1], t[3]))
            off = abs(tf - fx)
            if 100 <= off <= 250:
                ov = min(a1, b1) - max(a0, b0); ln = min(a1 - a0, b1 - b0)
                if ln > 0 and ov >= max(300.0, 0.5 * ln): return True
        return False
    fin_part = [s for s in FH if partner(s, FH, True)] + [s for s in FV if partner(s, FV, False)]

    WALLS_STRICT = byL["WALL"] + byL["S-WALL"] + fin_part
    ENC = (byL["WALL"] + byL["S-WALL"] + fin_part) if strict_enc else (byL["WALL"] + byL["S-WALL"] + fin_axial)
    DOORM = byL["DOOR"] + byL["door_plan"]

    # 门标记栅格(膨胀 700mm)
    W = int((X1 - X0) / RES); H = int((Y1 - Y0) / RES)
    door_grid = np.zeros((H, W), np.uint8)
    for x0, y0, x1, y1 in DOORM:
        cv2.line(door_grid, (_px(x0), _py(y0)), (_px(x1), _py(y1)), 1, max(1, int(round(120 / RES))), lineType=8)
    door_grid = cv2.dilate(door_grid, np.ones((int(round(700 / RES)) | 1, int(round(700 / RES)) | 1), np.uint8))

    def door_near(gx, gy):
        return door_grid[min(max(gy, 0), H - 1), min(max(gx, 0), W - 1)] > 0

    grid = np.zeros((H, W), np.uint8)
    tw = max(1, int(round(180 / RES)))
    for x0, y0, x1, y1 in ENC + byL["WINDOW"] + DOORM + byL["CURB"] + SEALS:
        cv2.line(grid, (_px(x0), _py(y0)), (_px(x1), _py(y1)), 1, tw, lineType=8)

    nodes = []
    for x0, y0, x1, y1 in ENC:
        nodes.append((_px(x0), _py(y0))); nodes.append((_px(x1), _py(y1)))
    AUTO = int(1200 / RES); GMAX = int(2200 / RES); TOL = int(80 / RES); cell = max(1, GMAX)
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

    # P-TEXT 锚点
    anchors = []
    def addtxt(e):
        if e.dxftype() not in ("TEXT", "MTEXT") or e.dxf.layer != "P-TEXT": return
        t = _txt_of(e)
        if not t: return
        t = _decode(t.strip()); p = e.dxf.insert
        if not (X0 <= p.x <= X1 and Y0 <= p.y <= Y1): return
        if re.match(r"^[\d.]+\s*s\.m\.?$", t) or t in ("上", "下") or re.match(r"^\d+人$", t): return
        anchors.append((p.x, p.y, t))
    for e in msp:
        if e.dxftype() == "INSERT":
            try:
                for s in e.virtual_entities(): addtxt(s)
            except Exception: pass
        else: addtxt(e)

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
        area = comp.sum() * (RES * RES) / 1e6
        if area < 0.4: continue
        anc = [(x, y, t) for x, y, t in anchors if comp[_py(y), _px(x)]]
        for x, y, t in anc: assigned.add(t)
        names = [t for x, y, t in anc]
        isout = any(any(k in t for k in OUT_KEYS) for t in names)
        cx = X0 + (cent[i, 0] + 0.5) * RES; cy = Y1 - (cent[i, 1] + 0.5) * RES
        rooms.append({"names": names, "area": round(area, 2), "cx": cx, "cy": cy,
                      "is_out": isout, "poly": mask_to_poly(comp), "comp_id": i})

    # Minibar 并入主人卧室
    for x, y, t in anchors:
        if t == "Minibar" and t not in assigned:
            ci = lab[_py(y), _px(x)]
            if ci not in border and ci != 0:
                pk = (lab == ci); add = pk.sum() * (RES * RES) / 1e6
                for r in rooms:
                    if any("主人卧室" in nm for nm in r["names"]):
                        r["area"] = round(r["area"] + add, 2); break
                assigned.add("Minibar")

    # 挂 p11 参考面积(几何为真值, p11 仅对账) + 含墙外包面积(最宽口径上界)
    for r in rooms:
        r["p11"] = p11_ref(r["names"])
        r["gross_area"] = gross_area(r["poly"], WALLS_STRICT) if r["poly"] else None

    result = {
        "bounds": (X0, Y0, X1, Y1), "res": RES, "V": V,
        "rooms": rooms,
        "walls_strict": WALLS_STRICT,
        "enc": ENC,
        "doors": byL["DOOR"] + byL["door_plan"],
        "windows": byL["WINDOW"],
        "seals": SEALS,
        "grid": grid, "lab": lab, "n": n, "border": border, "anchors": anchors,
        "counts": {"WALL": len(byL["WALL"]), "S-WALL": len(byL["S-WALL"]),
                   "FIN_axial": len(fin_axial), "FIN_part": len(fin_part),
                   "DOOR": len(byL["DOOR"]), "door_plan": len(byL["door_plan"]),
                   "WINDOW": len(byL["WINDOW"]), "CURB": len(byL["CURB"])},
    }
    # 逐间吊顶标高→主吊顶净高 soffit_mm(P3b 几何可机读部分); 失败兜底结构净高(不影响主链路)
    try:
        from . import ceiling_levels
        ceiling_levels.annotate(result)
    except Exception:
        for r in rooms:
            r["soffit_mm"] = V["clear_to_soffit"]; r["ceiling_levels"] = []
            r["ceiling_drop_mm"] = 0.0; r["ceiling_min_mm"] = V["clear_to_soffit"]
    return result


if __name__ == "__main__":
    d = build()
    print("counts:", d["counts"])
    inn = [r for r in d["rooms"] if not r["is_out"]]
    print("户内空间 %d 个, 合计净面积 %.1f m2" % (len(inn), sum(r["area"] for r in inn)))
    for r in sorted(inn, key=lambda x: -x["area"]):
        print("  %-24s %6.2f  poly=%d点" % ("+".join(r["names"]), r["area"], len(r["poly"]) if r["poly"] else 0))
