"""审计引擎（Phase 3）— 双向对账 + 历史模板漏项审计。

三路审计（与架构讨论一致）：
  1. 反向对账（清单→图纸/房间）：清单项回溯不到来源的为幽灵项——
     重复或幻觉。降置信度 + 标记，排除合计，留待人工（留痕不删除）。
     与正向覆盖度对账（coverage.py，图纸→清单）构成双向闭环。
  2. 横向对账（图与图）：同名房间跨图纸面积一致性检查。
     平立面门窗数交叉核对需 CAD 语义关联（图纸关联 Agent），Phase 4 后补全。
  3. 纵向审计（历史模板）：对照历史同类清单找漏项——
     只输出建议，不自动补入（审计只读不写原则）。

反向对账的可追溯性判定规则：
  - 有 drawing_id            → 来自图纸解析，可追溯
  - 分部 2/3/5/6 无 drawing_id → 按房间名前缀匹配已分区房间（几何推算链）
  - 分部 7/8   无 drawing_id → source_tag 须为 经验补全/逻辑推理/历史参照
  - 分部 1     无 drawing_id → 地面必须来自图纸，直接判幽灵
  - 其余                     → 幽灵项
"""
import logging
from collections import defaultdict

from app import db
from app.models import Measurement, HistoricalProject

logger = logging.getLogger(__name__)

# 无图纸来源时仍可接受的来源标签（经验补全类清单项的合法路径）
TRACEABLE_SOURCE_TAGS = {'经验补全', '逻辑推理', '历史参照', '待确认'}

# 横向对账：同名房间跨图面积偏差告警阈值
CROSS_AREA_DEVIATION = 0.30

# 纵向审计：漏项建议最多输出条数
HISTORICAL_MISSING_LIMIT = 50


def reverse_reconciliation(project) -> dict:
    """反向对账：清单→图纸/房间 逐项追溯，落空的项标记幽灵嫌疑。

    标记方式与幽灵房间一致（低置信 + 【幽灵嫌疑】备注），
    merge_boq 会自动排除其合计并单列，留待人工复核。
    """
    ms = Measurement.query.filter_by(project_id=project.id).all()
    kept_room_labels = {r.label for r in Measurement.query.filter_by(
        project_id=project.id, division_id=1).filter(
        Measurement.zone_id.isnot(None)).all() if r.label}

    ghosts = []
    for m in ms:
        if m.remarks and '幽灵嫌疑' in m.remarks:
            continue  # 房间级幽灵已在分区阶段标记，不重复处理
        if m.drawing_id:
            ok = True
        elif m.division_id in (2, 3, 5, 6):
            label = m.label or ''
            ok = any(label.startswith(rl + '-') for rl in kept_room_labels)
        elif m.division_id in (7, 8):
            ok = m.source_tag in TRACEABLE_SOURCE_TAGS
        else:
            ok = False  # 分部1无图纸来源 / 其他无来源分部

        if not ok:
            m.confidence_level = 'low'
            m.confidence_icon = '🔴'
            m.confidence = 0.3
            note = '【幽灵嫌疑·反向对账】清单项无法回溯到图纸或房间，疑似重复/幻觉，待人工复核'
            m.remarks = f"{m.remarks or ''}\n{note}".strip()
            ghosts.append(m)

    if ghosts:
        db.session.commit()
        logger.info("反向对账: 标记 %s 个幽灵清单项: %s",
                    len(ghosts), [g.label for g in ghosts])
    return {
        'ghost_count': len(ghosts),
        'ghosts': [{'id': g.id, 'label': g.label, 'division_id': g.division_id}
                   for g in ghosts],
    }


def cross_check_drawings(project) -> dict:
    """横向对账：同名房间跨图纸面积一致性（图片管线下的可行子集）。

    同名房间（如标准层多个客厅）面积应接近；偏差超阈值提示识别结果
    需人工核对（可能是轮廓误检或比例尺漂移）。幽灵嫌疑项不参与。
    """
    rooms = Measurement.query.filter_by(project_id=project.id, division_id=1).all()
    by_label = defaultdict(list)
    for r in rooms:
        if r.zone_id and r.label:
            by_label[r.label].append(r)

    warnings = []
    for label, group in by_label.items():
        if len(group) < 2:
            continue
        areas = [r.area or 0 for r in group]
        lo, hi = min(areas), max(areas)
        if lo > 0 and (hi - lo) / lo > CROSS_AREA_DEVIATION:
            warnings.append(
                f"⚠️ 同名房间「{label}」跨图面积偏差 {((hi - lo) / lo):.0%}"
                f"（{lo:.2f}~{hi:.2f}m²），请核对识别结果")
    return {'checked_labels': len(by_label), 'warnings': warnings}


def historical_audit(project) -> dict:
    """纵向审计：对照历史同类清单找漏项（只建议，不自动补入）。

    优先取挂在本项目下的历史清单，其次取同用户上传的。
    """
    from app.comparison import compare_with_historical

    hist = HistoricalProject.query.filter_by(project_id=project.id).first() \
        or HistoricalProject.query.filter_by(user_id=project.user_id).first()
    if not hist:
        return {'skipped': '未上传历史同类清单，跳过漏项审计（可在项目详情页上传后重跑）'}

    result = compare_with_historical(project.id, hist.id)
    if 'error' in result:
        return {'skipped': f"历史清单不可用: {result['error']}"}

    missing = result.get('missing_items', [])
    return {
        'historical': result.get('historical'),
        'missing_count': len(missing),
        'missing_items': [{'name': i['name'], 'division_id': i['division_id'],
                           'division_name': i['division_name'], 'reason': i['reason']}
                          for i in missing[:HISTORICAL_MISSING_LIMIT]],
        'stats_comparison': result.get('stats_comparison'),
        'note': '漏项仅为建议（历史参照），未自动补入清单；确认后可在历史对照页手动补入',
    }


def run_audits(project) -> dict:
    """三路审计总入口。"""
    rev = reverse_reconciliation(project)
    cross = cross_check_drawings(project)
    hist = historical_audit(project)
    return {'reverse': rev, 'cross': cross, 'historical': hist}
