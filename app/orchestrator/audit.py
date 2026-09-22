"""审计引擎（Phase 3/4/4.5）— 双向对账 + 历史模板漏项审计 + 异常规则 + 2D 拓扑预校验 + AI 综合审计。

六路审计（与架构讨论一致）：
  1. 反向对账（清单→图纸/房间）：清单项回溯不到来源的为幽灵项——
     重复或幻觉。降置信度 + 标记，排除合计，留待人工（留痕不删除）。
     与正向覆盖度对账（coverage.py，图纸→清单）构成双向闭环。
  2. 横向对账（图与图）：同名房间跨图纸面积一致性检查。
     平立面门窗数交叉核对需 CAD 语义关联（图纸关联 Agent），后续补全。
  3. 纵向审计（历史模板）：对照历史同类清单找漏项——
     只输出建议，不自动补入（审计只读不写原则）。
  4. 确定性异常规则（Phase 4）：异常尺寸 / 重复清单项 / 低置信度集中——
     纯阈值规则，不调 LLM。
  5. 2D 拓扑预校验（Phase 4.5）：门窗挂墙 / 房间 bbox 合理性 / 地面天棚面积守恒。
     §11.3 五条中可纯 2D 落地的三条；命中项降级 + 备注，为 Phase 5d 真拓扑建模验证接口。
  6. AI 综合审计（Phase 4）：LLM 汇总全部确定性发现，输出综合报告与复核建议。
     未配置 LLM_API_KEY 或预算耗尽时优雅降级；审计只读不写。

反向对账的可追溯性判定规则：
  - 有 drawing_id            → 来自图纸解析，可追溯
  - 分部 2/3/5/6 无 drawing_id → 按房间名前缀匹配已分区房间（几何推算链）
  - 分部 7/8   无 drawing_id → source_tag 须为 经验补全/逻辑推理/历史参照
  - 分部 1     无 drawing_id → 地面必须来自图纸，直接判幽灵
  - 其余                     → 幽灵项
"""
import json
import logging
from collections import defaultdict

from flask import current_app

from app import db
from app.models import Measurement, HistoricalProject

from .llm_client import call_llm, parse_json_response, LLMCallBudget
from .topology_2d import topology_2d_audit

logger = logging.getLogger(__name__)

# 无图纸来源时仍可接受的来源标签（经验补全类清单项的合法路径）
TRACEABLE_SOURCE_TAGS = {'经验补全', '逻辑推理', '历史参照', '待确认'}

# 横向对账：同名房间跨图面积偏差告警阈值
CROSS_AREA_DEVIATION = 0.30

# 纵向审计：漏项建议最多输出条数
HISTORICAL_MISSING_LIMIT = 50

# ── Phase 4: 确定性异常规则阈值 ──
DOOR_WIDTH_RANGE = (0.5, 1.5)      # 门宽常见范围（m）
DOOR_HEIGHT_RANGE = (1.8, 2.6)     # 门高常见范围（m）
ROOM_AREA_RANGE = (1.0, 300.0)     # 房间面积常见范围（m²）
LOW_CONF_RATIO_LIMIT = 0.30        # 低置信度项占比上限

# LLM 综合审计输入的清单样本上限（控制 token）
LLM_PROMPT_ITEM_LIMIT = 60


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
    """六路审计总入口（Phase 3 三路 + Phase 4 两路 + Phase 4.5 一路）。

    顺序：确定性审计先行（反向→横向→纵向→异常→拓扑），LLM 综合审计最后（看到全部确定性发现）。
    Phase 4.5 拓扑预校验放在最后一路确定性环节，因为它需要看到反向对账已标记的幽灵项（不重复处理）。
    """
    rev = reverse_reconciliation(project)
    cross = cross_check_drawings(project)
    hist = historical_audit(project)
    anomaly = anomaly_checks(project)
    topology = topology_2d_audit(project)
    llm = llm_audit(project, {'reverse': rev, 'cross': cross,
                              'historical': hist, 'anomaly': anomaly,
                              'topology_2d': topology})
    return {'reverse': rev, 'cross': cross, 'historical': hist,
            'anomaly': anomaly, 'topology_2d': topology, 'llm': llm}


