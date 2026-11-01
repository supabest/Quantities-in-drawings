"""两条判定带对账：墙地比 + 踢脚/围合周长比。

只写 row.check 与 Issue——不新增任何数字行、不改 row.value。
带值唯一来源是 template.ranges（样板房取 templates/sample_flat.json，
未知业态走 template.BUILTIN_RANGES 兜底），本模块不保留硬编码区间。

踢脚分母是**参与踢脚的房间围合周长**（残条不计），不是地面面积：
227 实测 166.73 / 187.04 = 0.89。metrics 由 rows_for_divisions 汇总传入；
缺 perimeter 时跳过踢脚判定，不用错误分母凑结论。
"""
from __future__ import annotations

from . import template
from .types import Issue

_CHECK_DIV = {"wall_floor_ratio": 2, "baseboard_ratio": 5}
_LIGHT_TOKENS = ("灯",)
_SOCKET_TOKENS = ("插座", "开关")


def _get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _check_of(row):
    if row.check is None:
        row.check = {}
    return row.check


def measurement_view(m):
    """ORM Measurement → 纯 dict（只取判定需要的字段）。"""
    unit = (getattr(m, "unit", "") or "").strip()
    value = getattr(m, "area", None)
    if unit == "m":
        value = getattr(m, "length", None)
    elif unit not in ("m2", "m²"):
        value = getattr(m, "count", None)
    return {"division_id": getattr(m, "division_id", 0) or 0,
            "label": getattr(m, "label", "") or "",
            "category": getattr(m, "category", "") or "",
            "source_tag": getattr(m, "source_tag", "") or "",
            "unit": unit, "value": value}


def apply(rows, params=None, metrics=None):
    project_type = getattr(params, "project_type", None) or "sample_flat"
    results, issues = template.evaluate_ranges(
        template.totals(rows, metrics), project_type)
    by_code = {item["code"]: item for item in results}

    for row in rows:
        division = int(_get(row, "division_id", 0) or 0)
        for code, check_key in (("wall_floor_ratio", "wall_floor_ratio"),
                                ("baseboard_ratio", "baseboard_ratio")):
            if _CHECK_DIV[code] == division and code in by_code:
                _check_of(row)[check_key] = by_code[code]["value"]
        if division == 8:
            label = _get(row, "label", "") or ""
            if "light_per_area" in by_code and any(t in label for t in _LIGHT_TOKENS):
                _check_of(row)["light_per_area"] = by_code["light_per_area"]["value"]
            if "socket_per_area" in by_code and any(t in label for t in _SOCKET_TOKENS):
                _check_of(row)["socket_per_area"] = by_code["socket_per_area"]["value"]
    return issues
