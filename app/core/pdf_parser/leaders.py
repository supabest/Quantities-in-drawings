"""引线追踪：从标高符号/标记出发，沿细线串链至尖端，归属目标域。

沉淀自 _p3b_leader.py。
"""
import math
import numpy as np
import cv2

from app.core.pdf_parser.extractor import (
    raster, thin_segments, filled_triangle_pairs, PT2MM,
)


def trace_leaders(page, candidates=None, dpi=200,
                  stem_max_dist=8.0, chain_max_length=250,
                  width_range=(0.18, 0.30)):
    """引线追踪。

    参数：
    - page: fitz Page
    - candidates: [(cx, cy), ...] 标高符号中心；None 则自动检测
    - dpi: 栅格化分辨率（用于洪水填充域探测）
    - stem_max_dist: 引线起始段距符号最大距离 (pt)
    - chain_max_length: 共线串链最大总长 (pt)
    - width_range: 引线宽度范围

    返回 [{"candidate": (cx,cy), "mode": "leader"|"direct",
           "tip": (x,y), "target_label": int|None,
           "leader_length": float, "has_arrow": bool}]
    """
    sc = dpi / 72.0
    px2m = (1.0 / sc) * PT2MM / 1000

    if candidates is None:
        candidates = filled_triangle_pairs(page)

    segs = thin_segments(page, width_range=width_range)
    if not segs:
        return [{"candidate": c, "mode": "no_segments",
                 "tip": c, "target_label": None,
                 "leader_length": 0, "has_arrow": False}
                for c in candidates]

    segs_arr = np.array(segs)

    img = raster(page, dpi=dpi, grayscale=True)
    blk = cv2.dilate(
        (img < 128).astype(np.uint8),
        np.ones((3, 3), np.uint8),
        iterations=2,
    )
    n, lab, stats, cent = cv2.connectedComponentsWithStats(
        (1 - blk).astype(np.uint8)
    )

    results = []
    for cx, cy in candidates:
        stems = _find_stems(cx, cy, segs_arr, stem_max_dist)
        if not stems:
            lid = _probe_ring(cx, cy, lab, stats, blk, sc, px2m)
            results.append({
                "candidate": (cx, cy),
                "mode": "direct",
                "tip": (cx, cy),
                "target_label": lid,
                "leader_length": 0,
                "has_arrow": False,
            })
            continue

        best = None
        for da, a, b, ang, L in sorted(stems):
            if L > 200:
                continue
            chain, total, tip = _chain_segments(a, b, ang, L, segs_arr, chain_max_length)
            has_arrow = _detect_arrow(tip[0], tip[1], ang, segs_arr)
            lid = _probe_lid(tip[0], tip[1], lab, stats, blk, sc, px2m)
            if lid is None:
                lid = _probe_ring(tip[0], tip[1], lab, stats, blk, sc, px2m)
            if best is None or (lid is not None and best["target_label"] is None):
                best = {
                    "candidate": (cx, cy),
                    "mode": "leader",
                    "tip": tip,
                    "target_label": lid,
                    "leader_length": total,
                    "has_arrow": has_arrow,
                }
            if best["target_label"] is not None:
                break

        if best is None:
            best = {
                "candidate": (cx, cy),
                "mode": "no_leader",
                "tip": (cx, cy),
                "target_label": None,
                "leader_length": 0,
                "has_arrow": False,
            }
        results.append(best)
    return results


def _find_stems(cx, cy, segs, max_dist):
    stems = []
    for x0, y0, x1, y1, L in segs:
        for a, b in (((x0, y0), (x1, y1)), ((x1, y1), (x0, y0))):
            da = math.dist(a, (cx, cy))
            if da <= max_dist and math.dist(b, (cx, cy)) > da:
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                if math.dist(mid, (cx, cy)) < 3:
                    continue
                ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
                stems.append((da, a, b, ang, L))
    return stems


def _chain_segments(a, b, ang, init_length, segs, max_total):
    chain = [(a, b)]
    cur = b
    ca = ang
    total = init_length
    while total < max_total:
        best = None
        best_d = 999
        for x0, y0, x1, y1, L in segs:
            if L < 0.8 or L > 200:
                continue
            for p, q in (((x0, y0), (x1, y1)), ((x1, y1), (x0, y0))):
                d_start = math.dist(p, cur)
                if d_start > 3:
                    continue
                sa = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
                da = abs((sa - ca + 90) % 180 - 90)
                if da > 15:
                    continue
                if d_start < best_d:
                    best_d = d_start
                    best = (p, q, sa, L)
        if best is None:
            break
        p, q, sa, L = best
        chain.append((p, q))
        cur = q
        ca = sa
        total += L
    return chain, total, cur


def _detect_arrow(tipx, tipy, ang, segs):
    hit = 0
    for x0, y0, x1, y1, L in segs:
        if L < 0.8 or L > 4.5:
            continue
        for p, q in (((x0, y0), (x1, y1)), ((x1, y1), (x0, y0))):
            if math.dist(p, (tipx, tipy)) > 5:
                continue
            sa = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
            da = abs((sa - ang + 90) % 180 - 90)
            if 8 <= da <= 45:
                hit += 1
    return hit >= 2


def _probe_lid(x, y, lab, stats, blk, sc, px2m, max_r=10, min_m2=0.02):
    xi, yi = int(x * sc), int(y * sc)
    r1 = int(max_r * sc)
    y0s, x0s = max(0, yi - r1), max(0, xi - r1)
    win = blk[y0s:yi + r1, x0s:xi + r1]
    lbw = lab[y0s:yi + r1, x0s:xi + r1]
    ys, xs = np.where(win == 0)
    if len(ys) == 0:
        return None
    d2 = (ys - (yi - y0s)) ** 2 + (xs - (xi - x0s)) ** 2
    order = np.argsort(d2)
    for k in order[:400]:
        lid = int(lbw[ys[k], xs[k]])
        a = stats[lid, cv2.CC_STAT_AREA] * px2m * px2m
        if 0.02 <= a <= 100:
            return lid
        if a > 100:
            return None
    return None


def _probe_ring(cx, cy, lab, stats, blk, sc, px2m, rad=12.0):
    cands = []
    for adeg in range(0, 360, 45):
        x = cx + rad * math.cos(math.radians(adeg))
        y = cy + rad * math.sin(math.radians(adeg))
        lid = _probe_lid(x, y, lab, stats, blk, sc, px2m, max_r=6)
        if lid is not None:
            cands.append(lid)
    if not cands:
        return None
    return max(set(cands), key=lambda l: (cands.count(l), stats[l, cv2.CC_STAT_AREA]))
