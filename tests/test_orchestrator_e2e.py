# -*- coding: utf-8 -*-
"""端到端回归：PNG 识别 → 分区守恒 → 归并 → 三路审计 → 拓扑预校验 → API 冒烟。

覆盖主管线（processing/zone/orchestrator/audit/topology_2d/api/auth），
与 MEP 封账 golden（tests/test_mep_*.py，PDF 算量线）互补。

结构说明：
  - session 级 fixture 建临时 DB + 合成图纸 + 两个项目；两次 Orchestrator.run
    各执行一次（昂贵），结果以 fixture 共享。
  - 用例按文件顺序执行且共享累积状态（Phase3/4/4.5 的注入项是后续断言的前提），
    不可乱序/单挑中间用例运行。
  - 无网络依赖：LLM 层强制 LLM_API_KEY='' 走优雅降级路径。
  - Phase 5a：设 TEST_DATABASE_URL 走 PG，否则回退 SQLite（CI 默认 SQLite）。
"""
import os
import sys
import time
import shutil
import json
from types import SimpleNamespace

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)

import numpy as np
import cv2
import pytest

TMP = '/tmp/qty_e2e'
WALL = 8  # 墙厚 px

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
    # 安全修复后 admin 密码走配置通道（生产无默认值）：测试显式钉一个已知密码供登录冒烟
    ADMIN_PASSWORD = 'admin123456'
    ADMIN_EMAIL = 'admin@decoration-quant.com'


# ── 合成图纸：A 栋(客厅27/主卧16/卫生间3 m²@100px/m) B 栋(次卧/厨房) 立面 ──
def _ring(img, x1, y1, x2, y2):
    cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 0), WALL)


def _make_plan_a(path):
    img = np.full((620, 1600, 3), 255, np.uint8)
    _ring(img, 40, 40, 580, 580)      # 客厅 540x540
    _ring(img, 600, 40, 1020, 460)    # 主卧 420x420（与客厅墙间距20px → 跨区相邻触发公共墙标记）
    _ring(img, 1120, 40, 1330, 220)   # 卫生间 210x180
    cv2.line(img, (300, 300), (400, 300), (0, 0, 0), 2)  # 客厅内 1m 线段 → 门窗检出
    cv2.imwrite(path, img)


def _make_plan_b(path):
    img = np.full((420, 800, 3), 255, np.uint8)    # 次卧9.6m² 厨房6.3m²
    _ring(img, 40, 40, 380, 360)      # 次卧 340x320
    _ring(img, 440, 40, 770, 230)     # 厨房 330x190
    cv2.imwrite(path, img)


def _make_elevation(path):
    img = np.full((280, 600, 3), 255, np.uint8)
    cv2.line(img, (30, 250), (570, 250), (0, 0, 0), 4)
    cv2.line(img, (30, 30), (570, 30), (0, 0, 0), 4)
    cv2.imwrite(path, img)


