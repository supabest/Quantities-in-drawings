"""MEP 房间归属配准（M2 封账版 _m2_takeoff.py 沉淀）。

坐标系：modeling.base_extract 定稿 DXF 房间面（mm，Y 向上）→ 施工图纸 PDF 页（pt，Y 向下）。
比例钉死 A3 1:100 打印：K = 1/35.2778 pt/mm，仅解平移 (tx, ty)。
标定流程：P01 房间标签 ↔ 小房间（排除 merged 面）质心中值初始化
          → 轮廓采样点到粗墙线距离网格搜索精调（erode 滤细线，防尺寸链/文字带偏）。
归属判据：射线法落房间面；未命中（墙装件落墙厚带）归边界最近房间（<=12pt≈420mm）；
          仍未命中返回 None —— 挂"未能归属"，不猜。
避坑记录：RETR_EXTERNAL 对环形域会产生吞全户实心多边形，归属必须用 label 像素直查
          得到的房间面（本模块的房间面来自 base_extract 连通域 label，天然规避）。
"""
import os
import re
import sys
import warnings

K = 1.0 / 35.2778          # pt per mm（1:100 打印）
PLAN_PAGE = 11             # P01 平面布置图（配准底图页 idx）
WALL_TOL_PT = 12.0         # 墙装件最近房间容差
DEFAULT_PDF = os.path.join("装修图纸", "227户型dxf",
                           "广州珠实金融城住宅项目--227户型施工图纸.pdf")
ARTIFACT = os.path.join("outputs", "preview", "vlm", "mep", "m2_attribution.json")


def room_faces():
    """定稿 DXF 房间面：[{name, area, poly_mm, cx, cy, merged}]（mm，Y 向上）。"""
    warnings.filterwarnings("ignore")
    stderr, sys.stderr = sys.stderr, open(os.devnull, "w")  # 吞 ezdxf proxy 警告
    try:
        import numpy as np
        import cv2
        from modeling import base_extract as B
        d = B.build()
        lab, (X0, Y0, X1, Y1), RES = d["lab"], d["bounds"], d["res"]
        rooms = []
        for r in d["rooms"]:
            if r["is_out"]:
                continue
            m = (lab == r["comp_id"]).astype(np.uint8)
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            c = max(cnts, key=cv2.contourArea)
            # 轮廓坐标是 mask 像素索引(gx,gy)，须还原为 DXF mm
            poly_mm = [(X0 + gx * RES, Y1 - gy * RES) for gx, gy in c.reshape(-1, 2)]
            name = "+".join(r["names"]) if r["names"] else \
                f"(无名@{r['cx'] / 1e3:.0f},{r['cy'] / 1e3:.0f})"
            rooms.append({"name": name, "area": float(r["area"]), "poly_mm": poly_mm,
                          "cx": r["cx"], "cy": r["cy"], "merged": len(r["names"]) > 1})
        return rooms
    finally:
        sys.stderr = stderr


def to_pt(poly, tx, ty):
    """DXF mm → PDF pt（Y 翻转）。"""
    return [(K * x + tx, -K * y + ty) for x, y in poly]


def label_anchors(page):
    """页内全部文字 span 中心：{去空白文本: (x, y)}（首次出现优先）。"""
    out = {}
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                t = re.sub(r"\s+", "", s["text"])
                if t and t not in out:
                    out[t] = ((s["bbox"][0] + s["bbox"][2]) / 2,
                              (s["bbox"][1] + s["bbox"][3]) / 2)
    return out


def initial_translation(rooms, anchors):
    """小房间质心 ↔ P01 标签中值初始化平移（merged 面质心≠标签位，必须排除）。"""
    import numpy as np
    pairs = []
    for r in rooms:
        if r["merged"]:
            continue
        if r["name"] in anchors:
            lx, ly = anchors[r["name"]]
            pairs.append((lx - K * r["cx"], ly + K * r["cy"], r["name"]))
    if not pairs:
        raise ValueError("无可用 房间↔标签 配对，检查页码与房间命名")
    return float(np.median([p[0] for p in pairs])), \
        float(np.median([p[1] for p in pairs])), [p[2] for p in pairs]


def wall_distance_map(page, dpi=200):
    """粗墙线距离图：erode 两次滤掉细线（≈1.4px），只剩墙体墨线。"""
    import numpy as np
    import cv2
    pm = page.get_pixmap(dpi=dpi)
    g = cv2.cvtColor(np.frombuffer(pm.samples, dtype=np.uint8)
                     .reshape(pm.height, pm.width, pm.n)[:, :, :3],
                     cv2.COLOR_RGB2GRAY)
    black = (g < 128).astype(np.uint8)
    wall = cv2.erode(black, np.ones((3, 3), np.uint8), iterations=2)
    dist = cv2.distanceTransform(1 - wall, cv2.DIST_L2, 3)
    return dist, dpi / 72.0


