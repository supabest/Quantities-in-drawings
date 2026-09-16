"""任务状态模型与追踪器 — 支持断点续传。

Phase 1 只做图纸级任务追踪；Phase 2 扩展为 区域×专业 任务矩阵。
"""
import logging
from enum import Enum
from datetime import datetime

from app import db

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    RETRYING = 'retrying'
    PARTIAL = 'partial'   # 部分成功（Worker 级容错）


class QuantityTask(db.Model):
    """算量任务持久化记录 — Orchestrator 断点续传的依据。"""
    __tablename__ = 'quantity_tasks'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    drawing_id = db.Column(db.Integer, db.ForeignKey('drawings.id'), index=True)

    task_type = db.Column(db.String(50), nullable=False)   # calibration / drawing_process / coverage / pricing_push
    status = db.Column(db.String(20), default=TaskStatus.PENDING.value, index=True)
    attempts = db.Column(db.Integer, default=0)
    detail = db.Column(db.Text)          # 失败原因 / 结果摘要（JSON）
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TaskTracker:
    """任务状态追踪器。

    原则：
    - 每个任务落库，失败只重跑该任务，不是全项目
    - run() 开始时自动恢复未完成任务
    """

    def __init__(self, project_id: int):
        self.project_id = project_id

    def create_task(self, task_type: str, drawing_id=None) -> QuantityTask:
        task = QuantityTask(
            project_id=self.project_id,
            drawing_id=drawing_id,
            task_type=task_type,
            status=TaskStatus.PENDING.value,
        )
        db.session.add(task)
        db.session.commit()
        return task

    def mark(self, task: QuantityTask, status: TaskStatus, detail: str = None):
        task.status = status.value
        task.attempts = (task.attempts or 0) + 1
        if detail is not None:
            task.detail = detail[:2000]
        db.session.commit()

    def resume(self) -> list:
        """恢复中断的任务，返回待重跑的 QuantityTask 列表。"""
        return QuantityTask.query.filter(
            QuantityTask.project_id == self.project_id,
            QuantityTask.status.in_([
                TaskStatus.PENDING.value,
                TaskStatus.RETRYING.value,
                TaskStatus.RUNNING.value,   # 上次异常中断遗留的 RUNNING 视为待重跑
            ]),
        ).all()

    def pending_counts(self) -> dict:
        rows = db.session.query(
            QuantityTask.status, db.func.count(QuantityTask.id)
        ).filter_by(project_id=self.project_id).group_by(QuantityTask.status).all()
        return {status: count for status, count in rows}