# ════════════════════════════════════════════
# Phase 4a: 确定性异常规则（不调 LLM）
# ════════════════════════════════════════════

def anomaly_checks(project) -> dict:
    """确定性异常检测：异常尺寸 / 重复清单项 / 低置信度集中。

    纯阈值规则（架构文档 §8：AI 不可做的交给确定性算法）。
    幽灵嫌疑项不参与（它们已被单列待复核）。
    """
    ms = Measurement.query.filter_by(project_id=project.id).all()
    live = [m for m in ms if not (m.remarks and '幽灵嫌疑' in m.remarks)]
    findings = []

    # 1. 异常尺寸
    for m in live:
        if m.division_id == 4:
            if m.length and not (DOOR_WIDTH_RANGE[0] <= m.length <= DOOR_WIDTH_RANGE[1]):
                findings.append({
                    'type': 'anomaly_size', 'severity': 'warning',
                    'measurement_id': m.id,
                    'description': f"{m.label}: 门宽 {m.length}m 超出常见范围 "
                                   f"{DOOR_WIDTH_RANGE[0]}~{DOOR_WIDTH_RANGE[1]}m"})
            if m.height and not (DOOR_HEIGHT_RANGE[0] <= m.height <= DOOR_HEIGHT_RANGE[1]):
                findings.append({
                    'type': 'anomaly_size', 'severity': 'warning',
                    'measurement_id': m.id,
                    'description': f"{m.label}: 门高 {m.height}m 超出常见范围 "
                                   f"{DOOR_HEIGHT_RANGE[0]}~{DOOR_HEIGHT_RANGE[1]}m"})
        if m.division_id == 1 and m.area \
                and not (ROOM_AREA_RANGE[0] <= m.area <= ROOM_AREA_RANGE[1]):
            findings.append({
                'type': 'anomaly_size', 'severity': 'warning',
                'measurement_id': m.id,
                'description': f"{m.label}: 房间面积 {m.area}m² 超出常见范围 "
                               f"{ROOM_AREA_RANGE[0]}~{ROOM_AREA_RANGE[1]}m²"})
        if (m.count is not None and m.count <= 0) or (m.area is not None and m.area < 0):
            findings.append({
                'type': 'anomaly_size', 'severity': 'error',
                'measurement_id': m.id,
                'description': f"{m.label}: 数量/面积非法（count={m.count}, area={m.area}）"})

    # 2. 重复清单项（同分部 + 同名 + 同单位 + 同量，均非幽灵）
    seen = defaultdict(list)
    for m in live:
        qty = round(m.area or m.length or m.count or 0, 2)
        seen[(m.division_id, m.label, m.unit, qty)].append(m.id)
    for (div_id, label, unit, qty), ids in seen.items():
        if len(ids) > 1 and label:
            findings.append({
                'type': 'duplicate', 'severity': 'warning',
                'measurement_ids': ids,
                'description': f"疑似重复项: {label}（{unit}×{qty}）出现 {len(ids)} 次，"
                               f"请人工合并/剔除"})

    # 3. 低置信度集中
    low = [m for m in live if m.confidence_level == 'low']
    if live and len(low) / len(live) > LOW_CONF_RATIO_LIMIT:
        findings.append({
            'type': 'low_confidence_concentration', 'severity': 'warning',
            'description': f"低置信度项占比 {len(low) / len(live):.0%}"
                           f"（>{LOW_CONF_RATIO_LIMIT:.0%}），整体识别质量存疑，建议抽查原图"})

    return {'finding_count': len(findings), 'findings': findings}


# ════════════════════════════════════════════
# Phase 4b: AI 综合审计（LLM，复用套价系统 GLM 配置）
# ════════════════════════════════════════════