@pytest.fixture(scope='session')
def app_env():
    """临时库 + 合成图纸 + 项目1(无比例尺，闸门用例) + 项目2(完整管线)。"""
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP + '/uploads', exist_ok=True)
    _make_plan_a(TMP + '/uploads/A1F.png')
    _make_plan_a(TMP + '/uploads/B1F.png')   # 第二层同户型 → 触发 medium 级并行（4 图纸）
    _make_plan_b(TMP + '/uploads/A2F.png')
    _make_elevation(TMP + '/uploads/S.png')

    from app import create_app, db
    from app.models import User, Project, Drawing
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        # Phase 5a: PG 下需清理上一次测试遗留数据（SQLite 因 rmtree 无此问题）
        if _PG_URL:
            from sqlalchemy import text as _t
            for tbl in ['measurements', 'reports', 'self_check_items',
                        'quantity_tasks', 'drawings', 'projects', 'historical_projects']:
                db.session.execute(_t(f'TRUNCATE TABLE {tbl} RESTART IDENTITY CASCADE'))
            db.session.commit()
        admin = User.query.filter_by(is_admin=True).first()

        p1 = Project(name='无比例尺项目', user_id=admin.id)          # 期望被定标闸门拒绝
        db.session.add(p1)
        db.session.flush()
        db.session.add(Drawing(filename='A1F.png', original_name='A1F.png',
                               file_type='png', project_id=p1.id))

        p2 = Project(name='标准项目', scale=100, default_floor_height=2.8, user_id=admin.id)
        db.session.add(p2)
        db.session.flush()
        for fn, name, dt in [('A1F.png', 'A栋一层平面.png', 'floor_plan'),
                             ('B1F.png', 'B栋一层平面.png', 'floor_plan'),
                             ('A2F.png', 'A栋二层平面.png', 'floor_plan'),
                             ('S.png', '南立面.png', 'elevation')]:
            db.session.add(Drawing(filename=fn, original_name=name, file_type='png',
                                   drawing_type=dt, project_id=p2.id))
        db.session.commit()
        return SimpleNamespace(app=app, pid_no_scale=p1.id, pid=p2.id, admin_id=admin.id)


@pytest.fixture(scope='session')
def no_scale_summary(app_env):
    """项目1 无比例尺 → 期望 rejected_by_calibration。"""
    from app.orchestrator import Orchestrator
    return Orchestrator(app_env.app).run(app_env.pid_no_scale)


@pytest.fixture(scope='session')
def full_summary(app_env):
    """项目2 完整管线（Phase1 并行 + Phase2 分区 + Phase3/4/4.5 审计），只跑一次。"""
    from app.orchestrator import Orchestrator
    t0 = time.time()
    s = Orchestrator(app_env.app).run(app_env.pid)
    s['_elapsed'] = time.time() - t0
    return s


# ── Test 1: 全局定标闸门 ──────────────────────────────────────────────
def test_calibration_gate_rejects(no_scale_summary):
    assert no_scale_summary.get('status') == 'rejected_by_calibration', '闸门未生效!'
    assert no_scale_summary['errors'], '拒绝时应给出原因'


# ── Test 2: Phase1 并行 + Phase2 区域并行 ─────────────────────────────
def test_pipeline_completed(full_summary):
    s = full_summary
    assert s['status'] == 'completed', f"状态异常: {s['status']}"
    assert s['drawings_processed'] == 4
    assert s['scale_assessment'] == 'medium', '应为图纸级并行路径'
    assert s['coverage']['ok'], '覆盖度对账未通过'
    assert s['zones']['zone_count'] >= 3, f"分区数不足: {s['zones']}"
    assert s['ghost_suspects'] >= 1, '幽灵房间去重未生效'
    assert not s['errors'], f"管线不应有图纸级错误: {s['errors']}"


def _rooms(app_env):
    from app import db
    from app.models import Measurement
    with app_env.app.app_context():
        return db.session.query(Measurement).filter_by(
            project_id=app_env.pid, division_id=1).all()


