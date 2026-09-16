"""Orchestrator API — 触发算量 / 查询进度 / 推送套价。

与既有 api.py 保持一致的鉴权方式（flask_login + 项目属主校验）。
"""
import logging
import threading

from flask import Blueprint, jsonify, current_app
from flask_login import login_required, current_user

from app import db
from app.models import Project, Measurement
from flask import abort

from .orchestrator import Orchestrator
from .pricing_client import PricingClient, PricingSystemError
from .task_state import TaskTracker
from .zone_worker import merge_boq

logger = logging.getLogger(__name__)

orchestrator_bp = Blueprint('orchestrator', __name__)


def _get_project_or_403(project_id: int) -> Project:
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return project


@orchestrator_bp.route('/projects/<int:project_id>/run', methods=['POST'])
@login_required
def run_takeoff(project_id: int):
    """触发 Phase 1 图算量（后台线程执行，前端轮询 status）。"""
    project = _get_project_or_403(project_id)

    app = current_app._get_current_object()
    orch = Orchestrator(app)

    def _background():
        try:
            with app.app_context():
                orch._run(app, project_id)
        except Exception:
            logger.exception("Orchestrator 运行失败 project=%s", project_id)

    threading.Thread(target=_background, daemon=True).start()
    return jsonify({'status': 'started', 'project_id': project.id})


@orchestrator_bp.route('/projects/<int:project_id>/status', methods=['GET'])
@login_required
def get_status(project_id: int):
    """任务进度查询（TaskTracker 持久化状态）。"""
    _get_project_or_403(project_id)
    tracker = TaskTracker(project_id)
    measurement_count = Measurement.query.filter_by(project_id=project_id).count()
    return jsonify({
        'project_id': project_id,
        'task_counts': tracker.pending_counts(),
        'measurement_count': measurement_count,
    })


@orchestrator_bp.route('/projects/<int:project_id>/zones', methods=['GET'])
@login_required
def get_zone_boq(project_id: int):
    """区域×分部归并清单（Phase 2），含幽灵嫌疑项。"""
    project = _get_project_or_403(project_id)
    return jsonify(merge_boq(project))


@orchestrator_bp.route('/projects/<int:project_id>/push-pricing', methods=['POST'])
@login_required
def push_pricing(project_id: int):
    """将本项目清单推送套价系统进行 AI 批量套价。

    Body: {'project_id': <套价系统项目ID>, 'mode': 'price'|'import'}
      - price  (默认): 调 /api/v1/pricing/batch，返回定额匹配结果
      - import: 调 /api/v1/bill-items 正式落库（需套价系统启用该路由）
    """
    from flask import request
    project = _get_project_or_403(project_id)
    payload = request.get_json(silent=True) or {}
    pricing_project_id = payload.get('project_id')
    mode = payload.get('mode', 'price')

    if mode == 'import' and not pricing_project_id:
        return jsonify({'error': 'import 模式需要提供套价系统 project_id'}), 400

    measurements = Measurement.query.filter_by(project_id=project_id).all()
    if not measurements:
        return jsonify({'error': '项目暂无算量结果，请先运行 /run'}), 400

    client = PricingClient(
        base_url=current_app.config.get('PRICING_API_BASE_URL', ''),
        api_key=current_app.config.get('PRICING_API_KEY', ''),
    )
    try:
        if mode == 'import':
            result = client.import_bill(pricing_project_id, measurements)
        else:
            result = client.price_batch(measurements)
    except PricingSystemError as exc:
        return jsonify({'error': str(exc)}), 502

    return jsonify({'status': 'ok', 'item_count': len(measurements), 'pricing_result': result})
