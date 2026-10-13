# T1′: P02~P04 页内标高三角定位 + 编号标记渲染（供 VLM 批量读值）
# 纪律: 几何只负责定位与面积; 标高数值由 VLM 转录注记, 读不到=未能读取(三态)
import fitz, cv2, numpy as np, json, os
from shapely.geometry import LineString, Point
from shapely.ops import unary_union, polygonize

PDF = "装修图纸/227户型dxf/广州珠实金融城住宅项目--227户型施工图纸.pdf"
OUT = "outputs/preview/vlm/elev"
os.makedirs(OUT, exist_ok=True)
PT2MM = 0.352778 * 100  # A3 1:100 -> mm/pt
S = 1.5                 # snap 网格 pt
DPI = 300

def bez(a, b, c, d, n=6):
    pts = [np.array(p, dtype=float) for p in (a, b, c, d)]
    out = []
    for i in range(n + 1):
        t = i / n
        q = (1 - t) ** 3 * pts[0] + 3 * (1 - t) ** 2 * t * pts[1] + 3 * (1 - t) * t ** 2 * pts[2] + t ** 3 * pts[3]
        out.append(tuple(q))
    return out

def snap(x, y):
    return (round(x / S) * S, round(y / S) * S)

def segments(page):
    segs = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                segs.append((snap(p1.x, p1.y), snap(p2.x, p2.y)))
            elif it[0] == "c":
                a, b, c, dd = it[1], it[2], it[3], it[4]
                pl = bez((a.x, a.y), (b.x, b.y), (c.x, c.y), (dd.x, dd.y))
                for u, v in zip(pl, pl[1:]):
                    segs.append((snap(*u), snap(*v)))
            elif it[0] == "re":
                r = it[1]
                cs = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                for u, v in zip(cs, cs[1:] + cs[:1]):
                    segs.append((snap(*u), snap(*v)))
    return segs

def faces(page):
    lines = [LineString([a, b]) for a, b in segments(page)]
    out = []
    for f in polygonize(unary_union(lines)):
        m2 = f.area * PT2MM ** 2 / 1e6
        cx, cy = f.centroid.x, f.centroid.y
        if m2 > 0.3 and 15 < cx < page.rect.width - 15 and 15 < cy < page.rect.height - 15:
            out.append({"wkt_area_m2": round(m2, 3), "centroid": (cx, cy),
                        "bbox": (f.bounds[0], f.bounds[1], f.bounds[2], f.bounds[3])})
    return out

def triangles(page):
    # P0x 标高符号=黑色双象限填充⊕: 两个 2~4pt 的'fs'小三角块对角配对
    quads = []
    for dr in page.get_drawings():
        if dr.get("fill") is None or dr.get("type") != "fs":
            continue
        r = dr["rect"]
        if 2 <= r.width <= 4 and 2 <= r.height <= 4:
            quads.append(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
    used, centers = set(), []
    for i in range(len(quads)):
        for j in range(i + 1, len(quads)):
            if i in used or j in used:
                continue
            (x1, y1), (x2, y2) = quads[i], quads[j]
            if (x1 - x2) ** 2 + (y1 - y2) ** 2 < 25:
                used.add(i); used.add(j)
                centers.append((round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1)))
    return centers

def annotate(page, cands):
    red = (1, 0, 0)
    for i, (cx, cy) in enumerate(cands):
        page.draw_circle((cx, cy), 4.5, color=red, width=0.4)
        page.insert_text((cx + 5, cy - 4), str(i), fontsize=7, color=red,
                         fontname="helv")
    return page

if __name__ == "__main__":
    doc = fitz.open(PDF)
    for idx in (12, 13, 14):
        page = doc[idx]
        cands = triangles(page)
        fs = faces(page)
        print(f"--- idx{idx}: 三角候选={len(cands)} 闭合面={len(fs)}")
        # 保存数据
        with open(f"{OUT}/idx{idx}_cands.json", "w") as f:
            json.dump({"cands": cands, "faces": fs}, f)
        # 编号标记后分 4 象限放大渲染
        annotate(page, cands)
        W, H = page.rect.width, page.rect.height
        for q, (x0, y0) in enumerate([(0, 0), (W / 2, 0), (0, H / 2), (W / 2, H / 2)]):
            clip = fitz.Rect(x0, y0, x0 + W / 2, y0 + H / 2)
            pm = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=clip)
            pm.save(f"{OUT}/idx{idx}_q{q}.png")
        print(f"    渲染 4 象限 -> {OUT}/idx{idx}_q*.png")
