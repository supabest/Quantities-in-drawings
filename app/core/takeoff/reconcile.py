"""两条判定带对账：墙地比 + 踢脚/地面比。

只写 row.check 与 Issue——**不新增任何数字行、不改 row.value**。
参考带与 app/comparison.py 的 REASONABLENESS_RANGES 同值（不 import 该模块，
避免把 ORM/Flask 拉进纯包）：

  · 墙地比 = Σ墙面(m²) / Σ地面(m²)，按项目业态取带；
  · 踢脚比 = Σ踢脚(m) / Σ地面(m²)，全业态同一带。

注意：计划伪代码把业态硬编码为 sample_flat，这里必须按 params.project_type 取带；
params=None 时按 sample_flat。

纯包：只 import .types。
"""
from __future__ import annotations

from .types import Issue

WALL_FLOOR_BANDS = {"sample_flat": (2.0, 3.0), "showroom": (2.5, 3.5)}
DEFAULT_WALL_FLOOR_BAND = (1.5, 4.0)
BASEBOARD_BAND = (0.6, 1.1)


def _total(rows, division_id):
    return sum(r.value or 0.0 for r in rows if r.division_id == division_id)


def _check_of(row):
    if row.check is None:
        row.check = {}
    return row.check


def _warn(code, ratio, band, project_type, label):
    lo, hi = band
    return Issue(
        code, "warn",
        "%s=%.2f，参考范围%.1f~%.1f（业态 %s），请核查"
        % (label, ratio, lo, hi, project_type),
        {"ratio": ratio, "band": [lo, hi], "project_type": project_type})


def apply(rows, params=None):
    """写 check + 返回越带 Issue；floor/踢脚为 0 时不判（比值无定义）。

    比值先 round 到 2 位，check、带判定、message、detail 全用同一舍入值——
    避免 3.004 显示成 3.00 却仍报越带的自相矛盾。
    """
    issues = []
    project_type = getattr(params, "project_type", None) or "sample_flat"
    floor = _total(rows, 1)
    wall = _total(rows, 2)
    baseboard = _total(rows, 5)

    if floor > 0 and wall > 0:
        ratio = round(wall / floor, 2)
        lo, hi = WALL_FLOOR_BANDS.get(project_type, DEFAULT_WALL_FLOOR_BAND)
        for row in rows:
            if row.division_id == 2:
                _check_of(row)["wall_floor_ratio"] = ratio
        if not (lo <= ratio <= hi):
            issues.append(_warn("wall_floor_ratio", ratio, (lo, hi),
                                project_type, "项目墙地比"))

    if floor > 0 and baseboard > 0:
        ratio = round(baseboard / floor, 2)
        lo, hi = BASEBOARD_BAND
        for row in rows:
            if row.division_id == 5:
                _check_of(row)["baseboard_ratio"] = ratio
        if not (lo <= ratio <= hi):
            issues.append(_warn("baseboard_ratio", ratio, (lo, hi),
                                project_type, "项目踢脚/地面比"))
    return issues
