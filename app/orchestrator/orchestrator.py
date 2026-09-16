"""Orchestrator — Phase 1: 全局定标 + 图纸级并行 + 覆盖度对账。

职责边界：
  - 不直接做几何计算（全部委托 processing 既有函数）
  - 不自定义算量规则（规则仍在 processing / 未来 QuantityRuleEngine）
  - 只负责：调度、定标闸门、并发容错、任务追踪、对账

Phase 2 演进方向：
  - 区域级并行（ZoneWorker），需先解决公共墙归属与分区守恒校验
  - parse/persist 分离：worker 只做纯 OpenCV 检测，DB 写回主线程，
    缩小临界区；数据库切 PostgreSQL 后可解除写锁
"""
import logging
import threading
import os
import json

from flask import current_app

from app import db
from app.models import Project, Drawing, Measurement
from app.processing import (
    _process_floor_plan,
    _process_elevation,
    _generate_derived_measurements,
    _generate_hidden_items,
)

from .calibration import calibrate_project
from .coverage import check_coverage
from .audit import run_audits
from .concurrency import run_tasks, DB_WRITE_LOCK, MAX_DRAWING_WORKERS
from .task_state import TaskTracker, TaskStatus
from .zone import (
    ensure_zone_columns,
    dedup_ghost_rooms,
    auto_cluster_zones,
    validate_zones,
    assign_zone_tags,
    mark_boundary_walls,
)
from .zone_worker import run_zone_workers, merge_boq

logger = logging.getLogger(__name__)


