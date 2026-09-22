"""Phase 4.5 · 2D 拓扑预校验（§十一 三维辅助算量的"零成本三维"起手式）。

§11.3 五条拓扑规则中，可纯 2D 落地的三条：
  1. 门窗挂墙：分部 4 门窗中心到最近房间 bbox 边线距离 ≤ 容差（默认 0.15m）
     替代 bbox 邻近启发法，为 Phase 5d 真拓扑（Opening.wall_segment_id 外键）预留接口。
  2. 边界合理性：分部 1 房间 bbox 反算矩形可构造且非退化（w/h ≤ 0 判可疑）
     §11.3(5) "闭合性" 在 bbox-only 数据下的近似；升级到 contour 多边形是 Phase 5b。
  3. 面积守恒：同房间地面 ≈ 天棚（分部 1 vs 分部 3）；偏差 >5% 触发
     §11.3(3) 的 2D 版；真"体积守恒"需挤出，Phase 6 补完。

审计纪律：
  - 命中项 confidence_level → low / 🔴 / confidence=0.3
  - remarks 追加【2D拓扑嫌疑·Phase4.5】<原因>
  - 留痕不删除、排除合计（merge_boq 已过滤低置信幽灵嫌疑项）
  - 与反向对账（Phase 3）行为一致；本模块与 Phase 3/4 现有幽灵项互不覆盖
"""
import json
import logging
from collections import defaultdict

from app import db
from app.models import Measurement

logger = logging.getLogger(__name__)

# ── 阈值（架构文档 §12.2）──
DEFAULT_PIXELS_PER_METER = 100    # processing 模块同名常量兜底
DOOR_ON_WALL_TOL_M = 0.15         # 门窗中心到最近墙边的最大容差（米）
AREA_CONSERVATION_DEV_PCT = 5.0   # 地面 vs 天棚面积偏差告警阈值（百分比）
ROOM_BBOX_MIN_SIDE_PX = 1         # bbox 边长下限（像素），≤0 判退化

# 已在其他审计环节命中过的标记，避免本模块重复处理
_SKIP_IF_REMARKS_CONTAIN = ('幽灵嫌疑·反向对账', '幽灵嫌疑·', '2D拓扑嫌疑')


# ═════════════════════════════════════════════════════
# 内部工具
# ═════════════════════════════════════════════════════

