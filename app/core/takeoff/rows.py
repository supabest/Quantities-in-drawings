"""Row → Measurement。takeoff 包内唯一允许触碰 ORM 的适配层（惰性 import）。

模块级只 import json；app.models 必须放在 to_measurement 函数内，保证
``import app.core.takeoff.rows`` 本身不拉 Flask/ORM（纯包边界可自动断言）。

落库口径：
  · m2/m² → area + unit="m²"；m → length（unit 保持 "m"）；
    樘/个/台/套 → count（unit 原样）；
  · source_tag_icon / confidence_icon 按字典取，未知来源给空串（与
    processing.emit 同口径）、未知置信度用 medium 图标兜底，不抛；
  · row.check（墙面毛/洞口、对账比值）必须落 data_json['check']（spec §2.5）。
"""
from __future__ import annotations

import json

_COUNT_UNITS = ("樘", "个", "台", "套")


def to_measurement(row, project_id, drawing_id=None):
    """把一条 Row 映射成未落库的 Measurement 实例（不 commit、不开 session）。"""
    from app.models import CONFIDENCE_LEVELS, SOURCE_TAGS, Measurement

    confidence = CONFIDENCE_LEVELS.get(row.confidence_level) or CONFIDENCE_LEVELS["medium"]
    kwargs = dict(
        project_id=project_id,
        drawing_id=drawing_id,
        division_id=row.division_id,
        label=row.label,
        category=row.category,
        unit=row.unit,
        source_tag=row.source_tag,
        source_tag_icon=dict((t[0], t[1]) for t in SOURCE_TAGS).get(row.source_tag, ""),
        confidence_level=row.confidence_level,
        confidence_icon=confidence["icon"],
        formula_text=row.formula_text,
        position_desc=row.position_desc,
        remarks=row.remarks,
        project_features=row.project_features,
        material_code=row.material_code,
    )
    if row.unit in ("m2", "m²"):
        kwargs.update(area=row.value, unit="m²")
    elif row.unit == "m":
        kwargs.update(length=row.value)
    elif row.unit in _COUNT_UNITS:
        kwargs.update(count=int(row.value) if row.value is not None else None)
    else:
        raise ValueError("未知单位: %r" % (row.unit,))
    if row.check is not None:
        kwargs["data_json"] = json.dumps({"check": row.check}, ensure_ascii=False)
    return Measurement(**kwargs)
