"""跨页配准：判断多页 PDF 是否为同一平面的复制/扩展。

沉淀自 _p3b_pagereg.py。
"""
import cv2
import numpy as np
from shapely.geometry import LineString
from shapely.ops import unary_union, polygonize

from app.core.pdf_parser.extractor import segments, raster


def register_pages(pages, zoom=2.0, threshold=40):
    """跨页配准。

    参数：
    - pages: [fitz.Page, ...] 待比较的页面列表
    - zoom: 栅格化缩放倍率
    - threshold: 二值化阈值

    返回 [{"page_idx": int, "translation": (tx,ty),
           "correlation": float, "iou": float, "is_duplicate": bool}]
    第一页为基准（translation=(0,0), correlation=1.0）。
    """
    if not pages:
        return []

    base_img = _binary_raster(pages[0], zoom, threshold)
    results = [{
        "page_idx": pages[0].number,
        "translation": (0, 0),
        "correlation": 1.0,
        "iou": 1.0,
        "is_duplicate": True,
    }]

    base_area = np.sum(base_img > 0)

    for page in pages[1:]:
        img = _binary_raster(page, zoom, threshold)
        tx, ty, corr = _template_match(base_img, img)
        iou = _compute_iou(base_img, img, tx, ty)
        area = np.sum(img > 0)
        area_ratio = area / max(base_area, 1)
        is_dup = corr > 0.9 and iou > 0.7 and 0.8 < area_ratio < 1.2

        results.append({
            "page_idx": page.number,
            "translation": (tx / zoom, ty / zoom),
            "correlation": round(corr, 4),
            "iou": round(iou, 4),
            "is_duplicate": is_dup,
        })
    return results


def _binary_raster(page, zoom, threshold):
    img = raster(page, dpi=int(zoom * 72), grayscale=True)
    return (img < threshold).astype(np.uint8)


def _template_match(base, target):
    h, w = base.shape
    th, tw = target.shape
    if th > h or tw > w:
        return 0, 0, 0.0
    result = cv2.matchTemplate(base, target, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    ty, tx = max_loc
    return tx, ty, float(max_val)


def _compute_iou(base, target, tx, ty):
    h, w = base.shape
    th, tw = target.shape
    shifted = np.zeros_like(base)
    y0 = max(0, ty)
    x0 = max(0, tx)
    y1 = min(h, ty + th)
    x1 = min(w, tx + tw)
    sy0 = y0 - ty
    sx0 = x0 - tx
    sy1 = sy0 + (y1 - y0)
    sx1 = sx0 + (x1 - x0)
    if sy0 < 0 or sx0 < 0 or sy1 > th or sx1 > tw:
        return 0.0
    shifted[y0:y1, x0:x1] = target[sy0:sy1, sx0:sx1]
    intersection = np.sum((base > 0) & (shifted > 0))
    union = np.sum((base > 0) | (shifted > 0))
    if union == 0:
        return 0.0
    return intersection / union


def page_face_polygons(page, snap_grid=1.5, min_area_m2=0.3, margin_pt=15):
    """页面闭合面多边形（pt 坐标），用于跨页几何比较。"""
    from app.core.pdf_parser.extractor import PT2MM
    segs = segments(page, snap_grid)
    lines = [LineString([a, b]) for a, b in segs]
    out = []
    for f in polygonize(unary_union(lines)):
        area_m2 = f.area * (PT2MM / 100) ** 2
        cx, cy = f.centroid.x, f.centroid.y
        if (area_m2 > min_area_m2
                and margin_pt < cx < page.rect.width - margin_pt
                and margin_pt < cy < page.rect.height - margin_pt):
            out.append(f)
    return out