def test_zone_membership(app_env):
    """房间→区域归属 + 派生项入区 + 门窗随房 + 公共墙规则 + 幽灵限同图。"""
    rooms = _rooms(app_env)
    zmap = {r.label: r.zone_id for r in rooms if r.zone_id}
    assert zmap.get('客厅') == 'zone_public'
    assert zmap.get('主卧') == 'zone_bedroom' and zmap.get('次卧') == 'zone_bedroom'
    assert zmap.get('厨房') == 'zone_wet'
    assert zmap.get('卫生间') == 'zone_wet'

    from app import db
    from app.models import Measurement
    with app_env.app.app_context():
        # 房间派生项（墙面/天棚/踢脚等 div2/3/5）必须全部入区；立面生成的"墙面"除外
        derived = Measurement.query.filter(
            Measurement.project_id == app_env.pid,
            Measurement.division_id.in_([2, 3, 5])).all()
        bad = [d for d in derived if not d.zone_id and d.label != '墙面']
        assert not bad, f'房间派生项应全部入区: {[d.label for d in bad]}'

        # 门窗全部随房间入区（合成本图客厅检出 ≥2）
        d4 = Measurement.query.filter_by(project_id=app_env.pid, division_id=4).all()
        assert len(d4) >= 2, '两张A栋平面图应各检出至少1个门窗'
        assert not [d for d in d4 if not d.zone_id], '门窗应全部随房间入区'
        assert all(d.zone_id == 'zone_public' for d in d4), '客厅内的门窗应随客厅入公区'

        # 公共墙规则：客厅(公区)↔主卧(居住区) 跨区相邻 → boundary；同区保持 zone
        for lbl in ('客厅-墙面', '主卧-墙面'):
            walls = Measurement.query.filter_by(project_id=app_env.pid, label=lbl).all()
            assert walls, f'{lbl} 应存在'
            for w in walls:
                assert w.boundary_type == 'boundary', \
                    f'{lbl} 应标记 boundary（实际 {w.boundary_type}）'
        for lbl in ('厨房-墙面', '次卧-墙面'):
            for w in Measurement.query.filter_by(project_id=app_env.pid, label=lbl):
                assert w.boundary_type == 'zone', f'{lbl} 不应标记 boundary'

        # 幽灵去重限同图：A栋卫生间(外轮廓)不应被 B栋厨房内轮廓误杀
        assert any(r.label == '卫生间' and r.zone_id == 'zone_wet'
                   and r.confidence_level == 'high' for r in rooms), \
            'A栋卫生间外轮廓被跨图幽灵误杀'


def test_area_conservation(app_env):
    """分区面积 + 幽灵嫌疑面积 == 全部房间面积（幽灵设计上不参与分区）。"""
    rooms = _rooms(app_env)
    ghosts = [r for r in rooms if r.confidence_level == 'low' and '幽灵嫌疑' in (r.remarks or '')]
    ghost_area = sum(r.area or 0 for r in ghosts)
    zone_area = sum(r.area or 0 for r in rooms if r.zone_id)
    total_area = sum(r.area or 0 for r in rooms)
    assert abs(zone_area - (total_area - ghost_area)) < 0.01, '分区面积应等于有效房间面积'
    assert abs(zone_area + ghost_area - total_area) < 0.01, '总面积不守恒'


def test_merge_boq_boundary_items(app_env):
    from app.models import Project
    from app.orchestrator.zone_worker import merge_boq
    with app_env.app.app_context():
        boq = merge_boq(Project.query.get(app_env.pid))
        assert len(boq['boundary_items']) >= 2, '归并报告应单列区界墙项'


# ── Test 3: Phase3 三路审计（干净 → 注入复核） ────────────────────────
def test_phase3_clean_audit(full_summary):
    audit = full_summary['audit']
    assert audit['reverse']['ghost_count'] == 0, '干净流水线不应产生反向幽灵项'
    assert any('卫生间' in w for w in audit['cross']['warnings']), '同名房间跨图面积偏差应告警'
    assert 'skipped' in audit['historical'], '未上传历史清单时应跳过漏项审计'


