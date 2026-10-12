"""modeling.wall_extract — Phase 1: 几何毛坯墙面面积。

从 base_extract 的房间多边形轮廓提取每段墙(边线), 算:
  每间毛墙面 = Σ(边长 × 净高)
  扣窗洞 = Σ(窗宽 × 窗高)   (窗从 WINDOW 线段沿墙聚合得到)
  扣门洞 = Σ(门宽 × 门高)   (门暂用默认 900×2100mm, 待后续精确化)
产出: {room_name: {perimeter_mm, gross_area_m2, window_area_m2, door_area_m2, net_wall_m2, edges:[...]}}
"""
import math
import numpy as np
from . import base_extract as B

V = B.V  # story_height, slab, floor_finish, clear_to_soffit
H_NET = V["clear_to_soffit"]  # 3020mm, 墙面计算基准高
H_DOOR = 2100  # mm, 默认门高
W_DOOR = 900   # mm, 默认门宽(户内门); 待立面/SD确认
H_WIN = 1500   # mm, 默认窗高(窗台900到梁底即约1500); 待立面确认
MATCH_TOL = 500  # mm, 洞口中心匹配到边的容差


def _edges(poly):
    """从闭合多边形提取每条边: [(x0,y0,x1,y1,length,angle), ...] (mm)"""
    P = np.array(poly)
    n = len(P)
    out = []
    for i in range(n):
        x0, y0 = P[i]; x1, y1 = P[(i + 1) % n]
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 50:  # 跳过极短碎边(<5cm, 通常是建模噪声)
            continue
        out.append((float(x0), float(y0), float(x1), float(y1), L))
    return out


def _edge_mid(e):
    return ((e[0] + e[2]) / 2.0, (e[1] + e[3]) / 2.0)


def _point_to_segment(px, py, x0, y0, x1, y1):
    """点到线段距离"""
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L2))
    proj_x, proj_y = x0 + t * dx, y0 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _cluster_openings(segments, edges, tol=MATCH_TOL):
    """将洞口线段按所属墙边聚合, 估算每边上的洞口宽度。
    segments: [(x1,y1,x2,y2), ...]
    edges: [(x0,y0,x1,y1,length), ...]
    返回 [edge_idx -> total_opening_width_mm]
    """
    hits = [0.0] * len(edges)
    used = set()
    for si, (sx0, sy0, sx1, sy1) in enumerate(segments):
        mx, my = (sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0
        seg_len = math.hypot(sx1 - sx0, sy1 - sy0)
        # 找最近的边
        best_e, best_d = -1, float("inf")
        for ei, e in enumerate(edges):
            d = _point_to_segment(mx, my, e[0], e[1], e[2], e[3])
            if d < best_d:
                best_d, best_e = d, ei
        if best_e >= 0 and best_d < tol:
            hits[best_e] += seg_len
    return hits


def extract_one(room, doors, windows):
    """对单间算墙面毛坯面积(上界)。洞口仅统计数量供参考, 不扣除(留给Phase2/3精确处理)。"""
    poly = room.get("poly")
    if not poly or len(poly) < 3:
        return None
    edges = _edges(poly)
    if not edges:
        return None
    perimeter = sum(e[4] for e in edges)
    gross = perimeter * H_NET / 1e6  # m2
    # 统计附近洞口(仅供参考, 不扣除)
    win_nearby = _cluster_openings(windows, edges)
    door_counts = _count_doors(doors, edges)
    n_win_clusters = sum(1 for w in win_nearby if w > 200)  # 边上有>200mm窗线段算有窗
    n_doors = sum(door_counts)
    nm = "+".join(room["names"]) or "(%.0f,%.0f)" % (room["cx"], room["cy"])
    return {
        "name": nm,
        "perimeter_mm": round(perimeter, 0),
        "gross_area_m2": round(gross, 2),
        "n_edges": len(edges),
        "n_windows_nearby": n_win_clusters,
        "n_doors_nearby": n_doors,
        "edges": [{"length_mm": round(e[4]), "win_seg_mm": round(win_nearby[i]),
                   "door_count": door_counts[i]} for i, e in enumerate(edges)],
    }


def _count_doors(doors, edges, tol=MATCH_TOL):
    """按边统计门的数量: 将短线段聚类 → 每团=一扇门。
    简化: 门线段极短(20mm), 若多条线段聚在<300mm范围内算一扇门。"""
    counts = [0] * len(edges)
    # 先把 door segments 按边归属
    edge_groups = {}  # edge_idx -> [segment_indices]
    for si, (sx0, sy0, sx1, sy1) in enumerate(doors):
        mx, my = (sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0
        best_e, best_d = -1, float("inf")
        for ei, e in enumerate(edges):
            d = _point_to_segment(mx, my, e[0], e[1], e[2], e[3])
            if d < best_d:
                best_d, best_e = d, ei
        if best_e >= 0 and best_d < tol:
            edge_groups.setdefault(best_e, []).append(si)
    # 对每边的 segments 做单链接聚类(距离<500mm 为同一扇)
    for ei, sidxs in edge_groups.items():
        pts = []
        for si in sidxs:
            dx0, dy0, dx1, dy1 = doors[si]
            pts.append(((dx0 + dx1) / 2.0, (dy0 + dy1) / 2.0))
        clusters = []
        for p in pts:
            found = False
            for ci, cl in enumerate(clusters):
                for q in cl:
                    if math.hypot(p[0] - q[0], p[1] - q[1]) < 500:
                        cl.append(p); found = True; break
                if found: break
            if not found:
                clusters.append([p])
        counts[ei] = len(clusters)
    return counts


def extract_all():
    """对所有户内房间提取墙面数据。"""
    d = B.build()
    doors = d["doors"]
    windows = d["windows"]
    results = []
    for r in d["rooms"]:
        if r["is_out"]:
            continue
        w = extract_one(r, doors, windows)
        if w:
            results.append(w)
    return results


if __name__ == "__main__":
    print("%-22s  周长m   毛墙面m2  边数  窗(近)  门(近)" % "房间")
    total_gross = 0.0
    for w in extract_all():
        total_gross += w["gross_area_m2"]
        print("  %-20s %6.1f  %8.1f   %2d    %d     %d" % (
            w["name"][:20], w["perimeter_mm"] / 1000, w["gross_area_m2"],
            w["n_edges"], w["n_windows_nearby"], w["n_doors_nearby"]))
    print("\n合计 毛墙面(上界) %.1f m2" % total_gross)
