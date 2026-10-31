"""按连通分量切出平面复制簇；四类信息共现（真墙/标签/门窗/反坎）打分选底图。

不读 LayerRoles.bounds：全局 bounds 会横跨镜像副本/别张图纸，簇范围一律由簇内
段端点重新求 min/max（不 pad），再交给下游当 scope_bounds 重取局部几何。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import layers
from .types import Issue

LINK_GAP_MM = 3000.0
# 定簇用的五类几何；wall_fin_axial 排除（227 有上千条装饰线噪声，会把簇连成一片）
CLUSTER_ROLES = ("wall_solid", "wall_fin_part", "door", "window", "curb")
# 图纸空间无主方向可言，wall_fin 不参与成簇也不计分
PAPER_ROLES = ("wall_solid", "door", "window", "curb")
BLOCK_DEPTH = 8


@dataclass
class PlanCluster:
    source: str
    bounds: tuple
    score: int = 0
    reasons: list = field(default_factory=list)
    drawings: list = field(default_factory=list)


def _bbox(s):
    return (min(s[0], s[2]), min(s[1], s[3]), max(s[0], s[2]), max(s[1], s[3]))


def _gap(a, b):
    """两包围盒的最短间距（相交或相切为 0）。"""
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def _components(boxes, gap=LINK_GAP_MM):
    """包围盒连通分量：网格哈希只比较相邻单元的已登记盒，避免 O(n²) 全配对。"""
    parent = list(range(len(boxes)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    cell = max(gap, 1.0)
    grid = {}
    for i, b in enumerate(boxes):
        cx0, cx1 = int(math.floor(b[0] / cell)), int(math.floor(b[2] / cell))
        cy0, cy1 = int(math.floor(b[1] / cell)), int(math.floor(b[3] / cell))
        seen = set()
        for cx in range(cx0 - 1, cx1 + 2):
            for cy in range(cy0 - 1, cy1 + 2):
                for j in grid.get((cx, cy), ()):
                    if j in seen:
                        continue
                    seen.add(j)
                    if _gap(b, boxes[j]) <= gap:
                        union(i, j)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                grid.setdefault((cx, cy), []).append(i)

    comps = {}
    for i in range(len(boxes)):
        comps.setdefault(find(i), []).append(i)
    return list(comps.values())


def _score(kinds, labels, bounds):
    """四类共现打分（上限 6）：墙+1、标签+2、门/窗/反坎各+1。"""
    counts = {}
    for k in kinds:
        counts[k] = counts.get(k, 0) + 1
    score = 0
    reasons = []
    if counts.get("wall_solid"):
        score += 1
        reasons.append("wall=%d" % counts["wall_solid"])
    n_label = sum(1 for (x, y, _t) in labels
                  if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3])
    if n_label:
        score += 2
        reasons.append("label=%d" % n_label)
    for role in ("door", "window", "curb"):
        if counts.get(role):
            score += 1
            reasons.append("%s=%d" % (role, counts[role]))
    return score, reasons


def _bounds_of(segs):
    xs = [v for s in segs for v in (s[0], s[2])]
    ys = [v for s in segs for v in (s[1], s[3])]
    return (min(xs), min(ys), max(xs), max(ys))


def _model_clusters(roles):
    segs, kinds = [], []
    for role in CLUSTER_ROLES:
        for s in roles.segments.get(role, []):
            segs.append(s)
            kinds.append(role)
    if not segs:
        return []
    out = []
    for idxs in _components([_bbox(segs[i]) for i in range(len(segs))]):
        comp = [segs[i] for i in idxs]
        bounds = _bounds_of(comp)
        score, reasons = _score([kinds[i] for i in idxs], roles.labels, bounds)
        out.append(PlanCluster("model", bounds, score, reasons, []))
    return out


def _paper_clusters(doc):
    out = []
    for layout in doc.layouts:
        if layout.name == "Model":
            continue
        segs, kinds, labels = [], [], []

        def rec(ents, depth):
            for e in ents:
                t = e.dxftype()
                if t in ("LINE", "LWPOLYLINE", "POLYLINE"):
                    role = layers.role_of(e.dxf.layer)
                    if role in PAPER_ROLES:
                        for s in layers.segs_of(e):
                            segs.append(s)
                            kinds.append(role)
                elif t in ("TEXT", "MTEXT"):
                    if layers.role_of(e.dxf.layer) == "room_label":
                        text = layers.decode_text((layers.text_of(e) or "").strip())
                        if text:
                            p = e.dxf.insert
                            labels.append((p.x, p.y, text))
                elif t == "INSERT" and depth < BLOCK_DEPTH:
                    try:
                        rec(list(e.virtual_entities()), depth + 1)
                    except Exception:
                        # 块不可解析（xref/循环引用/空块）：跳过该实例内容
                        pass

        rec(list(layout), 0)
        # 只有 VIEWPORT 的布局不成候选：view 只是引用，几何不在此布局内
        if not segs:
            continue
        bounds = _bounds_of(segs)
        score, reasons = _score(kinds, labels, bounds)
        out.append(PlanCluster("paper", bounds, score, reasons,
                               [layers.decode_text(layout.name)]))
    return out


def cluster(doc, roles) -> list:
    """模型空间连通分量簇 + 图纸空间布局簇（模型簇在前）。"""
    return _model_clusters(roles) + _paper_clusters(doc)


def pick_base(clusters, pinned=None):
    """只从 model 簇里按 score 取唯一最高；并列要求人工钉 Drawing.is_base。"""
    if not clusters:
        return None, [Issue("no_geometry", "error", "未读到任何墙几何")]
    if pinned is False:
        return None, [Issue("drawing_not_base", "info", "图纸被人工钉为非底图")]
    model = [c for c in clusters if c.source == "model"]
    paper = [c for c in clusters if c.source == "paper"]

    def detail(c):
        return {"bounds": c.bounds, "score": c.score,
                "reasons": c.reasons, "source": c.source}

    if not model:
        return None, [Issue("paper_base_unconsumable", "error",
                            "平面仅在图纸空间，当前取线只读模型空间，无法计量",
                            {"candidates": [detail(c) for c in paper]})]

    best = max(c.score for c in model)
    top = [c for c in model if c.score == best]
    if len(top) > 1:
        return None, [Issue("base_ambiguous", "warn",
                            "多簇得分并列，需人工钉 Drawing.is_base",
                            {"candidates": [detail(c) for c in top]})]

    base = top[0]
    issues = []
    if paper and max(c.score for c in paper) >= best:
        issues.append(Issue("paper_plan_not_consumable", "warn",
                            "图纸空间的平面得分不低于模型空间，当前未从布局取线",
                            {"paper": [detail(c) for c in paper]}))
    others = [c for c in clusters if c is not base]
    if others:
        issues.append(Issue("base_alternatives", "info",
                            "另有 %d 个平面复制簇未选为主源" % len(others),
                            {"chosen": detail(base),
                             "others": [detail(c) for c in others]}))
    return base, issues
