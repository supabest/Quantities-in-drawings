"""MEP 算量能力包（M2-M4 封账逻辑沉淀）。

- registration: DXF 房间面 → PDF 页配准与房间归属索引
- counts:       图纸文字锚计数 / 控制回路注记解析 / 锚点去重
- leaders:      注记引线追踪 → 箭头端点房间归属（M5）
"""
from app.orchestrator.mep.counts import (  # noqa: F401
    circuit_items, dedup, expand_token, ld_labels, spans, vent_counts,
)
from app.orchestrator.mep.leaders import aggregate, attribute_rows, thin_segments, track  # noqa: F401
from app.orchestrator.mep.registration import RoomIndex, load_index  # noqa: F401
