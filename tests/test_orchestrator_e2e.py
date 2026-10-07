# -*- coding: utf-8 -*-
"""端到端验证 Phase 1（图纸级并行+定标+对账）与 Phase 2（区域并行+守恒+归并）。"""
import os, sys, time, shutil, traceback, json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)

import numpy as np
import cv2

TMP = '/tmp/qty_e2e'
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP + '/uploads', exist_ok=True)

# ── 独立配置：临时 DB + 临时上传目录 ──
# Phase 5a: 支持 PG（设 TEST_DATABASE_URL）；未设时回退 SQLite，保持无依赖可跑
import config as _cfg
_PG_URL = os.environ.get('TEST_DATABASE_URL')

class TestConfig(_cfg.Config):
    if _PG_URL:
        SQLALCHEMY_DATABASE_URI = _PG_URL
        SQLALCHEMY_ENGINE_OPTIONS = _cfg._default_engine_options(_PG_URL)
    else:
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + TMP + '/test.db'
        SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'timeout': 30}}
    UPLOAD_FOLDER = TMP + '/uploads'
    REPORTS_FOLDER = TMP + '/reports'
    # 安全修复后 admin 密码走配置通道（无默认值）：测试显式钉一个已知密码供登录冒烟
    ADMIN_PASSWORD = 'admin123456'

from app import create_app, db
from app.models import User, Project, Drawing, Measurement, HistoricalProject
from app.orchestrator import Orchestrator
from app.orchestrator.task_state import QuantityTask

app = create_app(TestConfig)

WALL = 8  # 墙厚 px

def ring(img, x1, y1, x2, y2):
    cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 0), WALL)

def make_plan_a(path):
    img = np.full((620, 1600, 3), 255, np.uint8)   # 客厅27m² 主卧16m² 卫生间3m² @100px/m
    ring(img, 40, 40, 580, 580)      # 客厅 540x540
    ring(img, 600, 40, 1020, 460)    # 主卧 420x420（与客厅墙间距20px → 跨区相邻触发公共墙标记）
    ring(img, 1120, 40, 1330, 220)   # 卫生间 210x180
    cv2.line(img, (300, 300), (400, 300), (0, 0, 0), 2)  # 客厅内 1m 线段 → 门窗检出
    cv2.imwrite(path, img)

def make_plan_b(path):
    img = np.full((420, 800, 3), 255, np.uint8)    # 次卧9.6m² 厨房6.3m²
    ring(img, 40, 40, 380, 360)      # 次卧 340x320
    ring(img, 440, 40, 770, 230)     # 厨房 330x190
    cv2.imwrite(path, img)

def make_elevation(path):
    img = np.full((280, 600, 3), 255, np.uint8)
    cv2.line(img, (30, 250), (570, 250), (0, 0, 0), 4)
    cv2.line(img, (30, 30), (570, 30), (0, 0, 0), 4)
    cv2.imwrite(path, img)

make_plan_a(TMP + '/uploads/A1F.png')
make_plan_a(TMP + '/uploads/B1F.png')   # 第二层同户型 → 触发 medium 级并行（4 图纸）
make_plan_b(TMP + '/uploads/A2F.png')
make_elevation(TMP + '/uploads/S.png')

with app.app_context():
    db.create_all()
    # Phase 5a: PG 下需清理上一次测试遗留数据（SQLite 因为 shutil.rmtree 无此问题）
    if _PG_URL:
        from sqlalchemy import text as _t
        for tbl in ['measurements', 'reports', 'self_check_items',
                    'quantity_tasks', 'drawings', 'projects', 'historical_projects']:
            db.session.execute(_t(f'TRUNCATE TABLE {tbl} RESTART IDENTITY CASCADE'))
        db.session.commit()
    admin = User.query.filter_by(is_admin=True).first()

    # 项目1：未设比例尺 → 期望被定标闸门拒绝
    p1 = Project(name='无比例尺项目', user_id=admin.id)
    db.session.add(p1); db.session.flush()
    db.session.add(Drawing(filename='A1F.png', original_name='A1F.png', file_type='png', project_id=p1.id))

    # 项目2：scale=100 → 完整流程
    p2 = Project(name='标准项目', scale=100, default_floor_height=2.8, user_id=admin.id)
    db.session.add(p2); db.session.flush()
    for fn, name, dt in [('A1F.png', 'A栋一层平面.png', 'floor_plan'),
                         ('B1F.png', 'B栋一层平面.png', 'floor_plan'),
                         ('A2F.png', 'A栋二层平面.png', 'floor_plan'),
                         ('S.png', '南立面.png', 'elevation')]:
        db.session.add(Drawing(filename=fn, original_name=name, file_type='png',
                               drawing_type=dt, project_id=p2.id))
    db.session.commit()
    pid_no_scale, pid = p1.id, p2.id