def test_phase3_injection(app_env):
    """注入无回溯幽灵项 + 历史清单 → 恰中标记、归并排除、漏项建议不自动补入。"""
    from app import db
    from app.models import Measurement, HistoricalProject
    from app.orchestrator.audit import run_audits
    from app.orchestrator.zone_worker import merge_boq
    with app_env.app.app_context():
        g = Measurement(label='幽灵测试-墙面', division_id=2, area=9.9, unit='m²',
                        source_tag='图纸实测', project_id=app_env.pid)   # 无 drawing_id 且无同名房间
        db.session.add(g)
        hp = HistoricalProject(name='历史同类项目', project_type='residential',
                               user_id=app_env.admin_id, project_id=app_env.pid)
        hp.summary_json = json.dumps({'stats': {
            'total_items': 30, 'floor_area': 120, 'wall_floor_ratio': 2.8,
            'division_items': {'6': ['玄关柜'], '7': ['面盆龙头']}}})
        db.session.add(hp)
        db.session.commit()

        from app.models import Project
        rep = run_audits(Project.query.get(app_env.pid))
        assert rep['reverse']['ghost_count'] == 1, \
            f"应恰好标记1个注入幽灵: {rep['reverse']['ghosts']}"
        assert rep['reverse']['ghosts'][0]['label'] == '幽灵测试-墙面'
        assert rep['historical'].get('missing_count') == 1, \
            f"应只缺玄关柜(面盆龙头已存在): {rep['historical'].get('missing_items')}"
        assert rep['historical']['missing_items'][0]['name'] == '玄关柜'
        boq2 = merge_boq(Project.query.get(app_env.pid))
        assert any('幽灵测试' in (x.get('label') or '') for x in boq2['ghost_suspects']), \
            '注入幽灵应被归并排除并单列'


# ── Test 4: Phase4 确定性异常 + LLM 优雅降级 ──────────────────────────
def test_phase4_anomaly_and_llm_degradation(app_env, full_summary):
    anomaly = full_summary['audit']['anomaly']
    assert anomaly['finding_count'] >= 1, '合成图 A/B 栋同内容应检出异常/重复'
    assert any(f['type'] == 'duplicate' for f in anomaly['findings']), '应检出重复清单项'

    from app import db
    from app.models import Measurement, Drawing, Project
    from app.orchestrator.audit import run_audits, build_llm_audit_prompt
    with app_env.app.app_context():
        # 注入 0.3m 门宽（带 drawing_id 可追溯，只触发异常不触发幽灵）
        d1 = Drawing.query.filter_by(project_id=app_env.pid).first()
        bad = Measurement(label='客厅-门', division_id=4, length=0.3, height=2.1, unit='樘',
                          source_tag='图纸实测', project_id=app_env.pid, drawing_id=d1.id)
        db.session.add(bad)
        db.session.commit()

        app_env.app.config['LLM_API_KEY'] = ''   # 强制降级路径（不发网络请求）
        rep4 = run_audits(Project.query.get(app_env.pid))
        aw = [f for f in rep4['anomaly']['findings']
              if f['type'] == 'anomaly_size' and f.get('measurement_id') == bad.id]
        assert aw, f'0.3m 门宽应命中异常尺寸规则: {rep4["anomaly"]["findings"][:3]}'
        assert 'skipped' in rep4['llm'], '未配置 LLM_API_KEY 时 AI 综合审计应优雅降级'

        sys_p, user_p = build_llm_audit_prompt(Project.query.get(app_env.pid), rep4)
        assert '审计师' in sys_p and '客厅' in user_p and 'anomalies' in user_p