def _parse_json_field(raw):
    """容错解析 Measurement.data_json。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _bbox_of(measurement):
    """从房间 data_json 提取 (x, y, w, h) px；无则返回 None。"""
    d = _parse_json_field(measurement.data_json)
    if not d:
        return None
    bbox = d.get('bbox')
    if not bbox or len(bbox) != 4:
        return None
    try:
        return tuple(int(v) for v in bbox)
    except (TypeError, ValueError):
        return None


def _center_of(measurement):
    """从门窗 data_json 提取 (cx, cy) px；无则返回 None。"""
    d = _parse_json_field(measurement.data_json)
    if not d:
        return None
    center = d.get('center_px')
    if not center or len(center) != 2:
        return None
    try:
        return (int(center[0]), int(center[1]))
    except (TypeError, ValueError):
        return None


def _dist_to_bbox_border_px(point, bbox):
    """点到矩形 bbox 最近边线的像素距离（点到 4 条线段的最小距离）。

    点在矩形内部：距离 = min(|px-x|, |px-(x+w)|, |py-y|, |py-(y+h)|)
    点在矩形外部：仍取该 min 值的绝对值——即 nearest border distance
    """
    x, y, w, h = bbox
    px, py = point
    dx = min(abs(px - x), abs(px - (x + w)))
    dy = min(abs(py - y), abs(py - (y + h)))
    return float(min(dx, dy))


def _already_flagged(measurement) -> bool:
    """已被其他审计路命中过的项，本轮跳过（避免重复标记与串改备注）。"""
    remarks = measurement.remarks or ''
    return any(tag in remarks for tag in _SKIP_IF_REMARKS_CONTAIN)


def _mark_suspect(measurement, reason: str):
    """命中项降级 + 备注；与反向对账保持一致的处理口径。"""
    measurement.confidence_level = 'low'
    measurement.confidence_icon = '🔴'
    measurement.confidence = 0.3
    note = f'【2D拓扑嫌疑·Phase4.5】{reason}'
    measurement.remarks = f"{measurement.remarks or ''}\n{note}".strip()


# ═════════════════════════════════════════════════════
# 三条检查规则
# ═════════════════════════════════════════════════════

def _check_doors_on_walls(live_rooms, all_doors, scale):
    """规则 1：门窗挂墙。返回命中列表，并对命中项就地降级。"""
    hits = []
    room_bboxes = [(r.label, _bbox_of(r)) for r in live_rooms]
    room_bboxes = [(lbl, bbox) for lbl, bbox in room_bboxes if bbox is not None]
    if not room_bboxes:
        return hits

    for d in all_doors:
        if _already_flagged(d):
            continue
        center = _center_of(d)
        if center is None:
            continue  # 旧数据或派生门窗，优雅跳过（Phase 5b 补真位置）
        dist_px = min(_dist_to_bbox_border_px(center, bbox) for _, bbox in room_bboxes)
        dist_m = dist_px / scale if scale else dist_px / DEFAULT_PIXELS_PER_METER
        if dist_m > DOOR_ON_WALL_TOL_M:
            _mark_suspect(
                d,
                f"门窗中心距最近墙 {dist_m:.2f}m > 容差 {DOOR_ON_WALL_TOL_M}m，"
                f"疑似误检或悬空，待人工复核"
            )
            hits.append({
                'm_id': d.id,
                'label': d.label,
                'dist_m': round(dist_m, 3),
                'tol_m': DOOR_ON_WALL_TOL_M,
            })
    return hits


def _check_room_bbox(live_rooms):
    """规则 2：房间 bbox 退化 / 缺失。"""
    hits = []
    for r in live_rooms:
        if _already_flagged(r):
            continue
        bbox = _bbox_of(r)
        if bbox is None:
            _mark_suspect(r, "房间无 bbox 数据，无法拓扑校验")
            hits.append({'m_id': r.id, 'label': r.label, 'reason': 'no_bbox'})
            continue
        _, _, w, h = bbox
        if w <= ROOM_BBOX_MIN_SIDE_PX or h <= ROOM_BBOX_MIN_SIDE_PX:
            _mark_suspect(r, f"房间 bbox 退化 (w={w}, h={h})")
            hits.append({'m_id': r.id, 'label': r.label, 'reason': 'degenerate'})
    return hits


def _check_area_conservation(live_rooms, ceilings):
    """规则 3：同房间地面 vs 天棚面积偏差 >5%。

    匹配：分部 3 天棚 label 以 "{房间名}-" 开头；同名房间存在多张天棚时跳过（避免歧义）。
    """
    hits = []
    ceilings_by_room = defaultdict(list)
    for c in ceilings:
        if not c.label or '-' not in c.label:
            continue
        prefix = c.label.split('-', 1)[0]
        ceilings_by_room[prefix].append(c)

    for r in live_rooms:
        candidates = ceilings_by_room.get(r.label or '', [])
        # 只在同名唯一时对比，多义场景交给 Phase 7 图纸关联消歧
        if len(candidates) != 1:
            continue
        c = candidates[0]
        if _already_flagged(c):
            continue
        floor_area = r.area or 0
        ceil_area = c.area or 0
        if floor_area <= 0:
            continue
        delta_pct = abs(ceil_area - floor_area) / floor_area * 100
        if delta_pct > AREA_CONSERVATION_DEV_PCT:
            _mark_suspect(
                c,
                f"天棚 {ceil_area}m² vs 地面 {floor_area}m² 偏差 {delta_pct:.1f}%"
                f"（>{AREA_CONSERVATION_DEV_PCT}%），违反 §11.3(3) 面积守恒"
            )
            hits.append({
                'room': r.label,
                'ceiling_id': c.id,
                'floor_m2': round(floor_area, 2),
                'ceiling_m2': round(ceil_area, 2),
                'delta_pct': round(delta_pct, 2),
            })
    return hits


# ═════════════════════════════════════════════════════
# 入口
# ═════════════════════════════════════════════════════

def topology_2d_audit(project) -> dict:
    """Phase 4.5 主入口。

    返回：{'hit_count': int,
           'door_not_on_wall': [...],
           'invalid_room_polygons': [...],
           'area_conservation_violation': [...]}
    """
    scale = project.scale or DEFAULT_PIXELS_PER_METER

    all_rooms = Measurement.query.filter_by(
        project_id=project.id, division_id=1).all()
    ceilings = Measurement.query.filter_by(
        project_id=project.id, division_id=3).all()
    doors = Measurement.query.filter_by(
        project_id=project.id, division_id=4).all()

    # 排除已在反向对账/前轮拓扑命中的房间，避免二次降级
    live_rooms = [r for r in all_rooms if not _already_flagged(r)]

    door_hits = _check_doors_on_walls(live_rooms, doors, scale)
    bbox_hits = _check_room_bbox(live_rooms)
    area_hits = _check_area_conservation(live_rooms, ceilings)

    changed = door_hits or bbox_hits or area_hits
    if changed:
        db.session.commit()
        logger.info(
            "Phase 4.5 拓扑预校验 project=%s：悬空门窗 %d, bbox 可疑 %d, 面积不守恒 %d",
            project.id, len(door_hits), len(bbox_hits), len(area_hits))

    return {
        'hit_count': len(door_hits) + len(bbox_hits) + len(area_hits),
        'door_not_on_wall': door_hits,
        'invalid_room_polygons': bbox_hits,
        'area_conservation_violation': area_hits,
    }
