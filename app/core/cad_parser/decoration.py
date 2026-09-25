"""装修平面面积提取器（Phase 5b · 装修支路 · 增量 5b-D2a）。

设计决策（见 docs/QUANTITY_TAKEOFF_MASTER_ARCHITECTURE.md §12.7.7）：
  - 装修清单 76% 是 m² 面积项（地面/天棚/墙柱面），核心能力是"从平面图层求净面积"。
  - **净面积必须用 shapely 布尔 union**，不能对多边形面积求和：同图层常含重叠 /
    嵌套多边形（吊顶主面+反吊+灯槽、地面分区交叠），求和会显著虚高
    （实测 floor naive=118㎡ vs union=92㎡）。
  - 单位：DXF 为 mm，坐标 /1000 转 m，面积即 m²。
  - 多视图：实测 floor / CEILING 各自落在单一视图带，天然隔离，暂不需按图框切分；
    若后续出现多视图重复，再复用 dxf_extractor.find_plan_frames。

实测验收（05 平面图 vs 227 户型清单）：
  floor  union 净面积 92.0 ㎡  vs BOQ 地面找平 93.21 ㎡  → 偏差 1.3%（进 <2% 线）
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from shapely.geometry import Polygon
from shapely.ops import unary_union

MM_PER_M = 1000.0
_MIN_ROOM_AREA_M2 = 1.0  # 房间级面积下限（滤掉节点/家具小多边形）


def _to_polygon(e) -> Optional[Polygon]:
    """闭合 LWPOLYLINE → shapely Polygon（mm→m，自动修复自交）。"""
    try:
        pts = [(p[0] / MM_PER_M, p[1] / MM_PER_M) for p in e.get_points('xy')]
    except Exception:
        return None
    if len(pts) < 3:
        return None
    poly = Polygon(pts)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or poly.area <= 0:
        return None
    return poly


def closed_polylines(doc, layer: str) -> List:
    """返回指定图层上的闭合 LWPOLYLINE 实体列表。"""
    msp = doc.modelspace()
    return [e for e in msp
            if e.dxftype() == 'LWPOLYLINE' and e.dxf.layer == layer and e.closed]


def room_polygons(doc, layer: str,
                  min_area_m2: float = _MIN_ROOM_AREA_M2
                  ) -> List[Tuple[float, Tuple[float, float]]]:
    """房间级多边形：返回 [(面积㎡, 质心(x,y)m), ...]，按面积降序。"""
    out = []
    for e in closed_polylines(doc, layer):
        poly = _to_polygon(e)
        if poly and poly.area >= min_area_m2:
            c = poly.centroid
            out.append((poly.area, (c.x, c.y)))
    out.sort(key=lambda t: t[0], reverse=True)
    return out


def net_area_by_layer(doc, layer: str) -> Dict[str, float]:
    """图层净面积（布尔 union，消除重叠/嵌套）。

    返回 {'naive': 求和面积, 'union': 净面积, 'count': 有效多边形数}。
    对账用 union，naive 仅供诊断重叠虚高程度。
    """
    polys = [p for p in (_to_polygon(e) for e in closed_polylines(doc, layer)) if p]
    if not polys:
        return {'naive': 0.0, 'union': 0.0, 'count': 0}
    merged = unary_union(polys)
    union_area = (merged.area if merged.geom_type == 'Polygon'
                  else sum(g.area for g in merged.geoms))
    return {
        'naive': round(sum(p.area for p in polys), 2),
        'union': round(union_area, 2),
        'count': len(polys),
    }