# ── Test 1: 定标闸门 ──
print('\n========== Test 1: 全局定标闸门（无比例尺应拒绝） ==========')
summary1 = Orchestrator(app).run(pid_no_scale)
print('status:', summary1.get('status'))
assert summary1.get('status') == 'rejected_by_calibration', '闸门未生效!'
print('OK 无比例尺被拒绝执行:', summary1['errors'])

# ── Test 2: 完整流程 ──
print('\n========== Test 2: Phase1 并行 + Phase2 区域并行 ==========')
t0 = time.time()
summary2 = Orchestrator(app).run(pid)
print('耗时 %.1fs' % (time.time() - t0))
for k in ('status', 'scale_assessment', 'drawing_count', 'drawings_processed', 'zones', 'ghost_suspects'):
    print(f'  {k}: {summary2.get(k)}')
print('  warnings:', *summary2.get('warnings', []), sep='\n    ')
print('  errors:', summary2.get('errors'))
print('  coverage.ok:', summary2['coverage']['ok'], '| suspects:', summary2['coverage']['suspect_count'])

assert summary2['status'] == 'completed', f"状态异常: {summary2['status']}"
assert summary2['drawings_processed'] == 4
assert summary2['scale_assessment'] == 'medium', '应为图纸级并行路径'
assert summary2['coverage']['ok'], '覆盖度对账未通过'
assert summary2['zones']['zone_count'] >= 3, f"分区数不足: {summary2['zones']}"
assert summary2['ghost_suspects'] >= 1, '幽灵房间去重未生效'
print(f"OK Phase1: 4张图并行处理完成, 覆盖度对账通过")
print(f"OK Phase2: 分为 {summary2['zones']['zone_count']} 个区域, 幽灵嫌疑 {summary2['ghost_suspects']} 个")

