"""洪水填充域提取（天花分区 / 地面分区）。

将 PDF 页面栅格化 → 二值化 → 连通域标记 → 提取各域多边形与面积。
"""
import numpy as np
import cv2
from shapely.geometry import Point, Polygon
from shapely.validation import make_valid

from app.core.pdf_parser.extractor import raster, PT2MM


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

    zones = []
    for lid in range(1, n):
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

    返回 [{"label": int, "area_m2": float, "polygon": Polygon, "centroid": (cx,cy)}]
    """
    sc = dpi / 72.0
    px2m = (1.0 / sc) * PT2MM / 1000

    img = raster(page, dpi=dpi, grayscale=True)
    blk = cv2.dilate(
        (img < threshold).astype(np.uint8),
        np.ones((3, 3), np.uint8),
        iterations=dilate_iter,
    )
    n, lab, stats, _ = cv2.connectedComponentsWithStats(
        (1 - blk).astype(np.uint8)
    )

    out = []
    for lid in range(1, n):
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
        cx = poly.centroid.x
        cy = poly.centroid.y
        out.append({
            "label": lid,
            "area_m2": round(area_m2, 3),
            "polygon": poly,
            "centroid": (cx, cy),
        })
    return out


def point_in_zone(x, y, zones, default=None):
    """点 (x,y) pt 坐标 → 所在 zone label。未命中返回 default。"""
    pt = Point(x, y)
    for z in zones:
        if z["polygon"].contains(pt):
            return z["label"]
    return default
