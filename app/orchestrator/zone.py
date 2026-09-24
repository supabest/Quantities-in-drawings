"""区域划分（Phase 2）— 自动聚类 + 守恒校验 + 幽灵房间去重 + 公共墙规则。

分区原则（与架构讨论一致）：
  - 分区依据必须可解释：房间名关键词聚类，兜底"其他区"
  - 划分完必须过守恒校验：每个房间恰好归属一个区域，Σ区域面积=Σ有效房间面积
  - 幽灵房间（重复/外轮廓误判）先剔除再分区，剔除项留痕不删除

公共墙归属规则（Phase 2 落地）：
  - 墙面饰面按"使用侧"归属：每面墙的工程量挂在适用该面的房间上、随房间入区；
    隔墙双面各算一次，归各自的区——派生项按房间生成，天然满足，无需调量
  - 同一张图上分属不同区域且 bbox 相邻的房间，其分部2墙面项标记
    boundary_type='boundary'，归并报告单列（boundary_items），供审计复查
  - 门窗随所属房间入区（按 drawing_id + 房间名前缀匹配）

Phase 2.5 演进：
  - 分区依据升级为 楼层/轴线网格/图框（需 CAD 几何）
  - 公共墙按轴线中线切分（需 CAD 几何；当前为 bbox 相邻近似判定）
"""
import json
import logging
from collections import defaultdict

from sqlalchemy import inspect

from app import db
from app.models import Measurement

logger = logging.getLogger(__name__)

# ── 区域关键词规则（优先匹配靠前的区）──
ZONE_KEYWORDS = [
    ('zone_public',    '公区',    ['客厅', '餐厅', '大堂', '门厅', '玄关', '起居']),
    ('zone_bedroom',   '居住区',  ['主卧', '次卧', '卧室', '客房', '衣帽间', '书房']),
    ('zone_wet',       '厨卫区',  ['厨房', '卫生间', '浴室', '盥洗', '洗衣']),
    ('zone_auxiliary', '辅助区',  ['阳台', '走道', '走廊', '过道', '储藏', '楼梯间', '管井']),
]
ZONE_FALLBACK = ('zone_other', '其他区')

# 幽灵房间判定：同图同名房间，小房间面积 ∈ (50%, 95%) × 大房间面积
# （典型场景：外轮廓被误判为房间，或嵌套轮廓重复检出。
#   限定同图比较——不同楼层的标准层同名房间面积接近是正常现象，不互标）
GHOST_AREA_RATIO_RANGE = (0.5, 0.95)

# 跨区相邻判定容差（米）：覆盖常见墙厚（100~300mm）+ 轮廓检测误差
BOUNDARY_ADJACENCY_TOLERANCE_M = 0.35


# ── 列迁移（幂等，跨 SQLite / PostgreSQL）──
def ensure_zone_columns() -> None:
    """Measurement 增加 zone_id/boundary_type 列（已有则跳过）。

    Phase 5a：原用 SQLite 专属 `PRAGMA table_info`，PG 不支持。
    改用 SQLAlchemy inspect 探测列（双方言），ALTER TABLE ADD COLUMN 两库通用。
    """
    insp = inspect(db.engine)
    if 'measurements' not in insp.get_table_names():
        return
    cols = {c['name'] for c in insp.get_columns('measurements')}
    with db.engine.begin() as conn:
        if 'zone_id' not in cols:
            conn.exec_driver_sql('ALTER TABLE measurements ADD COLUMN zone_id VARCHAR(32)')
            logger.info("已添加 measurements.zone_id 列")
        if 'boundary_type' not in cols:
            conn.exec_driver_sql('ALTER TABLE measurements ADD COLUMN boundary_type VARCHAR(16)')
            logger.info("已添加 measurements.boundary_type 列")