class Orchestrator:

    def __init__(self, app=None):
        self.app = app

    # ── 规模判断 ──
    @staticmethod
    def assess_scale(project) -> str:
        n = Drawing.query.filter_by(project_id=project.id).count()
        if n <= 3:
            return 'small'
        if n <= 15:
            return 'medium'
        return 'large'

    # ── 主入口 ──
    def run(self, project_id: int) -> dict:
        app = self.app or current_app._get_current_object()
        with app.app_context():
            return self._run(app, project_id)

    def _run(self, app, project_id: int) -> dict:
        project = Project.query.get_or_404(project_id)
        drawings = Drawing.query.filter_by(project_id=project.id).all()
        scale_assessment = self.assess_scale(project)
        tracker = TaskTracker(project_id)

        summary = {
            'project_id': project_id,
            'scale_assessment': scale_assessment,
            'drawing_count': len(drawings),
            'warnings': [],
            'errors': [],
        }

        # ── Step 0: 列迁移（须先于任何 DB 写入）──
        ensure_zone_columns()

        # ── Step 1: 全局定标闸门（不过闸不执行）──
        calib_task = tracker.create_task('calibration')
        calibration = calibrate_project(project, drawings)
        if not calibration['ok']:
            tracker.mark(calib_task, TaskStatus.FAILED, '; '.join(calibration['warnings']))
            summary['errors'].extend(calibration['warnings'])
            summary['status'] = 'rejected_by_calibration'
            return summary
        summary['warnings'].extend(calibration['warnings'])
        tracker.mark(calib_task, TaskStatus.COMPLETED, f"scale={calibration['scale']}")

        # ── Step 2: 断点续传恢复（有遗留任务则提示，Phase 1 仍全量重跑）──
        resumed = tracker.resume()
        if resumed:
            summary['warnings'].append(f'发现 {len(resumed)} 个遗留任务，本次全量重跑覆盖')

        # ── Step 3: 重跑前清空本项目旧测量数据 ──
        n_del = Measurement.query.filter_by(project_id=project_id).delete()
        db.session.commit()
        if n_del:
            logger.info("清除旧测量数据 %s 条 (project=%s)", n_del, project_id)

        # ── Step 4: 图纸级并行处理 ──
        # 并发纪律：worker 只拿整数 ID，绝不跨线程共享主会话 ORM 对象。
        # （worker 线程访问主会话的过期属性会触发刷新/autoflush，
        #   把主会话拖进未提交事务，SQLite 下所有 worker 写库撞死锁）
        parallel = scale_assessment != 'small'
        max_workers = MAX_DRAWING_WORKERS if parallel else 1
        detection_stats = {}
        drawing_names = {d.id: (d.original_name or d.filename) for d in drawings}
        drawing_ids = list(drawing_names.keys())
        db.session.commit()   # 结束主会话事务（对象保持可刷新），再放 worker

        def worker(drawing_id):
            return self._process_single_drawing(app, drawing_id, project_id, calibration['scale'])

        if parallel:
            task_results = run_tasks(drawing_ids, worker, max_workers=max_workers)
        else:
            task_results = []
            for did in drawing_ids:
                result, err = worker(did)
                task_results.append((did, result, err))

        # 收集结果 + 任务落库
        ok_count = 0
        for drawing_id, result, err in task_results:
            t = tracker.create_task('drawing_process', drawing_id=drawing_id)
            if err is None:
                tracker.mark(t, TaskStatus.COMPLETED, f"{result['category_counts']}")
                detection_stats[drawing_id] = result['category_counts']
                ok_count += 1
            else:
                tracker.mark(t, TaskStatus.FAILED, err)
                summary['errors'].append(f"图纸 {drawing_names.get(drawing_id, drawing_id)}: {err}")
        summary['drawings_processed'] = ok_count
        db.session.commit()   # 结束读事务，后续查询可见 worker 已提交数据

        # ── Step 5: 区域划分 + 区域级并行（Phase 2）──
        zone_task = tracker.create_task('zone_partition')

        all_rooms = Measurement.query.filter_by(project_id=project_id, division_id=1).all()
        kept_rooms, ghosts = dedup_ghost_rooms(all_rooms)
        zones = auto_cluster_zones(kept_rooms)
        validation = validate_zones(zones, kept_rooms, ghosts)
        summary['warnings'].extend(validation['warnings'])

        if not validation['ok']:
            # 守恒校验不过闸：降级为 Phase 1 串行全量计算，不出区域清单
            tracker.mark(zone_task, TaskStatus.FAILED, '; '.join(validation['warnings']))
            summary['errors'].extend(validation['warnings'])
            summary['status'] = 'completed_degraded'
            floor_height = project.default_floor_height or 2.80
            derived = _generate_derived_measurements(project, floor_height, calibration['scale'])
            hidden = _generate_hidden_items(project)
            db.session.commit()
            summary['derived_count'] = len(derived) + len(hidden)
            return summary

        assign_zone_tags(zones)

        # 区域数据转纯 dict 再进 worker；floor_height/project 亦取标量，
        # ZoneWorker 在自己会话内按 ID 重新加载，杜绝跨线程 ORM 共享
        zones_data = {
            zid: {'name': z['name'],
                  'rooms': [{'id': r.id, 'label': r.label or '', 'area': r.area,
                             'perimeter': r.perimeter} for r in z['rooms']]}
            for zid, z in zones.items()
        }
        floor_height = project.default_floor_height or 2.80
        db.session.commit()
        zone_exec = run_zone_workers(zones_data, project_id, floor_height, calibration['scale'], app)
        summary['errors'].extend(zone_exec['errors'])
        tracker.mark(
            zone_task,
            TaskStatus.COMPLETED if not zone_exec['errors'] else TaskStatus.PARTIAL,
            json.dumps(zone_exec['zones'], ensure_ascii=False, default=str),
        )
        summary['zones'] = validation.get('stats', {})
        summary['ghost_suspects'] = len(ghosts)

        # ── Step 5b: 公共墙标记（跨区相邻墙面 → boundary，归并报告单列供审计复查）──
        summary['boundary_walls'] = mark_boundary_walls(project, calibration['scale'])

        # ── Step 6: 覆盖度对账 ──
        cov_task = tracker.create_task('coverage')
        coverage = check_coverage(project, drawings, detection_stats)
        tracker.mark(
            cov_task,
            TaskStatus.COMPLETED if coverage['ok'] else TaskStatus.PARTIAL,
            f"suspects={coverage['suspect_count']}",
        )
        summary['coverage'] = coverage
        if not coverage['ok']:
            summary['warnings'].append(
                f"{coverage['suspect_count']} 张图纸存在漏项嫌疑，详见 coverage.items"
            )

        # ── Step 7: 三路审计（Phase 3）——反向对账/横向对账/历史漏项 ──
        # 反向对账的幽灵项标记在 merge 之前，保证合计口径一致（排除幽灵）
        audit_task = tracker.create_task('audit')
        audit = run_audits(project)
        tracker.mark(audit_task, TaskStatus.COMPLETED,
                     json.dumps(audit, ensure_ascii=False, default=str))
        summary['audit'] = audit
        if audit['reverse']['ghost_count']:
            summary['warnings'].append(
                f"{audit['reverse']['ghost_count']} 个清单项无法回溯图纸，已标记幽灵嫌疑待人工复核")
        summary['warnings'].extend(audit['cross']['warnings'])

        summary['status'] = 'completed'
        summary['boq_merged'] = merge_boq(project)
        db.session.commit()
        return summary

    # ── 单图纸处理（worker 内执行，独立 app context）──
    def _process_single_drawing(self, app, drawing_id, project_id, scale) -> tuple:
        """返回 ({'category_counts': {...}}, None) 或 (None, error_detail)。

        只接收整数 ID；ORM 对象一律在 worker 会话内重新加载，
        避免跨线程访问主会话对象（过期刷新会污染主会话事务）。
        """
        try:
            with app.app_context():
                drawing = db.session.get(Drawing, drawing_id)
                project = db.session.get(Project, project_id)
                if drawing is None or project is None:
                    return {'category_counts': {}, 'skipped': 'drawing/project not found'}, None

                image_path = os.path.join(app.config['UPLOAD_FOLDER'], drawing.filename)
                if not os.path.exists(image_path):
                    return {'category_counts': {}, 'skipped': 'file not found'}, None

                dtype = drawing.drawing_type or 'floor_plan'
                # SQLite 写串行化：DB 写锁内执行完整处理
                with DB_WRITE_LOCK:
                    if dtype == 'elevation':
                        ms = _process_elevation(image_path, drawing, project, scale)
                    else:
                        ms = _process_floor_plan(image_path, drawing, project, scale)

                    counts = {}
                    for m in ms:
                        key = m.category or 'other'
                        counts[key] = counts.get(key, 0) + 1

                    db.session.commit()

                return {'category_counts': counts}, None
        except Exception as exc:
            logger.exception("处理图纸失败 drawing_id=%s", drawing_id)
            return None, f"{type(exc).__name__}: {exc}"