with app.app_context():
    # 区域归属断言（只看已分区的高置信房间；内外双轮廓的低置信副本不参与断言）
    rooms = Measurement.query.filter_by(project_id=pid, division_id=1).all()
    print('\n-- 房间 → 区域 --')
    zmap = {}
    for r in rooms:
        print(f"  {r.label}: {r.area}m² zone={r.zone_id} boundary={r.boundary_type} conf={r.confidence_level}")
        if r.zone_id:
            zmap[r.label] = r.zone_id
    assert zmap.get('客厅') == 'zone_public'
    assert zmap.get('主卧') == 'zone_bedroom' and zmap.get('次卧') == 'zone_bedroom'
    assert zmap.get('厨房') == 'zone_wet'
    assert '卫生间' in zmap and zmap['卫生间'] == 'zone_wet'

    # 派生项区域标记
    derived = Measurement.query.filter(Measurement.project_id == pid,
                                       Measurement.division_id.in_([2, 3, 5])).all()
    unzoned = [d for d in derived if not d.zone_id]
    print(f'-- 派生项 {len(derived)} 条, 未分区 {len(unzoned)} 条')
    # 立面图生成的"墙面"无所属房间，允许未分区；房间派生项必须全部入区
    bad = [d for d in unzoned if d.label != '墙面']
    assert not bad, f'房间派生项应全部入区: {[d.label for d in bad]}'

    # 门窗分区归属
    d4 = Measurement.query.filter_by(project_id=pid, division_id=4).all()
    d4_unzoned = [d for d in d4 if not d.zone_id]
    print(f'-- 门窗项 {len(d4)} 条: {[(d.label, d.zone_id) for d in d4]}')
    assert len(d4) >= 2, '两张A栋平面图应各检出至少1个门窗'
    assert not d4_unzoned, f'门窗应全部随房间入区: {[d.label for d in d4_unzoned]}'
    assert all(d.zone_id == 'zone_public' for d in d4), '客厅内的门窗应随客厅入公区'

    # 公共墙规则：客厅(公区)↔主卧(居住区) 跨区相邻 → 双方墙面项标记 boundary
    for lbl in ('客厅-墙面', '主卧-墙面'):
        walls = Measurement.query.filter_by(project_id=pid, label=lbl).all()
        assert walls, f'{lbl} 应存在'
        for w in walls:
            assert w.boundary_type == 'boundary', f'{lbl} 应标记 boundary（实际 {w.boundary_type}）'
    for lbl in ('厨房-墙面', '次卧-墙面'):
        walls = Measurement.query.filter_by(project_id=pid, label=lbl).all()
        for w in walls:
            assert w.boundary_type == 'zone', f'{lbl} 不应标记 boundary（实际 {w.boundary_type}）'
    print('-- 公共墙: 客厅/主卧墙面项已标记 boundary, 厨房/次卧保持 zone')

    # 幽灵去重限同图：A栋卫生间(外轮廓)不应被 B栋厨房内轮廓 误杀
    assert any(r.label == '卫生间' and r.zone_id == 'zone_wet' and r.confidence_level == 'high'
               for r in rooms), 'A栋卫生间外轮廓被跨图幽灵误杀'

    # boundary_type 取值分布（公共墙规则验证点）
    kinds = db.session.query(Measurement.boundary_type, db.func.count()) \
        .filter_by(project_id=pid).group_by(Measurement.boundary_type).all()
    print('-- boundary_type 分布:', dict(kinds))

    # 守恒：分区面积 + 幽灵嫌疑面积 == 全部房间面积（幽灵项设计上不参与分区）
    ghosts = [r for r in rooms if r.confidence_level == 'low' and '幽灵嫌疑' in (r.remarks or '')]
    ghost_area = sum(r.area or 0 for r in ghosts)
    zone_area = sum(r.area or 0 for r in rooms if r.zone_id)
    total_area = sum(r.area or 0 for r in rooms)
    kept_area = total_area - ghost_area
    print(f'-- 面积守恒: 分区 {zone_area:.2f} + 幽灵 {ghost_area:.2f} = {zone_area+ghost_area:.2f} vs 全部 {total_area:.2f}')
    assert abs(zone_area - kept_area) < 0.01, '分区面积应等于有效房间面积'
    assert abs(zone_area + ghost_area - total_area) < 0.01, '总面积不守恒'

    # 任务追踪
    tasks = QuantityTask.query.filter_by(project_id=pid).all()
    print('-- 任务记录:', [(t.task_type, t.status) for t in tasks])

    # 归并结果
    from app.orchestrator.zone_worker import merge_boq
    boq = merge_boq(Project.query.get(pid))
    print('-- grand_totals(分部:量):', boq['grand_totals'])
    for z in boq['zones']:
        print(f"   {z['zone_id']}: {z['divisions']}")
    print('-- boundary_items:', [(b['label'], b['zone_id']) for b in boq['boundary_items']])
    assert len(boq['boundary_items']) >= 2, '归并报告应单列区界墙项'

    # ── Phase 3: 三路审计 ──
    audit = summary2['audit']
    assert audit['reverse']['ghost_count'] == 0, '干净流水线不应产生反向幽灵项'
    assert any('卫生间' in w for w in audit['cross']['warnings']), '同名房间跨图面积偏差应告警'
    assert 'skipped' in audit['historical'], '未上传历史清单时应跳过漏项审计'
    print(f"-- Phase3 审计: 反向幽灵 {audit['reverse']['ghost_count']}, "
          f"横向告警 {len(audit['cross']['warnings'])} 条, 历史: 跳过")

    # 注入无法回溯的清单项 + 历史清单 → 复核审计行为
    admin = User.query.filter_by(is_admin=True).first()
    g = Measurement(label='幽灵测试-墙面', division_id=2, area=9.9, unit='m²',
                    source_tag='图纸实测', project_id=pid)   # 无 drawing_id 且无同名房间
    db.session.add(g)
    hp = HistoricalProject(name='历史同类项目', project_type='residential',
                           user_id=admin.id, project_id=pid)
    hp.summary_json = json.dumps({'stats': {
        'total_items': 30, 'floor_area': 120, 'wall_floor_ratio': 2.8,
        'division_items': {'6': ['玄关柜'], '7': ['面盆龙头']}}})
    db.session.add(hp)
    db.session.commit()

    from app.orchestrator.audit import run_audits, build_llm_audit_prompt
    from app.orchestrator.topology_2d import topology_2d_audit
    rep = run_audits(Project.query.get(pid))
    assert rep['reverse']['ghost_count'] == 1, f"应恰好标记1个注入幽灵: {rep['reverse']['ghosts']}"
    assert rep['reverse']['ghosts'][0]['label'] == '幽灵测试-墙面'
    assert rep['historical'].get('missing_count') == 1, \
        f"应只缺玄关柜(面盆龙头已存在): {rep['historical'].get('missing_items')}"
    assert rep['historical']['missing_items'][0]['name'] == '玄关柜'
    # 幽灵项被归并排除、单列
    boq2 = merge_boq(Project.query.get(pid))
    assert any('幽灵测试' in (x.get('label') or '') for x in boq2['ghost_suspects'])
    print(f"-- Phase3 注入验证: 注入幽灵已标记并排除合计, "
          f"历史漏项建议 {rep['historical']['missing_count']} 条(未自动补入)")

    # ── Phase 4: 确定性异常规则 + LLM 优雅降级 ──
    anomaly = summary2['audit']['anomaly']
    assert anomaly['finding_count'] >= 1, '合成图 A/B 栋同内容应检出异常/重复'
    assert any(f['type'] == 'duplicate' for f in anomaly['findings']), '应检出重复清单项'
    print(f"-- Phase4 确定性异常: {anomaly['finding_count']} 条发现（含重复项）")

    # 注入异常尺寸（0.3m 门宽；带 drawing_id 保证可追溯，只触发异常不触发幽灵）
    d1 = Drawing.query.filter_by(project_id=pid).first()
    bad = Measurement(label='客厅-门', division_id=4, length=0.3, height=2.1, unit='樘',
                      source_tag='图纸实测', project_id=pid, drawing_id=d1.id)
    db.session.add(bad)
    db.session.commit()

    app.config['LLM_API_KEY'] = ''   # 测试环境强制走降级路径（不发网络请求）
    rep4 = run_audits(Project.query.get(pid))
    aw = [f for f in rep4['anomaly']['findings']
          if f['type'] == 'anomaly_size' and f.get('measurement_id') == bad.id]
    assert aw, f'0.3m 门宽应命中异常尺寸规则: {rep4["anomaly"]["findings"][:3]}'
    assert 'skipped' in rep4['llm'], '未配置 LLM_API_KEY 时 AI 综合审计应优雅降级'

    # prompt 组装为独立函数，可离线测试（不发网络请求）
    sys_p, user_p = build_llm_audit_prompt(Project.query.get(pid), rep4)
    assert '审计师' in sys_p and '客厅' in user_p and 'anomalies' in user_p
    print(f"-- Phase4 注入验证: 0.3m 门宽命中异常规则, LLM 层优雅降级, prompt 组装正常")

    # ── Phase 4.5: 2D 拓扑预校验 ──
    # 1) 现有合成图中门窗均为中心浮在房间内部（非贴墙），应全部命中“门窗挂墙”规则
    topo_first = summary2['audit']['topology_2d']
    assert topo_first['hit_count'] >= 8, \
        f"Phase4.5 首轮应至少命中 8 个悬空门窗: {topo_first}"
    assert len(topo_first['door_not_on_wall']) >= 8, \
        f"门窗挂墙规则未生效: {topo_first['door_not_on_wall'][:2]}"
    # 命中项已降级 + 备注
    suspect_doors = Measurement.query.filter_by(project_id=pid, division_id=4).all()
    flagged = [d for d in suspect_doors if '2D拓扑嫌疑' in (d.remarks or '')]
    assert len(flagged) >= 8, f"门窗应已降级 + 标注备注, 实际命中 {len(flagged)}"
    assert all(d.confidence_level == 'low' for d in flagged), '拓扑嫌疑项必须降为低置信'
    print(f"-- Phase4.5 首轮: 悬空门窗 {len(topo_first['door_not_on_wall'])} 个命中，"
          f"房间 bbox 可疑 {len(topo_first['invalid_room_polygons'])}, "
          f"面积不守恒 {len(topo_first['area_conservation_violation'])}")

    # 2) 注入退化房间 bbox（w=0）→ invalid_room_polygons 命中
    bad_room = Measurement(label='退化房', division_id=1, area=0.0, unit='m²',
                           source_tag='图纸实测', project_id=pid,
                           drawing_id=Drawing.query.filter_by(project_id=pid).first().id,
                           zone_id='zone_test',
                           data_json=json.dumps({'bbox': [100, 100, 0, 50]}))
    db.session.add(bad_room); db.session.commit()

    # 3) 注入面积不守恒测试——新开一个唯一前缀房间＋不匹配天棚，避开同名多候选歧义
    test_room = Measurement(label='拓扑测试房', division_id=1, area=20.0, unit='m²',
                            source_tag='图纸实测', project_id=pid,
                            drawing_id=Drawing.query.filter_by(project_id=pid).first().id,
                            zone_id='zone_test',
                            data_json=json.dumps({'bbox': [800, 800, 200, 150]}))
    test_ceiling = Measurement(label='拓扑测试房-天棚', division_id=3,
                               area=30.0,  # 20 地面 vs 30 天棚，偏差 50% ≫ 5%
                               unit='m²', source_tag='几何推算', project_id=pid)
    db.session.add_all([test_room, test_ceiling]); db.session.commit()

    topo2 = topology_2d_audit(Project.query.get(pid))
    assert any(h.get('label') == '退化房' and h['reason'] == 'degenerate'
               for h in topo2['invalid_room_polygons']), \
        f"退化 bbox 应命中: {topo2['invalid_room_polygons']}"
    assert any(h['room'] == '拓扑测试房' and h['delta_pct'] > 5.0
               for h in topo2['area_conservation_violation']), \
        f"注入的不守恒天棚应命中: {topo2['area_conservation_violation']}"
    # 幂等性：已标记的门窗不重复命中
    assert len(topo2['door_not_on_wall']) == 0, \
        f"拓扑校验应幂等，已标记项不重复命中: {topo2['door_not_on_wall']}"
    print(f"-- Phase4.5 注入验证: 退化房间 + 面积不守恒天棚命中，幂等无重复")

    # 4) LLM prompt 包含拓扑预校验发现
    sys_p5, user_p5 = build_llm_audit_prompt(Project.query.get(pid), {
        'reverse': topo_first, 'cross': {}, 'historical': {},
        'anomaly': {}, 'topology_2d': topo_first,
    })
    assert 'topology_2d' in user_p5 and 'door_not_on_wall' in user_p5, \
        'LLM prompt 未包含拓扑预校验发现'
    print(f"-- Phase4.5 集成验证: LLM prompt 已包含拓扑发现字段")

