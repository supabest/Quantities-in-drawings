"""ZoneWorker 与清单归并（Phase 2）。

ZoneWorker 原则：
  - 使用统一规则引擎（processing 既有函数），不自定义规则
  - 只算本区域房间，产出带 zone_id 的 Measurement
  - 一个区域失败不影响其他区域

归并原则：
  - 不是简单 sum：按 区域×分部 归并，每条可下钻
  - 幽灵嫌疑项不参与归并合计，单列待复核
"""
import logging

from app import db
from app.models import Measurement, Project
from app.processing import _generate_derived_measurements, _generate_hidden_items

from .concurrency import run_tasks

logger = logging.getLogger(__name__)


class ZoneWorker:
    """区域级 Worker — 负责一个区域的分部 2/3/5/6/7/8 计算。

    并发纪律：只接收纯数据（rooms 为 dict 快照）与 project_id 标量；
    ORM 对象在 worker 会话内按 ID 重新加载，禁止跨线程共享。
    """

    def __init__(self, zone_id: str, zone: dict, project_id: int,
                 floor_height: float, scale: float, app):
        self.zone_id = zone_id
        self.zone = zone
        self.project_id = project_id
        self.floor_height = floor_height
        self.scale = scale
        self.app = app

    def run(self) -> tuple:
        """返回 ({zone_id, item_count}, error_detail)。"""
        try:
            with self.app.app_context():
                project = db.session.get(Project, self.project_id)
                room_ids = [r['id'] for r in self.zone['rooms']]
                rooms = (Measurement.query.filter(Measurement.id.in_(room_ids)).all()
                         if room_ids else [])
                # Phase 5a 后无需写锁（PG 天然并发）
                derived = _generate_derived_measurements(
                    project, self.floor_height, self.scale, rooms=rooms)
                hidden = _generate_hidden_items(project, rooms=rooms)
                # 区域标记
                for m in derived + hidden:
                    if m.zone_id is None:
                        m.zone_id = self.zone_id
                        m.boundary_type = 'zone'
                db.session.commit()
            return {'zone_id': self.zone_id, 'item_count': len(derived) + len(hidden)}, None
        except Exception as exc:
            logger.exception("ZoneWorker %s 失败", self.zone_id)
            return None, f"{type(exc).__name__}: {exc}"


def run_zone_workers(zones: dict, project_id: int, floor_height: float, scale: float, app) -> dict:
    """并行执行所有区域 Worker。"""
    tasks = [(zid, z) for zid, z in zones.items()]

    def worker(task):
        zid, z = task
        return ZoneWorker(zid, z, project_id, floor_height, scale, app).run()

    results = run_tasks(tasks, worker, max_workers=4)
    zone_results, errors = {}, []
    for task, result, err in results:
        zid = task[0]
        if err is None:
            zone_results[zid] = result
        else:
            errors.append(f"区域 {zid}: {err}")
    return {'zones': zone_results, 'errors': errors}


def merge_boq(project) -> dict:
    """归并清单 — 区域×分部 聚合，幽灵嫌疑项单列，区界墙项单列。

    Returns:
        {
          'zones': [{zone_id, divisions: {分部号: 合计量}}, ...],
          'grand_totals': {分部号: 合计量},
          'ghost_suspects': [清单项...],   # 不参与合计，待人工复核
          'boundary_items': [区界墙面项],  # 公共墙（使用侧归属已入区），审计单独复查
        }
    """
    ms = Measurement.query.filter_by(project_id=project.id).all()

    def qty_of(m):
        return m.area or m.length or m.count or 0

    zones_agg, grand = {}, {}
    ghosts, boundary = [], []
    for m in ms:
        if m.confidence_level == 'low' and m.remarks and '幽灵嫌疑' in m.remarks:
            ghosts.append(m.to_dict())
            continue  # 幽灵嫌疑不进合计
        if m.boundary_type == 'boundary':
            boundary.append({
                'id': m.id, 'label': m.label, 'zone_id': m.zone_id,
                'area': m.area, 'unit': m.unit,
            })  # 区界墙项正常计入所属区合计，仅审计标记
        zid = m.zone_id or 'unzoned'
        zagg = zones_agg.setdefault(zid, {'zone_id': zid, 'divisions': {}})
        d = m.division_id
        zagg['divisions'][d] = round(zagg['divisions'].get(d, 0) + qty_of(m), 2)
        grand[d] = round(grand.get(d, 0) + qty_of(m), 2)

    return {
        'zones': sorted(zones_agg.values(), key=lambda z: z['zone_id']),
        'grand_totals': dict(sorted(grand.items())),
        'ghost_suspects': ghosts,
        'boundary_items': boundary,
    }
