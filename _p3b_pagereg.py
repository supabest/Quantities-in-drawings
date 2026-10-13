# P02/P03/P04 三页配准 + 分区面同位比对（判定 ceiling_zones §6 的 51.66㎡ 差额性质）
# 纪律: ①先验证页面布局(互相关)再谈锚点; ②几何只负责位置与面积, 标高读不到=未能读取
# 结论用途: 判 P03/P04 的"主面"是重复表达 P02, 还是 P02 之外的新增吊顶域
import fitz, json, numpy as np, cv2
from shapely.geometry import LineString, box
from shapely.ops import unary_union, polygonize
import _p3b_elev as E          # 复用同一套 segments/snap/polygonize 口径, 不另起炉灶

PAGES = (12, 13, 14)
PT2MM = E.PT2MM


def polys(page):
    """页内闭合面多边形（与 _p3b_elev.faces 同判据: >0.3㎡ 且离页边>15pt）。"""
    lines = [LineString([a, b]) for a, b in E.segments(page)]
    out = []
    for f in polygonize(unary_union(lines)):
        m2 = f.area * PT2MM ** 2 / 1e6
        cx, cy = f.centroid.x, f.centroid.y
        if m2 > 0.3 and 15 < cx < page.rect.width - 15 and 15 < cy < page.rect.height - 15:
            out.append(f)
    return out


def raster(page, zoom=2.0):
    pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
    img = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width)
    return (255 - img) > 40                     # 线条=1, 白底=0


def register(a, b):
    """在 a 中找 b 的最佳平移峰（互相关）。返回 (dx, dy, 归一化得分, 同位重合率)。"""
    A, B = raster(a), raster(b)
    h = min(A.shape[0], B.shape[0])
    w = min(A.shape[1], B.shape[1])
    A, B = A[:h, :w], B[:h, :w]
    res = cv2.matchTemplate(A.astype(np.uint8) * 255, B.astype(np.uint8) * 255,
                            cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    dx, dy = loc[1], loc[0]                     # B 相对 A 的平移(像素, zoom=2)
    if dx or dy:
        Bs = np.roll(np.roll(B, -dy, axis=0), -dx, axis=1)
    else:
        Bs = B
    inter = (A & Bs).sum()
    both = (A | Bs).sum()
    return dx / 2.0, dy / 2.0, float(score), float(inter / both) if both else 0.0


doc = fitz.open(E.PDF)
P = {i: doc[i] for i in PAGES}
F = {i: polys(P[i]) for i in PAGES}
U = {i: unary_union(F[i]) for i in PAGES}

print("=== ① 页面布局：整页线栅格互相关（先排'是不是同一张平面'）===")
for j in PAGES[1:]:
    dx, dy, sc, iou = register(P[12], P[j])
    print(f"  idx12 ← idx{j}: 平移=({dx:+.1f},{dy:+.1f})pt 相关峰={sc:.3f} 线栅格IoU={iou:.3f}")

print("\n=== ② 面级同位：P03/P04 的每面是否与 P02 某面重合 ===")
b2m = (PT2MM / 1000.0) ** 2
for j in PAGES[1:]:
    same_cent = cover_area = 0.0
    unmatched = []
    for f in F[j]:
        c = f.centroid
        hit = [g for g in F[12] if g.distance(c) < 1.0]      # 质心落点处 P02 有面
        if hit:
            same_cent += 1
            best = max(hit, key=lambda g: g.area)
            if abs(best.area - f.area) / max(f.area, 1e-9) < 0.05:
                cover_area += f.area * b2m
        else:
            unmatched.append(f)
    tot = sum(f.area * b2m for f in F[j])
    um = sum(f.area * b2m for f in unmatched)
    print(f"  idx{j}: 面 {len(F[j])} 个 Σ={tot:.2f}㎡ | 质心同位命中 {int(same_cent)} 个"
          f"({same_cent/len(F[j]):.0%}) | 与P02等面积(±5%) {cover_area:.2f}㎡"
          f" | 未同位 {len(unmatched)} 个 Σ={um:.2f}㎡")
    for f in sorted(unmatched, key=lambda g: -g.area)[:5]:
        print(f"      未同位: {f.area*b2m:6.2f}㎡ @({f.centroid.x:.0f},{f.centroid.y:.0f})")

print("\n=== ③ 面并集几何重叠（不依赖质心）===")
for j in PAGES[1:]:
    inter = U[12].intersection(U[j]).area * b2m
    print(f"  idx12∩idx{j}: 交={inter:.2f}㎡  P02独有={U[12].area*b2m-inter:.2f}㎡ "
          f" idx{j}独有={U[j].area*b2m-inter:.2f}㎡  Jaccard="
          f"{inter/(U[12].area*b2m+U[j].area*b2m-inter):.3f}")

print(f"\n=== ④ 已封账 P02 水平域基准 ===")
print("  P02 分级水平面 Σ=97.78㎡（21 域，ceiling_zones.md §1）；"
      f"本页全闭合面 Σ={sum(f.area*b2m for f in F[12]):.2f}㎡（含家具/格栅/注记框等非吊顶面）")