LLM_AUDIT_SYSTEM_PROMPT = (
    '你是资深装修算量审计师。基于给定的工程量清单摘要与确定性审计发现，'
    '输出综合审计意见。只依据给定数据判断，不要编造图纸上不存在的信息。'
    '严格输出 JSON（不要多余文字）：'
    '{"summary": "一段话总体评价", '
    '"risks": [{"item": "清单项或部位", "risk": "风险描述", "suggestion": "处理建议"}], '
    '"overall": {"verdict": "pass|review|reject", "advice": "下一步建议"}}'
)


def _division_summary(project_id: int) -> list:
    """分部级统计摘要（控制 token：只给聚合数 + 每分部样本项）。"""
    out = []
    ms = Measurement.query.filter_by(project_id=project_id).all()
    for div_id in sorted({m.division_id for m in ms}):
        items = [m for m in ms if m.division_id == div_id]
        out.append({
            'division_id': div_id,
            'item_count': len(items),
            'total': round(sum(m.area or m.length or m.count or 0 for m in items), 2),
            'sample_items': [{'label': m.label, 'qty': m.area or m.length or m.count,
                              'unit': m.unit, 'conf': m.confidence_level}
                             for m in items[:LLM_PROMPT_ITEM_LIMIT]],
        })
    return out


def build_llm_audit_prompt(project, deterministic: dict) -> tuple:
    """组装综合审计的 (system, user) prompt——独立函数便于测试。"""
    user_payload = {
        'project': {'id': project.id, 'name': project.name,
                    'type': project.project_type,
                    'floor_height': project.default_floor_height},
        'boq_divisions': _division_summary(project.id),
        'deterministic_findings': {
            'reverse_ghosts': deterministic.get('reverse', {}).get('ghost_count', 0),
            'cross_warnings': deterministic.get('cross', {}).get('warnings', []),
            'historical_missing': (deterministic.get('historical', {})
                                   .get('missing_items', []))[:20],
            'anomalies': (deterministic.get('anomaly', {}).get('findings', []))[:40],
            'topology_2d': {
                'hit_count': deterministic.get('topology_2d', {}).get('hit_count', 0),
                'door_not_on_wall': (deterministic.get('topology_2d', {})
                                     .get('door_not_on_wall', []))[:15],
                'invalid_room_polygons': (deterministic.get('topology_2d', {})
                                          .get('invalid_room_polygons', []))[:10],
                'area_conservation_violation': (deterministic.get('topology_2d', {})
                                                .get('area_conservation_violation', []))[:10],
            },
        },
        'note': '低置信度/幽灵项已被标记待人工；你只需综合判断与给出建议，不要复述全部数据',
    }
    return LLM_AUDIT_SYSTEM_PROMPT, json.dumps(user_payload, ensure_ascii=False)


def llm_audit(project, deterministic: dict, budget: LLMCallBudget = None) -> dict:
    """AI 总审计：汇总确定性发现 → 人类可读的综合报告与复核建议。

    未配置 LLM_API_KEY / 预算耗尽 / 调用失败 / 解析失败 → 优雅降级，
    确定性审计结果不受影响（审计只读不写原则）。
    """
    api_key = current_app.config.get('LLM_API_KEY') or ''
    if not api_key:
        return {'skipped': '未配置 LLM_API_KEY，跳过 AI 综合审计（确定性审计结果不受影响）'}
    budget = budget or LLMCallBudget(current_app.config.get('LLM_AUDIT_MAX_CALLS', 5))
    if not budget.allow():
        return {'skipped': 'LLM 调用预算耗尽，跳过 AI 综合审计'}

    system_prompt, user_prompt = build_llm_audit_prompt(project, deterministic)
    budget.count()
    try:
        raw = call_llm(system_prompt, user_prompt, api_key)
    except Exception as exc:
        logger.warning("AI 综合审计调用失败（已降级）: %s", exc)
        return {'error': f'LLM 调用失败（已降级）: {exc}', 'calls_used': budget.calls_made}

    parsed = parse_json_response(raw)
    if not parsed:
        return {'error': 'LLM 返回无法解析为 JSON（已降级）',
                'raw_excerpt': raw[:300], 'calls_used': budget.calls_made}
    return {'report': parsed, 'calls_used': budget.calls_made}
