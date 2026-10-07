"""MEP 引线追踪房间归属（M5 封账版 _m5b_leaders.py 逻辑沉淀）。

前提（M5 已证，见 outputs/model/mep_takeoff_m5.md §0）：
  P08/P09 与 P01 零平移同位置单平面，M2 标定直接复用（registration.load_index）。
  未落域锚点真因 = 注记列文字 bbox（宽 50-90pt，位于图外），需引线追踪而非配准。

线宽定律（P08 实测）：0.24=引线/细部、0.36=符号笔画、0.48=墙线——
  线段池只取 width==LEADER_WIDTH 的 'l' 线段，粗/细天然分离。

行走规则（方向感知，逐步迭代钉死）：
  种子  = 距标签 bbox 边缘 <=SEED_DRECT 的线段，取离 bbox 更远端起步
          （方向写反曾致全败——近端起步会立即出界）；
  直走  = 夹角 <=STRAIGHT_DA 且段长 >=STRAIGHT_MIN_LEN（括号母线行距短段
          不算直路，防沿母线滑移）；
  转弯  = 40°~120°、段长 >=TURN_MIN_LEN、且必须离锚更远；
  上限  = MAX_HOPS 跳 / MAX_TOTAL pt，端点=箭头落点。
归属判据：端点入房间面 + 墙装容差（RoomIndex.room_of）；链长 <MIN_CHAIN_LEN
  视为追踪失败，锚点原地落域挂"未能归属"——追不到如实挂账，不硬落域。
"""
import math

LEADER_WIDTH = 0.24          # 引线线宽（pt），P08 实测定律
WIDTH_TOL = 0.02             # 线宽匹配容差
SEED_DRECT = 12.0            # 种子：距 bbox 边缘阈值（pt）
GAP = 6.0                    # 端点接续间隙阈值（pt）
STRAIGHT_DA = math.radians(30)   # 直行夹角上限
STRAIGHT_MIN_LEN = 18.0          # 直行最小段长（母线短段过滤）
TURN_DA_MIN, TURN_DA_MAX = math.radians(40), math.radians(120)
TURN_MIN_LEN = 20.0              # 转弯最小段长
MAX_HOPS = 14                    # 行走跳数上限
MAX_TOTAL = 600.0                # 链总长上限（pt）
MIN_CHAIN_LEN = 25.0             # 有效追踪的最小链长（pt）
CELL = 20                        # 端点网格索引边长（pt）