# ── Test 5: Phase4.5 2D 拓扑预校验 ────────────────────────────────────
def test_phase4_5_topology(app_env, full_summary):
    # 首轮：合成图门窗中心浮在房间内部（非贴墙）→ 全命中"门窗挂墙"并降级
    topo_first = full_summary['audit']['topology_2d']
    assert topo_first['hit_count'] >= 8, f"首轮应至少命中 8 个悬空门窗: {topo_first}"
    assert len(topo_first['door_not_on_wall']) >= 8, \
        f"门窗挂墙规则未生效: {topo_first['door_not_on_wall'][:2]}"

    from app import db
    from app.models import Measurement, Drawing, Project
    from app.orchestrator.topology_2d import topology_2d_audit
    from app.orchestrator.audit import build_llm_audit_prompt
    with app_env.app.app_context():
        doors = Measurement.query.filter_by(project_id=app_env.pid, division_id=4).all()
        flagged = [d for d in doors if '2D拓扑嫌疑' in (d.remarks or '')]
        assert len(flagged) >= 8, f'门窗应已降级 + 标注备注, 实际命中 {len(flagged)}'
        assert all(d.confidence_level == 'low' for d in flagged), '拓扑嫌疑项必须降为低置信'

        # 注入退化房间 bbox（w=0）→ invalid_room_polygons 命中
        did = Drawing.query.filter_by(project_id=app_env.pid).first().id
        db.session.add(Measurement(
            label='退化房', division_id=1, area=0.0, unit='m²', source_tag='图纸实测',
            project_id=app_env.pid, drawing_id=did, zone_id='zone_test',
            data_json=json.dumps({'bbox': [100, 100, 0, 50]})))
        # 注入面积不守恒：唯一前缀房间 + 20 地面 vs 30 天棚（偏差 50% ≫ 5%）
        db.session.add(Measurement(
            label='拓扑测试房', division_id=1, area=20.0, unit='m²', source_tag='图纸实测',
            project_id=app_env.pid, drawing_id=did, zone_id='zone_test',
            data_json=json.dumps({'bbox': [800, 800, 200, 150]})))
        db.session.add(Measurement(
            label='拓扑测试房-天棚', division_id=3, area=30.0, unit='m²',
            source_tag='几何推算', project_id=app_env.pid))
        db.session.commit()

        topo2 = topology_2d_audit(Project.query.get(app_env.pid))
        assert any(h.get('label') == '退化房' and h['reason'] == 'degenerate'
                   for h in topo2['invalid_room_polygons']), \
            f"退化 bbox 应命中: {topo2['invalid_room_polygons']}"
        assert any(h['room'] == '拓扑测试房' and h['delta_pct'] > 5.0
                   for h in topo2['area_conservation_violation']), \
            f"注入的不守恒天棚应命中: {topo2['area_conservation_violation']}"
        assert len(topo2['door_not_on_wall']) == 0, \
            f"拓扑校验应幂等，已标记项不重复命中: {topo2['door_not_on_wall']}"

        sys_p5, user_p5 = build_llm_audit_prompt(Project.query.get(app_env.pid), {
            'reverse': topo_first, 'cross': {}, 'historical': {},
            'anomaly': {}, 'topology_2d': topo_first,
        })
        assert 'topology_2d' in user_p5 and 'door_not_on_wall' in user_p5, \
            'LLM prompt 未包含拓扑预校验发现'


# ── Test 6: API 冒烟（run / status / zones） ──────────────────────────
def test_api_smoke(app_env):
    from app import db
    from app.models import User, Project, Drawing
    app = app_env.app
    with app.app_context():
        admin = User.query.filter_by(is_admin=True).first()
        p3 = Project(name='API冒烟项目', scale=100, user_id=admin.id)
        db.session.add(p3)
        db.session.flush()
        db.session.add(Drawing(filename='A1F.png', original_name='api.png',
                               file_type='png', project_id=p3.id))
        db.session.commit()
        pid3 = p3.id

    c = app.test_client()
    r = c.post('/auth/login', data={'email': TestConfig.ADMIN_EMAIL,
                                    'password': TestConfig.ADMIN_PASSWORD})
    assert r.status_code in (200, 302), f'登录失败: {r.status_code}'
    r = c.post(f'/api/orchestrator/projects/{pid3}/run')
    assert r.status_code == 200 and r.get_json().get('status') == 'started'
    for _ in range(60):
        time.sleep(0.5)
        s = c.get(f'/api/orchestrator/projects/{pid3}/status').get_json()
        if s and s.get('task_counts', {}).get('completed', 0) >= 5:
            break
    s = c.get(f'/api/orchestrator/projects/{pid3}/status').get_json()
    assert s and s.get('measurement_count', 0) > 0, f'status 无测量: {s}'
    z = c.get(f'/api/orchestrator/projects/{pid3}/zones').get_json()
    assert z and 'zones' in z, 'zones 端点应返回 JSON 分区结果'
    assert len(z['zones']) >= 1 and z.get('grand_totals'), f'zones 内容异常: {z}'
