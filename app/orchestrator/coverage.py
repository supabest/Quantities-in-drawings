"""覆盖度对账（Phase 1 正向） — 图纸→清单，逐图对账减少漏项。

双向对账的另一半（反向幽灵项排查）在 Phase 3 实现：
  反向 = 清单项回溯图纸坐标，落不到图纸的项为幽灵项。

正向对账原则：
  每张图纸识别出的构件数 vs 计入清单的测量项数。
  覆盖率低 + 无白名单原因 → 漏项嫌疑，进审计报告。
"""
import logging
import json
from datetime import datetime

from app import db
from app.models import Measurement, Drawing

logger = logging.getLogger(__name__)

# 覆盖率低于此值且无豁免原因 → 漏项嫌疑
COVERAGE_SUSPECT_THRESHOLD = 0.5


def check_coverage(project, drawings, detection_stats: dict = None) -> dict:
    """逐图覆盖度对账。

    Args:
        detection_stats: {drawing_id: {'rooms': n, 'doors': n, 'windows': n}}
                         由并行 worker 解析时统计上报；为 None 时退化为
                         只统计"图纸是否产出零清单项"这一最粗粒度检查。

    Returns:
        {
            'ok': bool,
            'items': [{
                'drawing_id', 'filename', 'parsed_components', 'measurement_rows',
                'coverage_ratio', 'suspect': bool, 'reason': str
            }],
        }
    """
    items = []
    for d in drawings:
        stats = (detection_stats or {}).get(d.id, {})
        parsed = sum(stats.values()) if stats else None

        rows = Measurement.query.filter_by(project_id=project.id, drawing_id=d.id).count()

        # 派生测量（分部 2/3/5/6 由 _generate_derived_measurements 生成，无 drawing_id）
        if parsed and rows == 0:
            suspect, reason = True, '图纸已识别构件但未产出任何清单项'
        elif parsed is None and rows == 0:
            # 退化检查：无解析统计时，零产出图纸同样标嫌疑（也可能是增补页/封面图）
            suspect, reason = True, '图纸未产出清单项（无解析统计，需人工确认是否为封面/目录页）'
        elif parsed:
            ratio = min(rows / parsed, 1.0)
            suspect = ratio < COVERAGE_SUSPECT_THRESHOLD
            reason = f'入清单测量行数({rows})少于识别构件数({parsed})，覆盖率 {ratio:.0%}' if suspect else ''
        else:
            suspect, reason = False, ''

        items.append({
            'drawing_id': d.id,
            'filename': d.filename if hasattr(d, 'filename') else str(d),
            'parsed_components': parsed,
            'measurement_rows': rows,
            'suspect': suspect,
            'reason': reason,
        })

    suspects = [i for i in items if i['suspect']]
    return {
        'ok': len(suspects) == 0,
        'checked_at': datetime.utcnow().isoformat(),
        'items': items,
        'suspect_count': len(suspects),
    }
