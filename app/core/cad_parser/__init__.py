"""DXF 矢量提取（Phase 5b）。"""
from .dxf_extractor import (
    count_blocks, measure_wire_lengths, layer_length_units,
    find_plan_frames, region_of,
    count_blocks_by_region, measure_wire_lengths_by_region,
)
from .textcodec import decode_acad_text, has_cjk
from .decoration import net_area_by_layer, room_polygons, closed_polylines
