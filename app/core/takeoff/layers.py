"""收图探针与图层角色判定。不改几何，只产出状态。"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

# 显式层名覆盖：先于正则顺序求值，key 为小写全名，值为角色。
# 只收整名精确命中，不做子串/正则，避免把别张图纸的同名通用层一并改判。
ROLE_OVERRIDES = {
    # 227/天正构件层：AX-COMP-160$0$A-WINDOW 实为阳台反坎/栏杆
    # （CURB/RAIL 层为空，该层承载 16 条阳台反坎 LWPOLYLINE）
    "ax-comp-160$0$a-window": "curb",
}

ROLE_LAYER_PATTERNS = {
    "wall_solid": re.compile(r"(^(s[-_ ]?wall|墙体|剪力墙)$|(^|[-_$ ])s?[-_ ]?wall$)", re.I),
    "wall_fin": re.compile(r"^(fin|饰面|造型|finish)$", re.I),
    "door": re.compile(r"(^door|门)", re.I),
    "window": re.compile(r"(window|窗)", re.I),
    "curb": re.compile(r"(curb|反坎|栏板|rail)", re.I),
    "room_label": re.compile(r"^(p[-_ ]?text|text|文字|房名|room[_-]?tag)$", re.I),
}
ROLE_ORDER = ["wall_solid", "wall_fin", "door", "window", "curb", "room_label"]
AUDIT_PREFIX = "$TD_AUDIT_GENERATED_"


def role_of(layer_name: str) -> str | None:
    override = ROLE_OVERRIDES.get((layer_name or "").lower())
    if override:
        return override
    for role in ROLE_ORDER:
        if ROLE_LAYER_PATTERNS[role].search(layer_name or ""):
            return role
    return None


def affected_divisions(broken_roles: set) -> set:
    divs = set()
    if "wall_solid" in broken_roles:
        divs |= {1, 2, 3, 5}
    if broken_roles & {"door", "window"}:
        divs |= {2, 4}
    if "curb" in broken_roles:
        divs |= {1, 3}
    return divs


@dataclass
class SurveyReport:
    dxf_version: str = ""
    codepage: str = ""
    entity_counts: dict = field(default_factory=dict)
    proxies_by_layer: dict = field(default_factory=dict)
    block_prefix_counts: dict = field(default_factory=dict)
    insert_prefix_counts: dict = field(default_factory=dict)
    viewport_count: int = 0
    layouts: list = field(default_factory=list)
    text_heights: list = field(default_factory=list)


def _block_prefix(name: str) -> str:
    upper = (name or "").upper()
    if upper.startswith("*U"):
        return "*U"
    if upper.startswith("A$C"):
        return "A$C"
    if upper.startswith("$TWTSYS$"):
        return "$TwtSys$"
    if upper.startswith("$EQUIP_U$"):
        return "$equip_U$"
    return "其他"


def proxy_survey(doc) -> SurveyReport:
    rep = SurveyReport(dxf_version=doc.dxfversion or "",
                       codepage=doc.header.get("$DWGCODEPAGE", ""))
    counts = Counter()
    proxies = Counter()
    block_prefixes = Counter()
    insert_prefixes = Counter()
    heights = []

    def walk(entities):
        for e in entities:
            t = e.dxftype()
            counts[t] += 1
            if t == "ACAD_PROXY_ENTITY":
                proxies[e.dxf.layer] += 1
            elif t == "INSERT":
                insert_prefixes[_block_prefix(e.dxf.name)] += 1
                # 块定义内的 proxy 一层展开探测；虚拟实体不计入 entity_counts，
                # 避免同一份几何按实例数重复放大。
                try:
                    for v in e.virtual_entities():
                        if v.dxftype() == "ACAD_PROXY_ENTITY":
                            proxies[v.dxf.layer] += 1
                except Exception:
                    # 块不可解析（xref/循环引用/空块）：跳过该实例内容
                    pass
            elif t in ("TEXT", "MTEXT"):
                try:
                    h = e.dxf.char_height if t == "MTEXT" else e.dxf.height
                    heights.append(float(h))
                except (AttributeError, TypeError, ValueError):
                    pass

    walk(doc.modelspace())
    for layout in doc.layouts:
        rep.layouts.append(layout.name)
    for layout in doc.layouts:
        if layout.name != "Model":
            walk(layout)  # Model 已由 modelspace() 走过，不重复计数
        for e in layout:
            if e.dxftype() == "VIEWPORT":
                rep.viewport_count += 1
    for name in (doc.blocks.block_names() if hasattr(doc.blocks, "block_names")
                 else [b.name for b in doc.blocks]):
        block_prefixes[_block_prefix(name)] += 1

    rep.entity_counts = dict(counts)
    rep.proxies_by_layer = dict(proxies)
    rep.block_prefix_counts = dict(block_prefixes)
    rep.insert_prefix_counts = dict(insert_prefixes)
    rep.text_heights = heights
    return rep


# ---------------------------------------------------------------- 角色取线
# 与 modeling/base_extract.py 的取线判据保持一致（227 基准对账口径）。
DECODE_RE = re.compile(r"\\M\+5([0-9A-Fa-f]{4})")


def decode_text(s: str) -> str:
    """还原 \\M+5xxxx 形式的 GBK 双字节转义（天正/理正文字常见）。"""
    return DECODE_RE.sub(lambda m: bytes.fromhex(m.group(1)).decode("gbk", "replace"), s or "")


def segs_of(e) -> list:
    st = e.dxftype()
    out = []
    if st == "LINE":
        s, t = e.dxf.start, e.dxf.end
        out.append((s.x, s.y, t.x, t.y))
    elif st in ("LWPOLYLINE", "POLYLINE"):
        try:
            pts = [(p[0], p[1]) for p in e.get_points()]
            for a, b in zip(pts, pts[1:]):
                out.append((a[0], a[1], b[0], b[1]))
        except Exception:
            pass
    return out


def text_of(e):
    if e.dxftype() == "TEXT":
        return e.dxf.text
    if e.dxftype() == "MTEXT":
        try:
            return e.plain_text()
        except Exception:
            return e.text
    return None


def _ang(dx, dy) -> float:
    return math.degrees(math.atan2(dy, dx)) % 180


@dataclass
class LayerRoles:
    segments: dict
    labels: list
    used_layers: dict
    audit_fallback: list
    bounds: tuple


def _collect(msp, roles_by_layer):
    """深度遍历取几何段（块内 < 8 层），审计层单独归账备回退。"""
    by_role = {}
    audit = {}

    def rec(ents, depth):
        for e in ents:
            st = e.dxftype()
            if st in ("LINE", "LWPOLYLINE", "POLYLINE"):
                segs = segs_of(e)
                if not segs:
                    continue
                layer = e.dxf.layer
                role = roles_by_layer.get(layer)
                if role:
                    by_role.setdefault(role, []).extend(segs)
                elif layer.startswith(AUDIT_PREFIX):
                    audit.setdefault(layer, []).extend(segs)
            elif st == "INSERT" and depth < 8:
                try:
                    rec(list(e.virtual_entities()), depth + 1)
                except Exception:
                    # 块不可解析（xref/循环引用/空块）：跳过该实例内容
                    pass

    rec(msp, 0)
    return by_role, audit


def resolve_roles(doc, strict_enc=False) -> LayerRoles:
    """按图层角色取线：真墙/FIN 双线间墙/门窗/反坎/房间标签 + 审计层零命中回退。

    strict_enc 不改变取线结果：wall_fin_part（严格）与 wall_fin_axial（宽松）
    两套候选同时返回，由下游围合按精度选取（对应 base_extract 的 ENC 取舍）。
    """
    msp = doc.modelspace()
    roles_by_layer = {}
    used = {role: [] for role in ROLE_ORDER}
    for layer in doc.layers:
        name = layer.dxf.name
        role = role_of(name)
        if role:
            roles_by_layer[name] = role
            used[role].append(name)
    by_role, audit = _collect(msp, roles_by_layer)

    # 真墙层零命中 → 回退审计层（只补 wall_solid，不猜其它角色；227 实测审计层
    # 不参与正常算量，仅用于真墙层缺失的图纸）
    fallback = []
    if not by_role.get("wall_solid"):
        fallback = sorted(audit)
        for name in fallback:
            by_role.setdefault("wall_solid", []).extend(audit[name])

    # FIN 方向判定：只保留与真墙主方向平行/垂直的 FIN 线
    tdir = set()
    for a, b, c, d in by_role.get("wall_solid", []):
        if (c - a) or (d - b):
            tdir.add(round(_ang(c - a, d - b)))
    prim = []
    for a in sorted(tdir):
        if not any(abs(a - x) <= 5 or abs(a - x) >= 175 for x in prim):
            prim.append(a)

    def qualifying(ax, ay, bx, by):
        if not ((bx - ax) or (by - ay)):
            return False
        fa = _ang(bx - ax, by - ay)
        for p in prim:
            d = min(abs(fa - p) % 180, 180 - (abs(fa - p) % 180))
            d2 = min(abs(fa - (p + 90)) % 180, 180 - (abs(fa - (p + 90)) % 180))
            if d <= 5 or d2 <= 5:
                return True
        return False

    fin = by_role.get("wall_fin", [])
    fin_axial = [s for s in fin if qualifying(*s)]

    def horizontal(s):
        return abs(s[3] - s[1]) <= abs(s[2] - s[0])

    FH = [s for s in fin_axial if horizontal(s)]
    FV = [s for s in fin_axial if not horizontal(s)]

    def partner(s, group, horiz):
        """100~250mm 内的平行同道线对 → 真隔墙候选。"""
        if horiz:
            a0, a1 = sorted((s[0], s[2]))
            fx = (s[1] + s[3]) / 2.0
        else:
            a0, a1 = sorted((s[1], s[3]))
            fx = (s[0] + s[2]) / 2.0
        for t in group:
            if t is s:
                continue
            if horiz:
                tf = (t[1] + t[3]) / 2.0
                b0, b1 = sorted((t[0], t[2]))
            else:
                tf = (t[0] + t[2]) / 2.0
                b0, b1 = sorted((t[1], t[3]))
            off = abs(tf - fx)
            if 100 <= off <= 250:
                ov = min(a1, b1) - max(a0, b0)
                ln = min(a1 - a0, b1 - b0)
                if ln > 0 and ov >= max(300.0, 0.5 * ln):
                    return True
        return False

    fin_part = ([s for s in FH if partner(s, FH, True)]
                + [s for s in FV if partner(s, FV, False)])

    labels = []

    def addtxt(e):
        if e.dxftype() not in ("TEXT", "MTEXT"):
            return
        if role_of(e.dxf.layer) != "room_label":
            return
        t = decode_text((text_of(e) or "").strip())
        # 与 base_extract.py:207 同口径：面积标注、"上/下" 索引、人数备注都不是房名
        if (not t or re.match(r"^[\d.]+\s*s\.m\.?$", t) or t in ("上", "下")
                or re.match(r"^\d+人$", t)):
            return
        p = e.dxf.insert
        labels.append((p.x, p.y, t))

    for e in msp:
        if e.dxftype() == "INSERT":
            try:
                for s in e.virtual_entities():
                    addtxt(s)
            except Exception:
                pass
        else:
            addtxt(e)

    pts = [p for segs in by_role.values() for s in segs for p in ((s[0], s[1]), (s[2], s[3]))]
    xs = [p[0] for p in pts] or [0.0]
    ys = [p[1] for p in pts] or [0.0]
    pad = 200.0
    bounds = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

    segments = {
        "wall_solid": by_role.get("wall_solid", []),
        "wall_fin_axial": fin_axial,
        "wall_fin_part": fin_part,
        "door": by_role.get("door", []),
        "window": by_role.get("window", []),
        "curb": by_role.get("curb", []),
    }
    return LayerRoles(segments=segments, labels=labels, used_layers=used,
                      audit_fallback=fallback, bounds=bounds)
