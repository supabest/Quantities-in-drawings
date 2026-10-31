"""收图探针与图层角色判定。不改几何，只产出状态。"""
from __future__ import annotations

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
