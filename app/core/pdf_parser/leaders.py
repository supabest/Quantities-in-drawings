"""引线追踪：从符号/标注出发，沿细线串链至尖端，归属到分区多边形。

沉淀自 _p3b_leader.py。线段串链与箭头判读沿用原算法（已验证）；
**归属**部分改为只认 zone_polygons() 给的多边形域——早期版本在本模块内
再栅格化一次并用连通域编号回探，换 dpi 或换面积阈值就和入账域对不上号。

与 app/orchestrator/mep/leaders.py 是两个追踪器，不要合并：
  - 本模块起点是**图形符号**（填充三角/材料代号锚点），目标域是**本页栅格
    出来的分区多边形**，服务天花/地面分区算量，图纸内自洽、无需标定；
  - mep 版起点是**注记文字 bbox**，靠方向感知规则（直行/转弯/离锚更远）行走，
    目标域是**跨图源配准后的 DXF 房间面**（RoomIndex.room_of），服务机电点位
    按房间计数，被 M2/M5 封账工件与 golden 测试钉住。
两者只共享「端点网格索引」这一手法（本模块 _endpoint_index 由此提速 10 倍），
线宽口径也不同：这里按 width_range 取细线带，mep 按 ==0.24 精确匹配。
"""
import math

from app.core.pdf_parser.extractor import thin_segments, filled_triangle_pairs
from app.core.pdf_parser.zones import zone_polygons, zone_of_point

CELL = 20.0   # 端点网格边长 (pt)；引线线段多在几~几十 pt，3×3 邻域足够覆盖阈值


def _endpoint_index(segs, cell=CELL):
    """端点 → 网格索引 {cell_key: [seg_idx]}，把逐段全表扫描降为邻域查找。

    与 app/orchestrator/mep/leaders.py 同一手法；本模块早期每跳都重扫
    2000+ 段，一页 100+ 标注要跑 6s，整套图纸光引线就一分半。
    """
    grid = {}
    for k, (x0, y0, x1, y1, _L) in enumerate(segs):
        for e in ((x0, y0), (x1, y1)):
            grid.setdefault((int(e[0] // cell), int(e[1] // cell)), []).append(k)
    return grid


def _near_indices(grid, x, y, cell=CELL):
    """点 (x,y) 邻域内可能触及的段号（3×3 单元格，覆盖 <=cell 的阈值）。"""
    gx, gy = int(x // cell), int(y // cell)
    out = set()
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            out.update(grid.get((gx + dx, gy + dy), ()))
    return sorted(out)


def trace_leaders(page, candidates=None, zones=None, dpi=200,
                  zone_min_area_m2=0.02, stem_max_dist=8.0,
                  chain_max_length=250, width_range=(0.18, 0.30),
                  max_attach_dist=10.0):
    """引线追踪 + 分区归属。

    参数：
    - page: fitz Page
    - candidates: [(cx, cy), ...] 起点（标高符号/材料代号锚点）；None 自动检测填充三角
    - zones: zone_polygons() 结果；None 则按 dpi/zone_min_area_m2 现算
    - stem_max_dist: 引线起始段距起点最大距离 (pt)
    - chain_max_length: 共线串链最大总长 (pt)
    - width_range: 引线宽度范围
    - max_attach_dist: 尖端未落入域时，吸附到最近域边缘的容差 (pt)

    返回 [{"candidate", "mode", "tip", "target_label", "target_area_m2",
           "leader_length", "has_arrow", "attach_dist"}]
    mode ∈ leader(串到尖端) / direct(起点即落域) / no_stem / no_leader
    attach_dist 为 None 表示无可吸附域（外层据此降级为「未能归属」）。
    """
    if candidates is None:
        candidates = filled_triangle_pairs(page)
    if zones is None:
        zones = zone_polygons(page, dpi=dpi, min_area_m2=zone_min_area_m2)

    segs = thin_segments(page, width_range=width_range)
    if not segs:
        return [{"candidate": c, "mode": "no_segments", "tip": c,
                 "target_label": None, "target_area_m2": None,
                 "leader_length": 0, "has_arrow": False, "attach_dist": None}
                for c in candidates]
    grid = _endpoint_index(segs)

    results = []
    for cx, cy in candidates:
        label, area, dist = zone_of_point(cx, cy, zones, max_attach_dist)

        stems = _find_stems(cx, cy, segs, grid, stem_max_dist)
        if not stems:
            results.append({
                "candidate": (cx, cy),
                "mode": "direct" if label is not None else "no_stem",
                "tip": (cx, cy),
                "target_label": label,
                "target_area_m2": area,
                "leader_length": 0,
                "has_arrow": False,
                "attach_dist": dist,
            })
            continue

        best = None
        for da, a, b, ang, L, k in sorted(stems):
            if L > 200:
                continue
            chain, total, tip = _chain_segments(a, b, ang, L, k, segs, grid,
                                                chain_max_length)
            lid, larea, ldist = zone_of_point(tip[0], tip[1], zones, max_attach_dist)
            cand = {
                "candidate": (cx, cy),
                "mode": "leader",
                "tip": tip,
                "target_label": lid,
                "target_area_m2": larea,
                "leader_length": total,
                "has_arrow": _detect_arrow(tip[0], tip[1], ang, segs, grid),
                "attach_dist": ldist,
            }
            if best is None or (lid is not None and best["target_label"] is None):
                best = cand
            if best["target_label"] is not None:
                break

        if best is None:
            best = {"candidate": (cx, cy), "mode": "no_leader", "tip": (cx, cy),
                    "target_label": None, "target_area_m2": None,
                    "leader_length": 0, "has_arrow": False, "attach_dist": None}
        # 串链落空但起点本身就在域内：退回直读，别把已成立的归属丢掉
        if best["target_label"] is None and label is not None:
            best.update(mode="direct", tip=(cx, cy), target_label=label,
                        target_area_m2=area, attach_dist=dist, leader_length=0)
        results.append(best)
    return results


def _find_stems(cx, cy, segs, grid, max_dist):
    stems = []
    for k in _near_indices(grid, cx, cy):
        x0, y0, x1, y1, L = segs[k]
        for a, b in (((x0, y0), (x1, y1)), ((x1, y1), (x0, y0))):
            da = math.dist(a, (cx, cy))
            if da <= max_dist and math.dist(b, (cx, cy)) > da:
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                if math.dist(mid, (cx, cy)) < 3:
                    continue
                ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
                stems.append((da, a, b, ang, L, k))
    return stems


def _chain_segments(a, b, ang, init_length, start_k, segs, grid, max_total):
    """从起始段 (a→b) 沿共线方向续走，返回 (链, 总长, 尖端)。

    used 必带：同一段反向也在阈值内且夹角 0°，早期没有已用集合时会立刻走回头路，
    在一根两段式引线上反复折返把总长堆到几百 pt，尖端停在起点附近，归属自然落空。
    """
    chain = [(a, b)]
    used = {start_k}
    cur = b
    ca = ang
    total = init_length
    while total < max_total:
        best = None
        best_d = 999
        for k in _near_indices(grid, cur[0], cur[1]):
            if k in used:
                continue
            x0, y0, x1, y1, L = segs[k]
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
                    best = (k, p, q, sa, L)
        if best is None:
            break
        k, p, q, sa, L = best
        used.add(k)
        chain.append((p, q))
        cur = q
        ca = sa
        total += L
    return chain, total, cur


def _detect_arrow(tipx, tipy, ang, segs, grid):
    hit = 0
    for k in _near_indices(grid, tipx, tipy):
        x0, y0, x1, y1, L = segs[k]
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