def thin_segments(page, width=LEADER_WIDTH, tol=WIDTH_TOL):
    """页内引线段列表 [(x0,y0,x1,y1)]：仅指定线宽的 'l' 线段。"""
    segs = []
    for p in page.get_drawings():
        if abs((p.get("width") or 0) - width) > tol:
            continue
        for it in p["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                segs.append((a.x, a.y, b.x, b.y))
    return segs


def _drect(x, y, bb):
    """点到矩形 bbox 的边缘距离（矩形内为 0）。"""
    dx = max(bb[0] - x, 0, x - bb[2])
    dy = max(bb[1] - y, 0, y - bb[3])
    return math.hypot(dx, dy)


def _ang(s):
    return math.atan2(s[3] - s[1], s[2] - s[0])


def _len(s):
    return math.hypot(s[2] - s[0], s[3] - s[1])


def track(segs, bb):
    """从注记 bbox 沿引线行走 → (端点(x,y)|None, 链路径, 总长pt)。

    bb = (x0, y0, x1, y1) 标签文字包围盒（PDF pt）。返回 None 表示无种子。
    """
    ax, ay = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    d1 = [_drect(s[0], s[1], bb) for s in segs]
    d2 = [_drect(s[2], s[3], bb) for s in segs]
    cand = [i for i in range(len(segs)) if d1[i] <= SEED_DRECT or d2[i] <= SEED_DRECT]
    if not cand:
        return None, [], 0.0
    # 种子：远端点距 bbox 最大者；cur 取该线段离 bbox 更远的端点（指向图内）
    i = max(cand, key=lambda j: max(d1[j], d2[j]))
    s = segs[i]
    cur = (s[0], s[1]) if d1[i] > d2[i] else (s[2], s[3])
    path, used = [(ax, ay), cur], {i}
    cur_ang, total = _ang(s), _len(s)
    grid = {}
    for k, t in enumerate(segs):
        for e in ((t[0], t[1]), (t[2], t[3])):
            grid.setdefault((int(e[0] // CELL), int(e[1] // CELL)), []).append((k, e))
    for _ in range(MAX_HOPS):
        if total > MAX_TOTAL:
            break
        near, seen = [], set()
        gx, gy = int(cur[0] // CELL), int(cur[1] // CELL)
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                for k, e in grid.get((gx + ddx, gy + ddy), []):
                    if k in used or k in seen or math.hypot(e[0] - cur[0], e[1] - cur[1]) > GAP:
                        continue
                    seen.add(k)
                    t = segs[k]
                    o = (t[2], t[3]) if e == (t[0], t[1]) else (t[0], t[1])
                    if math.hypot(o[0] - cur[0], o[1] - cur[1]) > 1.0:
                        da = abs((_ang(t) - cur_ang + math.pi) % (2 * math.pi) - math.pi)
                        near.append((k, o, _ang(t), da))
        if not near:
            break
        d_cur = math.hypot(cur[0] - ax, cur[1] - ay)
        straight = [c for c in near if c[3] <= STRAIGHT_DA and _len(segs[c[0]]) >= STRAIGHT_MIN_LEN]
        if straight:
            k, o, a, _ = max(straight, key=lambda c: math.hypot(c[1][0] - cur[0], c[1][1] - cur[1]))
        else:
            fwd = [c for c in near
                   if TURN_DA_MIN <= c[3] <= TURN_DA_MAX
                   and math.hypot(c[1][0] - ax, c[1][1] - ay) > d_cur
                   and _len(segs[c[0]]) >= TURN_MIN_LEN]
            if not fwd:
                break
            k, o, a, _ = max(fwd, key=lambda c: math.hypot(c[1][0] - cur[0], c[1][1] - cur[1]))
        used.add(k)
        cur = o
        path.append(cur)
        total += _len(segs[k])
        cur_ang = a
    return cur, path, total


def attribute_rows(kinds_src, segs, room_of):
    """逐锚点引线归属：kinds_src → {kind: {"items":[[n,bbox],...]} | [[n,bbox],...]}。

    room_of: (x,y) → 房间名|None（通常传 RoomIndex.room_of）。
    权重只用 M2 的 n（n 已含 X2 倍率，勿重复乘）。
    返回 rows（与 M5 封账 m5_P{08,09}_rows.json 同构）。
    """
    rows, chains = [], []
    for kind, e in kinds_src.items():
        items = e["items"] if isinstance(e, dict) else e
        for n, bb in items:
            ax, ay = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            ep, path, L = track(segs, tuple(bb))
            if ep and L >= MIN_CHAIN_LEN:
                rm = room_of(*ep)
                rows.append({"kind": kind, "n": n, "anchor": [round(ax, 1), round(ay, 1)],
                             "endpoint": [round(ep[0], 1), round(ep[1], 1)], "len": round(L, 1),
                             "room": rm or "未能归属"})
                chains.append(path)
            else:
                rm = room_of(ax, ay)
                rows.append({"kind": kind, "n": n, "anchor": [round(ax, 1), round(ay, 1)],
                             "endpoint": None, "len": 0, "room": rm or "未能归属"})
    return rows, chains


def aggregate(rows):
    """rows → {"kind|room": Σn} 分布汇总。"""
    agg = {}
    for r in rows:
        key = f"{r['kind']}|{r['room']}"
        agg[key] = agg.get(key, 0) + r["n"]
    return agg


def main(pdf=None, art="outputs/preview/vlm/mep/"):
    """复现入口：P08/P09 全量引线归属，回写 m5_{tag}_rows.json（与封账比对）。"""
    import json
    import os
    import fitz
    from app.orchestrator.mep.registration import DEFAULT_PDF, load_index
    pdf = pdf or DEFAULT_PDF
    doc = fitz.open(pdf)
    index, _calib = load_index(os.path.join(art, "m2_attribution.json"))
    out = {}
    for pi, src, tag in (
            (18, json.load(open(os.path.join(art, "m2_counts_clean.json")))["18"], "P08"),
            (19, {k: [[1, bb] for bb in v] for k, v in
                  json.load(open(os.path.join(art, "m2_textcounts.json")))["19"]["pos"].items()}, "P09")):
        segs = thin_segments(doc[pi])
        rows, _ = attribute_rows(src, segs, index.room_of)
        n_ok = sum(1 for r in rows if r["endpoint"])
        print(f"{tag}: {len(rows)} 锚, 追踪成功 {n_ok}")
        json.dump(rows, open(os.path.join(art, f"m5_{tag}_rows.json.new"), "w"),
                  ensure_ascii=False, indent=1)
        out[tag] = {"n_anchors": len(rows), "n_tracked": n_ok, "dist": aggregate(rows)}
    return out


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.getcwd())
    main()
