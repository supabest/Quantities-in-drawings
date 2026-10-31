"""由房间围合面产出 1/2/3/4/5 分部行，每行给可独立复算的算式。

口径（与 modeling/wall_geometry.py 逐字同判据，不导入 modeling）：
  · 1 地面 / 3 天棚：房间围合多边形净面积（栅格围合，鞋带公式可复算）；
  · 2 墙面：Σ边长×净高 − 门宽×门高 − 窗宽×窗高；净高优先 room["soffit_mm"]，
    否则 params.effective_floor_height_mm()（含业态默认），再否则 2800；
  · 5 踢脚：净周长 − 门洞总宽（窗不落地，不扣）；
  · 4 门窗洞口：按**物理洞口**计（每条 poly 边投影 opening_intervals →
    同类中点距 <300mm 去重），unit=樘，每洞口一行——不按线段条数计。

来源标注（设计稿 2026-10-17 general-takeoff-engine-design.md:253-255）：
  · 1 地面 / 3 天棚：矢量墙线直接围合量出 → 图纸实测（默认，不显式传）；
  · 2 墙面 / 4 洞口 / 5 踢脚：由参数派生（净高、门洞宽、洞口尺寸）→ 几何推算。

纯包：只依赖 math 与 .types；不得导入 flask / app.models / modeling。
"""
from __future__ import annotations

import math

from .types import Issue, Row

# 洞口判据（与 modeling/wall_geometry.py 常数一致）
TOL = 400.0          # 洞口中点到墙边垂距容差 mm
PAR_TOL = 25.0       # 窗线与墙边平行容差（°）
SKIP_EDGE = 100.0    # 碎边
MERGE_GAP = 60.0     # 同边区间合并间隔 mm
MIN_W = 200.0        # 洞口最小净宽 mm，以下视为碎线
CLAMP = 20.0         # 区间向边外延的截断余量 mm
DEDUP = 300.0        # 同一物理洞口跨房间去重：同类中点距阈值 mm
FALLBACK_HEIGHT_MM = 2800.0


def _seg_ang(x0, y0, x1, y1):
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180


def _para(a, b, tol=PAR_TOL):
    d = abs(a - b) % 180
    return min(d, 180 - d) <= tol


def _pt_seg_dist(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L2))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def _edges(poly):
    """围合多边形 → [(x0,y0,x1,y1,L)]，跳过碎边。"""
    out = []
    n = len(poly or [])
    if n < 3:
        return out
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        L = math.hypot(x1 - x0, y1 - y0)
        if L < SKIP_EDGE:
            continue
        out.append((float(x0), float(y0), float(x1), float(y1), L))
    return out


