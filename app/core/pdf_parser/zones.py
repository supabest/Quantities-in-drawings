"""洪水填充域提取（天花分区 / 地面分区）。

将 PDF 页面栅格化 → 二值化 → 连通域标记 → 提取各域多边形与面积。
"""
import math

import numpy as np
import cv2
from shapely.geometry import Point, Polygon
from shapely.validation import make_valid

from app.core.pdf_parser.extractor import raster, PT2MM


def _components(page, dpi=200, threshold=128, dilate_iter=2, drop_border=True):
    """栅格化 + 连通域标记（flood_zones / zone_polygons 共用，避免两份二值化代码漂移）。

    drop_border=True 剔除贴到图框四边的连通域——那就是整张图纸的背景空白：
    它的外轮廓覆盖全页，既会把标注吸成「归属到背景」，也会作为一整个
    「天花分区」入账（227 图纸每页背景 100~160m²，纯噪声面积）。
    返回 (n, blk, lab, stats, cent, sc, px2m, kept)；kept 为保留的域编号集合。
    """
    sc = dpi / 72.0
    px2m = (1.0 / sc) * PT2MM / 1000
    img = raster(page, dpi=dpi, grayscale=True)
    blk = cv2.dilate(
        (img < threshold).astype(np.uint8),
        np.ones((3, 3), np.uint8),
        iterations=dilate_iter,
    )
    n, lab, stats, cent = cv2.connectedComponentsWithStats(
        (1 - blk).astype(np.uint8)
    )
    h, w = blk.shape
    kept = set()
    for lid in range(1, n):
        x0 = int(stats[lid, cv2.CC_STAT_LEFT])
        y0 = int(stats[lid, cv2.CC_STAT_TOP])
        x1 = x0 + int(stats[lid, cv2.CC_STAT_WIDTH])
        y1 = y0 + int(stats[lid, cv2.CC_STAT_HEIGHT])
        if drop_border and (x0 <= 1 or y0 <= 1 or x1 >= w - 1 or y1 >= h - 1):
            continue
        kept.add(lid)
    return n, blk, lab, stats, cent, sc, px2m, kept


def flood_zones(page, dpi=200, threshold=128, dilate_iter=2,
                min_area_m2=0.02, max_area_m2=100):
    """洪水填充域提取。

    返回 [{"label": int, "area_m2": float, "bbox": (x0,y0,x1,y1),
           "centroid_pt": (cx,cy), "mask": ndarray}]

    参数：
    - dpi: 栅格化分辨率
    - threshold: 二值化阈值
    - dilate_iter: 膨胀迭代次数（连通墙线）
    - min_area_m2 / max_area_m2: 面积过滤

    注：label 只是本次栅格化的连通域编号，换 dpi 就变，不可作为跨页/跨函数
    的稳定标识；需要可归属、可融合的几何请用 zone_polygons()。
    """
    n, blk, lab, stats, cent, sc, px2m, kept = _components(
        page, dpi, threshold, dilate_iter)

    zones = []
    for lid in sorted(kept):
        area_m2 = stats[lid, cv2.CC_STAT_AREA] * px2m * px2m
        if area_m2 < min_area_m2 or area_m2 > max_area_m2:
            continue
        zones.append({
            "label": lid,
            "area_m2": round(area_m2, 3),
            "bbox": (
                int(stats[lid, cv2.CC_STAT_LEFT]),
                int(stats[lid, cv2.CC_STAT_TOP]),
                int(stats[lid, cv2.CC_STAT_LEFT] + stats[lid, cv2.CC_STAT_WIDTH]),
                int(stats[lid, cv2.CC_STAT_TOP] + stats[lid, cv2.CC_STAT_HEIGHT]),
            ),
            "centroid_pt": (cent[lid][0] / sc, cent[lid][1] / sc),
            "mask": (lab == lid).astype(np.uint8),
        })
    return zones


