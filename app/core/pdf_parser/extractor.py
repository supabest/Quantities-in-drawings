"""PDF 矢量几何提取原语。

从 fitz page 提取线段、闭合面、栅格图。
所有函数参数化（无硬编码路径/页码），可复用于任意 PDF。
"""
import math
import numpy as np
import cv2
import fitz
from shapely.geometry import LineString
from shapely.ops import unary_union, polygonize


PT2MM = 0.352778 * 100


def _snap(x, y, grid=1.5):
    return (round(x / grid) * grid, round(y / grid) * grid)


def _bezier_flatten(a, b, c, d, n=6):
    """三次 Bezier → 折线点列。"""
    pts = [np.array(p, dtype=float) for p in (a, b, c, d)]
    out = []
    for i in range(n + 1):
        t = i / n
        q = ((1 - t) ** 3 * pts[0]
             + 3 * (1 - t) ** 2 * t * pts[1]
             + 3 * (1 - t) * t ** 2 * pts[2]
             + t ** 3 * pts[3])
        out.append(tuple(q))
    return out


class PDFPage:
    """fitz Page 的缓存包装，提供惰性计算的几何/栅格/文字。"""

    def __init__(self, page, snap_grid=1.5, bezier_subdiv=6):
        self.page = page
        self.snap_grid = snap_grid
        self.bezier_subdiv = bezier_subdiv
        self._segments = None
        self._drawings = None

    @property
    def width(self):
        return self.page.rect.width

    @property
    def height(self):
        return self.page.rect.height

    @property
    def index(self):
        return self.page.number

    def raw_drawings(self):
        if self._drawings is None:
            self._drawings = self.page.get_drawings()
        return self._drawings

    def get_segments(self):
        if self._segments is None:
            self._segments = segments(self.page, self.snap_grid, self.bezier_subdiv)
        return self._segments

    def get_faces(self, min_area_m2=0.3, margin_pt=15):
        return faces(self.page, self.snap_grid, self.bezier_subdiv, min_area_m2, margin_pt)

    def get_raster(self, dpi=200, grayscale=False):
        return raster(self.page, dpi, grayscale)

    def get_text_spans(self):
        from app.core.pdf_parser.semantics import extract_text_spans
        return extract_text_spans(self.page)


def segments(page, snap_grid=1.5, bezier_subdiv=6):
    """page.get_drawings() → [(p1, p2), ...] 线段列表。

    处理三种 drawing item：
    - "l" (line): 直线
    - "c" (cubic bezier): 展平为折线
    - "re" (rect): 四条边
    """
    segs = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                segs.append((_snap(p1.x, p1.y, snap_grid),
                             _snap(p2.x, p2.y, snap_grid)))
            elif it[0] == "c":
                a, b, c, dd = it[1], it[2], it[3], it[4]
                pl = _bezier_flatten(
                    (a.x, a.y), (b.x, b.y), (c.x, c.y), (dd.x, dd.y),
                    n=bezier_subdiv,
                )
                for u, v in zip(pl, pl[1:]):
                    segs.append((_snap(*u, snap_grid), _snap(*v, snap_grid)))
            elif it[0] == "re":
                r = it[1]
                cs = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                for u, v in zip(cs, cs[1:] + cs[:1]):
                    segs.append((_snap(*u, snap_grid), _snap(*v, snap_grid)))
    return segs


def faces(page, snap_grid=1.5, bezier_subdiv=6, min_area_m2=0.3, margin_pt=15):
    """闭合面多边形提取。

    线段 → Shapely polygonize → 过滤面积/边距。
    返回 [{"wkt_area_m2": float, "centroid": (x,y), "bbox": (x0,y0,x1,y1), "polygon": Polygon}]
    """
    segs = segments(page, snap_grid, bezier_subdiv)
    lines = [LineString([a, b]) for a, b in segs]
    out = []
    for f in polygonize(unary_union(lines)):
        area_m2 = f.area * (PT2MM / 100) ** 2
        cx, cy = f.centroid.x, f.centroid.y
        if (area_m2 > min_area_m2
                and margin_pt < cx < page.rect.width - margin_pt
                and margin_pt < cy < page.rect.height - margin_pt):
            out.append({
                "wkt_area_m2": round(area_m2, 3),
                "centroid": (cx, cy),
                "bbox": (f.bounds[0], f.bounds[1], f.bounds[2], f.bounds[3]),
                "polygon": f,
            })
    return out


def raster(page, dpi=200, grayscale=False):
    """page → numpy array (H, W, 3) 或 (H, W)。"""
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    if grayscale:
        pm = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY)
        return np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width)
    pm = page.get_pixmap(matrix=matrix)
    return np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, pm.n)[:, :, :3].copy()


def thin_segments(page, width_range=(0.18, 0.30), min_length=1.0):
    """细线段（引线宽度范围），返回 [(x0,y0,x1,y1,length), ...]。"""
    segs = []
    for dr in page.get_drawings():
        w = dr.get("width") or 0
        if dr.get("type") == "s" and dr.get("fill") is None and width_range[0] <= w <= width_range[1]:
            for it in dr["items"]:
                if it[0] == "l":
                    p, q = it[1], it[2]
                    L = math.dist((p.x, p.y), (q.x, q.y))
                    if L > min_length:
                        segs.append((p.x, p.y, q.x, q.y, L))
    return segs


def filled_triangle_pairs(page, size_range=(2, 4), max_dist=5):
    """填充三角对（标高符号候选），返回 [(cx, cy), ...]。"""
    quads = []
    for dr in page.get_drawings():
        if dr.get("fill") is None or dr.get("type") != "fs":
            continue
        r = dr["rect"]
        if size_range[0] <= r.width <= size_range[1] and size_range[0] <= r.height <= size_range[1]:
            quads.append(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
    used, centers = set(), []
    for i in range(len(quads)):
        for j in range(i + 1, len(quads)):
            if i in used or j in used:
                continue
            (x1, y1), (x2, y2) = quads[i], quads[j]
            if (x1 - x2) ** 2 + (y1 - y2) ** 2 < max_dist ** 2:
                used.add(i)
                used.add(j)
                centers.append((round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1)))
    return centers
