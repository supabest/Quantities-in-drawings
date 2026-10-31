"""收图探针与图层角色判定。不改几何，只产出状态。"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import ezdxf

ROLE_LAYER_PATTERNS = {
    "wall_solid": re.compile(r"^(s[-_ ]?wall|wall|墙体|剪力墙)$", re.I),
    "wall_fin": re.compile(r"^(fin|饰面|造型|finish)$", re.I),
    "door": re.compile(r"(^door|门)", re.I),
    "window": re.compile(r"(window|窗)", re.I),
    "curb": re.compile(r"(curb|反坎|栏板|rail)", re.I),
    "room_label": re.compile(r"^(p[-_ ]?text|text|文字|房名|room[_-]?tag)$", re.I),
}
ROLE_ORDER = ["wall_solid", "wall_fin", "door", "window", "curb", "room_label"]
AUDIT_PREFIX = "$TD_AUDIT_GENERATED_"


def role_of(layer_name: str) -> str | None:
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
    viewport_count: int = 0
    layouts: list = field(default_factory=list)
    text_heights: list = field(default_factory=list)


def _block_prefix(name: str) -> str:
    name = name or ""
    if name.startswith("*U"):
        return "*U"
    if name.startswith("A$C"):
        return "A$C"
    if name.startswith("$TwtSys$"):
        return "$TwtSys$"
    if name.startswith("$equip_U$"):
        return "$equip_U$"
    return "其他"


def proxy_survey(doc) -> SurveyReport:
    rep = SurveyReport(dxf_version=doc.dxfversion or "",
                       codepage=doc.header.get("$DWGCODEPAGE", ""))
    counts = Counter()
    proxies = Counter()
    prefixes = Counter()
    heights = []

    def walk(entities):
        for e in entities:
            t = e.dxftype()
            counts[t] += 1
            if t == "ACAD_PROXY_ENTITY":
                proxies[e.dxf.layer] += 1
            elif t == "INSERT":
                prefixes[_block_prefix(e.dxf.name)] += 1
            elif t in ("TEXT", "MTEXT"):
                try:
                    heights.append(float(e.dxf.height))
                except Exception:
                    pass

    walk(doc.modelspace())
    for layout in doc.layouts:
        rep.layouts.append(layout.name)
    for layout in doc.layouts:
        for e in layout:
            if e.dxftype() == "VIEWPORT":
                rep.viewport_count += 1
    for name in (doc.blocks.block_names() if hasattr(doc.blocks, "block_names")
                 else [b.name for b in doc.blocks]):
        prefixes[_block_prefix(name)] += 1

    rep.entity_counts = dict(counts)
    rep.proxies_by_layer = dict(proxies)
    rep.block_prefix_counts = dict(prefixes)
    rep.text_heights = heights
    return rep