def zone_polygons(page, dpi=200, threshold=128, dilate_iter=2,
                  min_area_m2=0.02, max_area_m2=100):
    """洪水填充域 → Shapely 多边形（pt 坐标）。

    返回 [{"label", "area_m2", "polygon", "centroid", "wkt", "perimeter_m"}]

    wkt 是 pt 坐标下的多边形，供 Phase 5d 做 IoU 去重与跨源冲突仲裁；
    引线归属（leaders）也只认这里给出的域，不再自行栅格化探测，
    否则 label 编号与入账域对不上。

    注意：area_m2 取连通域**净像素面积**，wkt 取**外轮廓**——回字形域的外轮廓
    会把中间的独立岛框进来，两者口径不同不可互算；归属判定按最小含点域。
    """
    n, blk, lab, stats, cent, sc, px2m, kept = _components(
        page, dpi, threshold, dilate_iter)

    out = []
    for lid in sorted(kept):
        area_m2 = stats[lid, cv2.CC_STAT_AREA] * px2m * px2m
        if area_m2 < min_area_m2 or area_m2 > max_area_m2:
            continue
        mask = (lab == lid).astype(np.uint8)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        pts = [(pt[0][0] / sc, pt[0][1] / sc) for pt in c.reshape(-1, 1, 2)]
        if len(pts) < 3:
            continue
        poly = make_valid(Polygon(pts))
        out.append({
            "label": lid,
            "area_m2": round(area_m2, 3),
            "polygon": poly,
            "centroid": (poly.centroid.x, poly.centroid.y),
            "wkt": poly.wkt,
            "perimeter_m": round(poly.length * PT2MM / 1000, 3),
        })
    return out


def _containing(x, y, zones):
    """点命中的域：取**外接轮廓面积最小**的那个。

    洪水域的 wkt 取的是 RETR_EXTERNAL 外轮廓，环形/回字形区域的外轮廓会把
    中间的独立岛一起框住；按列表顺序返回第一个命中会把岛上标注判给外环，
    所以这里必须比面积而不是比顺序。先用外接矩形筛掉绝大多数域再精判。
    """
    best, barea = None, float('inf')
    for z in zones:
        poly = z["polygon"]
        mnx, mny, mxx, mxy = poly.bounds
        if not (mnx <= x <= mxx and mny <= y <= mxy):
            continue
        if poly.contains(Point(x, y)) and poly.area < barea:
            best, barea = z, poly.area
    return best


def point_in_zone(x, y, zones, default=None):
    """点 (x,y) pt 坐标 → 所在 zone label。未命中返回 default。"""
    z = _containing(x, y, zones)
    return z["label"] if z else default


def zone_of_point(x, y, zones, max_attach_dist=10.0, ring=12.0):
    """点 → 域归属，三级降级：内含 → 环形采样 → 边缘距离容差。

    环形采样应对符号正压在读图上（探针绕一圈找开阔域）；
    最后一级用点到多边形**边缘**距离而非到顶点距离——CAD 域角点稀疏，
    到顶点距离会把紧邻长边中点的标注判给远处的域。
    返回 (label|None, area_m2|None, distance_pt)；distance_pt 为 0 表示真内含。
    """
    z = _containing(x, y, zones)
    if z is not None:
        return z["label"], z["area_m2"], 0.0

    hits = []
    for adeg in range(0, 360, 45):
        q = _containing(x + ring * math.cos(math.radians(adeg)),
                        y + ring * math.sin(math.radians(adeg)), zones)
        if q is not None:
            hits.append(q)
    if hits:
        counts = {}
        for q in hits:
            counts[q["label"]] = counts.get(q["label"], 0) + 1
        top = max(counts.values())
        cand = [q for q in hits if counts[q["label"]] == top]
        best = min(cand, key=lambda q: q["polygon"].area)
        return best["label"], best["area_m2"], ring

    pt = Point(x, y)
    near, best = float('inf'), None
    for z in zones:
        d = pt.distance(z["polygon"])
        if d < near:
            near, best = d, z
    if best is None or near > max_attach_dist:
        # 未达容差也回报最近距离：上层能判断「差多少」而不是笼统失败
        return None, None, round(near, 2) if near != float('inf') else None
    return best["label"], best["area_m2"], round(near, 2)