def calibrate(rooms, plan_page, dpi=200):
    """网格搜索贴墙对齐。返回 {tx, ty, wall_fit_px}。"""
    import numpy as np
    import fitz
    tx0, ty0, used = initial_translation(rooms, label_anchors(plan_page))
    dist, sc = wall_distance_map(plan_page, dpi)

    def cost(tx, ty):
        tot, n = 0.0, 0
        for r in rooms:
            poly = to_pt(r["poly_mm"], tx, ty)
            for i in range(len(poly)):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % len(poly)]
                L = np.hypot(x2 - x1, y2 - y1)
                for k in range(0, int(L), 4):
                    f = k / L if L else 0
                    px, py = int((y1 + (y2 - y1) * f) * sc), int((x1 + (x2 - x1) * f) * sc)
                    if 0 <= py < dist.shape[0] and 0 <= px < dist.shape[1]:
                        tot += min(dist[py, px], 20.0)
                        n += 1
        return tot / max(n, 1)

    best = (tx0, ty0, cost(tx0, ty0))
    for dtx in np.arange(-8, 8.01, 1.0):        # 粗搜 ±8pt
        for dty in np.arange(-8, 8.01, 1.0):
            c = cost(tx0 + dtx, ty0 + dty)
            if c < best[2]:
                best = (tx0 + dtx, ty0 + dty, c)
    tx, ty = best[0], best[1]
    for dtx in np.arange(-1, 1.01, 0.2):        # 细搜 ±1pt
        for dty in np.arange(-1, 1.01, 0.2):
            c = cost(tx + dtx, ty + dty)
            if c < best[2]:
                best = (tx + dtx, ty + dty, c)
    return {"tx": best[0], "ty": best[1], "wall_fit_px": best[2], "pairs": used}


def _inpoly(px, py, poly):
    inside, n = False, len(poly)
    for j in range(n):
        x1, y1 = poly[j]
        x2, y2 = poly[(j + 1) % n]
        if (y1 > py) != (y2 > py) and \
                px < (x2 - x1) * (py - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


class RoomIndex:
    """标定后的房间归属索引（PDF pt 坐标）。"""

    def __init__(self, rooms_pt, tx=None, ty=None):
        # rooms_pt: [{"name", "poly_pt", ...}] —— 多边形已是 PDF pt
        self.rooms = rooms_pt

    @classmethod
    def from_faces(cls, rooms, tx, ty):
        return cls([{**r, "poly_pt": to_pt(r["poly_mm"], tx, ty)} for r in rooms])

    def room_of(self, x, y, wall_tol=WALL_TOL_PT):
        import math
        for r in self.rooms:
            if _inpoly(x, y, r["poly_pt"]):
                return r["name"]
        best, bd = None, wall_tol
        for r in self.rooms:
            dmin = min(math.hypot(x - px, y - py) for px, py in r["poly_pt"])
            if dmin < bd:
                bd, best = dmin, r["name"]
        return best


def load_index(path=ARTIFACT):
    """快路径：直接读 M2 封账工件（含标定与房间多边形），不重跑配准。"""
    import json
    j = json.load(open(path, encoding="utf-8"))
    return RoomIndex(j["rooms"]), j.get("calib", {})


def main(pdf=DEFAULT_PDF, out=ARTIFACT):
    """重跑完整配准并回写 m2_attribution.json（标定参数复现入口）。"""
    import json
    import fitz
    doc = fitz.open(pdf)
    rooms = room_faces()
    calib = calibrate(rooms, doc[PLAN_PAGE])
    idx = RoomIndex.from_faces(rooms, calib["tx"], calib["ty"])
    json.dump({"calib": {k: calib[k] for k in ("tx", "ty", "wall_fit_px")} |
                        {"k_pt_per_mm": K},
               "rooms": [{"name": r["name"], "area": r["area"], "poly_pt": r["poly_pt"]}
                         for r in idx.rooms]},
              open(out, "w"), ensure_ascii=False)
    print(f"标定 tx={calib['tx']:.2f} ty={calib['ty']:.2f} "
          f"离墙均值={calib['wall_fit_px']:.2f}px → {out}")


if __name__ == "__main__":
    sys.path.insert(0, os.getcwd())
    main()