# ── Test 3: API 冒烟 ──
print('\n========== Test 3: API 冒烟（run/status/zones） ==========')
with app.app_context():
    admin = User.query.filter_by(is_admin=True).first()   # 重新加载（上一上下文已退出）
    p3 = Project(name='API冒烟项目', scale=100, user_id=admin.id)
    db.session.add(p3); db.session.flush()
    db.session.add(Drawing(filename='A1F.png', original_name='api.png', file_type='png', project_id=p3.id))
    db.session.commit()
    pid3 = p3.id

c = app.test_client()
c.post('/auth/login', data={'email': 'admin@decoration-quant.com', 'password': 'admin123456'})
r = c.post(f'/api/orchestrator/projects/{pid3}/run')
print('run:', r.status_code, r.get_json())
for _ in range(60):
    time.sleep(0.5)
    s = c.get(f'/api/orchestrator/projects/{pid3}/status').get_json()
    if s and s.get('task_counts', {}).get('completed', 0) >= 5:
        break
print('status:', c.get(f'/api/orchestrator/projects/{pid3}/status').get_json())
z = c.get(f'/api/orchestrator/projects/{pid3}/zones').get_json()
print('zones api: zone_count=', len(z.get('zones', [])), '| grand:', z.get('grand_totals'))

print('\n========== 全部断言通过 ==========')