# ── 幽灵房间去重 ──
def dedup_ghost_rooms(rooms: list) -> tuple:
    """识别疑似幽灵房间（重复/外轮廓误判）。

    策略：同一张图纸内的同名房间中，若小面积 ∈ (50%, 95%) × 大面积，
    小面积者标疑。不删除——降置信度 + 备注，留待人工复核（审计独立性原则）。

    Returns:
        (kept_rooms, ghost_rooms)
    """
    by_label = {}
    for r in rooms:
        # 限定同图比较：跨图同名同面积是标准层的常态，不是重复
        by_label.setdefault((r.drawing_id, r.label or '未命名'), []).append(r)

    ghosts = []
    for (drawing_id, label), group in by_label.items():
        if len(group) < 2:
            continue
        group_sorted = sorted(group, key=lambda r: r.area or 0, reverse=True)
        for i, small in enumerate(group_sorted[1:], start=1):
            big = group_sorted[0]
            s, b = small.area or 0, big.area or 0
            if b > 0 and GHOST_AREA_RATIO_RANGE[0] < s / b < GHOST_AREA_RATIO_RANGE[1]:
                small.confidence_level = 'low'
                small.confidence_icon = '🔴'
                small.confidence = 0.3
                note = f"【幽灵嫌疑】与同图同名房间({label}, {b}m²)面积接近，疑似重复/外轮廓误判，待人工复核"
                small.remarks = f"{small.remarks or ''}\n{note}".strip()
                ghosts.append(small)

    if ghosts:
        db.session.commit()
        logger.info("标记 %s 个幽灵嫌疑房间: %s", len(ghosts), [g.label for g in ghosts])
    return [r for r in rooms if r not in ghosts], ghosts


# ── 自动聚类分区 ──
def auto_cluster_zones(rooms: list) -> dict:
    """按房间名关键词聚类分区。

    Returns:
        {zone_id: {'name': 区域名, 'rooms': [Measurement]}}
    """
    zones = {zid: {'name': name, 'rooms': []} for zid, name, _ in ZONE_KEYWORDS}
    zones[ZONE_FALLBACK[0]] = {'name': ZONE_FALLBACK[1], 'rooms': []}

    for room in rooms:
        label = room.label or ''
        for zid, _, keywords in ZONE_KEYWORDS:
            if any(kw in label for kw in keywords):
                zones[zid]['rooms'].append(room)
                break
        else:
            zones[ZONE_FALLBACK[0]]['rooms'].append(room)

    # 空区不保留
    return {zid: z for zid, z in zones.items() if z['rooms']}


# ── 守恒校验（不过闸不执行）──
def validate_zones(zones: dict, kept_rooms: list, ghosts: list) -> dict:
    """分区守恒校验。

    检查项：
      1. 覆盖守恒：每个有效房间恰好归属一个区域（无遗漏、无重复）
      2. 面积守恒：Σ区域面积 == Σ有效房间面积
      3. 合理性：区域数量、最大区占比
    """
    warnings = []
    zone_rooms = [r for z in zones.values() for r in z['rooms']]
    kept_ids = {id(r) for r in kept_rooms}
    zone_ids = {id(r) for r in zone_rooms}

    # 1. 覆盖守恒
    if not kept_rooms:
        return {'ok': False, 'warnings': ['未检测到任何有效房间，无法分区（请检查图纸识别质量或比例尺）']}
    missing = [r.label for r in kept_rooms if id(r) not in zone_ids]
    if missing:
        return {'ok': False, 'warnings': [f'以下房间未分入任何区域: {missing}']}
    dup = len(zone_rooms) - len(zone_ids)
    if dup:
        return {'ok': False, 'warnings': [f'{dup} 个房间被重复分配到多个区域']}

    # 2. 面积守恒
    zone_area = sum(r.area or 0 for r in zone_rooms)
    kept_area = sum(r.area or 0 for r in kept_rooms)
    if abs(zone_area - kept_area) > 0.01:
        return {'ok': False,
                'warnings': [f'面积不守恒: 区域合计{zone_area:.2f} ≠ 有效房间合计{kept_area:.2f}']}

    # 3. 合理性
    if ghosts:
        warnings.append(f'{len(ghosts)} 个幽灵嫌疑房间已剔除，待人工复核')
    if len(zones) == 1:
        warnings.append('全部房间落入同一区域，区域并行退化为单区（可接受，但失去并行收益）')
    else:
        areas = {zid: sum(r.area or 0 for r in z['rooms']) for zid, z in zones.items()}
        biggest = max(areas.values())
        if zone_area > 0 and biggest / zone_area > 0.9:
            warnings.append(f'最大区域占比 {biggest / zone_area:.0%}，负载可能不均衡')

    return {'ok': True, 'warnings': warnings,
            'stats': {'zone_count': len(zones), 'total_area': round(zone_area, 2)}}