def _project_ivs(segs, e, parallel_only):
    """洞口中点落在墙边容差内 → 投影成沿边一维区间（未合并）。"""
    ex0, ey0, ex1, ey1, L = e
    dx, dy = (ex1 - ex0) / L, (ey1 - ey0) / L
    eang = _seg_ang(ex0, ey0, ex1, ey1)
    ivs = []
    for sx0, sy0, sx1, sy1 in segs or []:
        mx, my = (sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0
        if _pt_seg_dist(mx, my, ex0, ey0, ex1, ey1) > TOL:
            continue
        if parallel_only and not _para(_seg_ang(sx0, sy0, sx1, sy1), eang):
            continue                       # 剔除窗的垂直封口线
        t0 = (sx0 - ex0) * dx + (sy0 - ey0) * dy
        t1 = (sx1 - ex0) * dx + (sy1 - ey0) * dy
        a, b = max(min(t0, t1), -CLAMP), min(max(t0, t1), L + CLAMP)
        if b - a > 1:
            ivs.append((a, b))
    ivs.sort()
    return ivs


def _merge(ivs):
    """≤MERGE_GAP 的相邻区间合并（同一洞口的平行重复线只算一次）。"""
    if not ivs:
        return []
    merged = []
    cs, ce = ivs[0]
    for a, b in ivs[1:]:
        if a <= ce + MERGE_GAP:
            ce = max(ce, b)
        else:
            merged.append((cs, ce))
            cs, ce = a, b
    merged.append((cs, ce))
    return merged


def _opening_width(segs, e, parallel_only):
    """洞口投影宽度合计（一维区间并集去重），上限 = 边长。与 wall_geometry 同口径。"""
    merged = _merge(_project_ivs(segs, e, parallel_only))
    if not merged:
        return 0.0
    return min(sum(b - a for a, b in merged), e[4])


def _opening_intervals(segs, e, parallel_only, min_w=MIN_W):
    """**逐樘**洞口区间 [(t0,t1)]（沿边参数，mm）：净宽 <min_w 的碎线丢弃。

    与 modeling/wall_geometry.opening_intervals 同判据：中点垂距 ≤TOL；
    窗（parallel_only）要求与墙边平行 ≤PAR_TOL；≤MERGE_GAP 的间隔合并。
    """
    out = []
    L = e[4]
    for s, t in _merge(_project_ivs(segs, e, parallel_only)):
        s2, t2 = max(0.0, s), min(L, t)
        if t2 - s2 >= min_w:
            out.append((s2, t2))
    return out


def _net_height(room, params):
    """净高：吊顶净高 > 参数/业态默认 > 2800（不得绕过业态默认直接 floor_height）。"""
    soffit = room.get("soffit_mm")
    if soffit:
        return float(soffit), "吊顶净高"
    eff = params.effective_floor_height_mm()
    if eff:
        return float(eff), "参数/业态默认"
    return FALLBACK_HEIGHT_MM, "兜底 2800"


def _room_name(room):
    names = room.get("names") or []
    return "+".join(names) or "(无标注)"


def rows_for_divisions(data, params):
    """1/2/3/4/5 分部出数。返回 (rows, issues)；issues 本轮恒为 []。"""
    rows = []
    issues: list[Issue] = []
    door_h = float(params.door_height_mm or 2100.0)
    win_h = float(params.window_height_mm or 1500.0)
    doors = data.get("doors") or []
    windows = data.get("windows") or []

    cand = []   # 物理洞口候选：(kind, cx, cy, w, comp_id)
    for room in data.get("rooms") or []:
        if room.get("is_out"):
            continue
        poly = room.get("poly") or []
        edges = _edges(poly)
        if not edges:
            continue                      # 无 poly 的室内房间不造假行
        name = _room_name(room)
        comp = room.get("comp_id")
        pos = "05平面围合 comp_id=%s" % comp
        rem_comp = "comp_id=%s;layer=围合面" % comp
        per = sum(e[4] for e in edges)
        h_net, h_src = _net_height(room, params)

        # ---- 1 地面 / 3 天棚：围合净面积（鞋带可复算） ----
        area = round(float(room.get("area") or 0.0), 2)
        rows.append(Row(
            1, "%s-地面" % name, "dxf_floor", "m2", area,
            "净面积=围合多边形鞋带公式 |Σ(x_i·y_(i+1)−x_(i+1)·y_i)|/2 = {:.2f}".format(area),
            pos, remarks=rem_comp))
        rows.append(Row(
            3, "%s-天棚刮腻子/涂料(基层)" % name, "dxf_ceiling", "m2", area,
            "天棚投影=同房间围合多边形 {:.2f}（校正 0：未见天花边界外挑/露明证据）".format(area),
            pos, remarks=rem_comp + ";correction=0(无天花边界证据)",
            confidence_level="medium"))

        # ---- 2 墙面：Σ边长×净高 − 门/窗洞口 ----
        door_w = min(sum(_opening_width(doors, e, False) for e in edges), per)
        win_w = min(sum(_opening_width(windows, e, True) for e in edges), per)
        gross = per * h_net / 1e6
        net = max(gross - door_w * door_h / 1e6 - win_w * win_h / 1e6, 0.0)
        rows.append(Row(
            2, "%s-墙面抹灰/腻子/涂料基层" % name, "dxf_wall", "m2", round(net, 2),
            "净墙=Σ边{:.2f}×净高{:.3f} − 门{:.2f}×{:.2f} − 窗{:.2f}×{:.2f}".format(
                per / 1e3, h_net / 1e3, door_w / 1e3, door_h / 1e3,
                win_w / 1e3, win_h / 1e3),
            pos, remarks=rem_comp + ";height=%s" % h_src,
            source_tag="几何推算",
            confidence_level="medium",
            check={"gross_m2": round(gross, 2), "openings_m2": round(gross - net, 2)}))

        # ---- 5 踢脚：净周长 − 门洞总宽（窗不落地） ----
        bb = max(per / 1e3 - door_w / 1e3, 0.0)
        if bb > 0:
            rows.append(Row(
                5, "%s-踢脚线" % name, "dxf_baseboard", "m", round(bb, 2),
                "踢脚=净周长{:.2f} − 门洞{:.2f}".format(per / 1e3, door_w / 1e3),
                pos, remarks=rem_comp, source_tag="几何推算"))

        # ---- 4 洞口候选：逐边取逐樘区间 ----
        for e in edges:
            x0, y0, x1, y1, L = e
            dx, dy = (x1 - x0) / L, (y1 - y0) / L
            for segs, kind, par in ((doors, "门", False), (windows, "窗", True)):
                for a, b in _opening_intervals(segs, e, parallel_only=par):
                    wx0, wy0 = x0 + dx * a, y0 + dy * a
                    wx1, wy1 = x0 + dx * b, y0 + dy * b
                    cand.append((kind, (wx0 + wx1) / 2, (wy0 + wy1) / 2, b - a, comp))

    # 共享墙会在相邻两间各命中一次：同类且中点距 <DEDUP 视为同一物理洞口
    uniq = []
    for c in cand:
        if not any(u[0] == c[0] and math.hypot(u[1] - c[1], u[2] - c[2]) < DEDUP
                   for u in uniq):
            uniq.append(c)
    order = {"门": 0, "窗": 1}
    uniq.sort(key=lambda c: (order.get(c[0], 9), round(c[2], 1), round(c[1], 1)))
    seq = {"门": 0, "窗": 0}
    for kind, cx, cy, width, comp in uniq:
        seq[kind] = seq.get(kind, 0) + 1
        hh = door_h if kind == "门" else win_h
        layer = "DOOR" if kind == "门" else "WINDOW"
        rows.append(Row(
            4, "%s洞-%d" % (kind, seq[kind]), "dxf_opening", "樘", 1,
            "洞口计数=1（{}洞 净宽{:.2f}m×高{:.2f}m 中点({:.0f},{:.0f})，物理洞口去重后计 1 樘）".format(
                kind, width / 1e3, hh / 1e3, cx, cy),
            "05平面 %s comp_id=%s 中点=(%.0f,%.0f)" % (layer, comp, cx, cy),
            remarks="comp_id=%s;width_mm=%.0f;center=(%.0f,%.0f)" % (comp, width, cx, cy),
            source_tag="几何推算"))
    return rows, issues
