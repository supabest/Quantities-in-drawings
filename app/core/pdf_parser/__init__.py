"""PDF 语义提取模块（Phase 5c）。

从装修施工图 PDF 提取：
- 矢量线段 / 闭合面多边形
- 文字语义（房间名、材料代号、尺寸标注）
- 洪水填充域（天花分区、地面分区）
- 引线追踪（标高符号 → 目标域归属）
- 跨页配准

坐标系：PDF pt（Y 向下），A3 1:100 打印时 1pt ≈ 0.3528mm。
"""
from app.core.pdf_parser.extractor import (
    PDFPage,
    segments,
    faces,
    raster,
)
from app.core.pdf_parser.semantics import (
    extract_text_spans,
    extract_text_anchors,
    extract_room_labels,
    extract_material_codes,
    extract_dimensions,
    extract_area_annotations,
    room_from_text,
    room_from_title,
    extract_leader_text,
    extract_page_title,
)
from app.core.pdf_parser.zones import (
    flood_zones,
    zone_polygons,
    point_in_zone,
    zone_of_point,
)
from app.core.pdf_parser.leaders import (
    trace_leaders,
)
from app.core.pdf_parser.registration import (
    register_pages,
)

__all__ = [
    "PDFPage",
    "segments",
    "faces",
    "raster",
    "extract_text_spans",
    "extract_text_anchors",
    "extract_room_labels",
    "extract_material_codes",
    "extract_dimensions",
    "extract_area_annotations",
    "room_from_text",
    "room_from_title",
    "extract_leader_text",
    "extract_page_title",
    "flood_zones",
    "zone_polygons",
    "point_in_zone",
    "zone_of_point",
    "trace_leaders",
    "register_pages",
]