# ── 区域标记落库（房间 + 门窗）──
def assign_zone_tags(zones: dict) -> None:
    """把 zone_id / boundary_type 写回房间与门窗（分部4）。

    门窗归属规则：门窗挂在所属房间上、随房间入区——
    按 (drawing_id, 房间名前缀) 匹配，仅写入尚未分配的门窗。
    墙面/天棚/踢脚线等派生项由 ZoneWorker 生成时自行标记。
    """
    for zid, z in zones.items():
        for room in z['rooms']:
            room.zone_id = zid
            room.boundary_type = 'zone'
            base = room.label or ''
            if not base:
                continue
            doors = Measurement.query.filter(
                Measurement.project_id == room.project_id,
                Measurement.division_id == 4,
                Measurement.drawing_id == room.drawing_id,
                Measurement.zone_id.is_(None),
                Measurement.label.like(f'{base}-%'),
            ).all()
            for d in doors:
                d.zone_id = zid
                d.boundary_type = 'zone'
    db.session.commit()


# ── 公共墙规则：跨区相邻墙面标记 ──
def _bboxes_adjacent(a: tuple, b: tuple, tol: float) -> bool:
    """两个 bbox (x, y, w, h) 是否在容差内相邻/接触。"""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    gap_x = max(ax - (bx + bw), bx - (ax + aw))
    gap_y = max(ay - (by + bh), by - (ay + ah))
    return gap_x <= tol and gap_y <= tol


def mark_boundary_walls(project, scale: float) -> dict:
    """公共墙规则（Phase 2）—— 跨区相邻墙面的审计标记。

    归属原则：墙面饰面按"使用侧"归属，每面墙的工程量挂在适用该面的
    房间上、随房间入区（隔墙双面各算一次），因此不调整工程量。
    本函数只做标记：同一张图上分属不同区域且 bbox 相邻的房间，
    其分部2墙面项 boundary_type 置为 'boundary'——归并报告单列
    （merge_boq → boundary_items），供审计单独复查。

    Returns:
        {'marked': 标记的墙面项数, 'rooms': 涉及的房间数}
    """
    rooms = Measurement.query.filter(
        Measurement.project_id == project.id,
        Measurement.division_id == 1,
        Measurement.zone_id.isnot(None),
    ).all()

    # 按图纸分组，取 bbox（识别阶段写入 data_json）
    by_drawing = defaultdict(list)
    for r in rooms:
        bbox = None
        if r.data_json:
            try:
                bbox = json.loads(r.data_json).get('bbox')
            except (ValueError, TypeError, AttributeError):
                bbox = None
        by_drawing[r.drawing_id].append((r, bbox))

    tol = max(10.0, scale * BOUNDARY_ADJACENCY_TOLERANCE_M)
    room_by_id = {r.id: r for r, _ in by_drawing.get(None, [])}
    for entries in by_drawing.values():
        for r, _ in entries:
            room_by_id[r.id] = r

    boundary_room_ids = set()
    for drawing_id, entries in by_drawing.items():
        if drawing_id is None or len(entries) < 2:
            continue
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                ra, ba = entries[i]
                rb, bb = entries[j]
                if ba is None or bb is None or ra.zone_id == rb.zone_id:
                    continue
                if _bboxes_adjacent(ba, bb, tol):
                    boundary_room_ids.add(ra.id)
                    boundary_room_ids.add(rb.id)
                    logger.info("区界墙: %s(%s) ↔ %s(%s)",
                                ra.label, ra.zone_id, rb.label, rb.zone_id)

    marked = 0
    for rid in boundary_room_ids:
        room = room_by_id.get(rid)
        base = (room.label or '') if room else ''
        if not base:
            continue
        walls = Measurement.query.filter(
            Measurement.project_id == project.id,
            Measurement.division_id == 2,
            Measurement.label.like(f'{base}-%'),
        ).all()
        for w in walls:
            if w.boundary_type != 'boundary':
                w.boundary_type = 'boundary'
                marked += 1
    db.session.commit()

    if marked:
        logger.info("公共墙标记: %s 条墙面项（涉及 %s 个房间）", marked, len(boundary_room_ids))
    return {'marked': marked, 'rooms': len(boundary_room_ids)}
