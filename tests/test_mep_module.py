#!/usr/bin/env python
"""app/orchestrator/mep 模块测试。

Tier A（纯函数/工件）CI 可跑；Tier B（全量重跑配准）需本地图纸，标记 slow。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.orchestrator.mep import counts, leaders  # noqa: E402
from app.orchestrator.mep.registration import K, load_index  # noqa: E402

PDF = os.path.join(ROOT, "装修图纸", "227户型dxf",
                   "广州珠实金融城住宅项目--227户型施工图纸.pdf")
ART = os.path.join(ROOT, "outputs", "preview", "vlm", "mep", "m2_attribution.json")
MEP_ART = os.path.join(ROOT, "outputs", "preview", "vlm", "mep")

# M2 封账标定值（mep_takeoff_m2.md §0）
SEALED_TX, SEALED_TY = -1478317.20, 6457826.22


# ── Tier A：纯函数 ────────────────────────────────────────────────────
def test_expand_token_variants():
    assert counts.expand_token("LD-01") == ["LD-01"]
    assert counts.expand_token("LD-26~LD27") == ["LD-26", "LD-27"]   # 后半省横线
    assert counts.expand_token("LD-14~LD16") == ["LD-14", "LD-15", "LD-16"]
    assert counts.expand_token("LD-12~13") == ["LD-12", "LD-13"]     # 后半省前缀
    assert counts.expand_token("ALD-01~ALD-06")[-1] == "ALD-06"
    assert counts.expand_token("AC") == []                           # 面板按键不入代码
    assert counts.expand_token("EX") == []


def test_circuit_items():
    got = counts.circuit_items("LD-23~LD-25、AC、ALD-16~ALD-17")
    assert got == ["LD-23", "LD-24", "LD-25", "ALD-16", "ALD-17"]


def test_dedup():
    pts = [(920.0, 536.7), (921.0, 540.0), (305.7, 542.1)]
    assert counts.dedup(pts, tol=15.0) == [(920.0, 536.7), (305.7, 542.1)]


def test_room_of_uses_edge_not_vertex_distance():
    """M5b 勘误回归：长墙中段墙装件应按点到边距离归属（顶点距会误判未能归属）。"""
    from app.orchestrator.mep.registration import RoomIndex
    # 细长矩形房间：长边中段点距两顶点都远(>100)但距边仅 5pt
    poly = [(0.0, 0.0), (400.0, 0.0), (400.0, 100.0), (0.0, 100.0)]
    idx = RoomIndex([{"name": "卧室", "poly_pt": poly}])
    assert idx.room_of(200.0, 105.0) == "卧室"      # 长墙外 5pt → 应吸附
    assert idx.room_of(200.0, 130.0) is None         # 30pt 超墙装容差 → 不硬落域


@pytest.mark.skipif(not os.path.exists(ART), reason="m2_attribution.json 不在库")
def test_load_index_sealed_calib():
    idx, calib = load_index(ART)
    assert abs(calib["tx"] - SEALED_TX) < 0.01
    assert abs(calib["ty"] - SEALED_TY) < 0.01
    assert len(idx.rooms) >= 14
    # 抽查封账归属：客厅开敞面内一点应落该面
    open_room = next(r for r in idx.rooms if r["name"].startswith("客厅"))
    import statistics
    xs = [p[0] for p in open_room["poly_pt"]]
    ys = [p[1] for p in open_room["poly_pt"]]
    assert idx.room_of(statistics.median(xs), statistics.median(ys)) is not None


# ── Tier A：引线行走（leaders，合成线段无需图纸） ────────────────────
BB = (900.0, 500.0, 960.0, 512.0)   # 注记 bbox，中心 (930,506)
LEADER = [
    (890.0, 506.0, 830.0, 506.0),   # 水平母线（种子：远端 (830,506)）
    (830.0, 506.0, 790.0, 466.0),   # 45° 斜引线（转弯，离锚更远）
    (790.0, 466.0, 760.0, 466.0),   # 水平尾段 → 箭头端点
]


def test_track_synthetic_leader():
    ep, path, total = leaders.track(LEADER, BB)
    assert ep == pytest.approx((760.0, 466.0))   # 端点=箭头落点
    assert path[0] == (930.0, 506.0)             # 起点= bbox 中心
    assert total == pytest.approx(60 + 56.569 + 30, abs=0.1)


def test_track_no_seed_returns_none():
    far = [(100.0, 100.0, 160.0, 100.0)]        # 距 bbox 远超 SEED_DRECT
    assert leaders.track(far, BB) == (None, [], 0.0)


def test_track_seed_takes_far_end():
    # 种子取离 bbox 更远端起步；近端 (890,506) 不应成为路径第二点
    _ep, path, _L = leaders.track(LEADER, BB)
    assert path[1] == (830.0, 506.0)


def test_attribute_rows_weights_and_short_chain_hook():
    # 成功链 n 直取；短链(<MIN_CHAIN_LEN) 挂失败行但锚点原地落域
    rooms = {("客厅", (700, 400, 850, 550)), ("主人卧室", (860, 560, 970, 650))}

    def room_of(x, y):
        for name, (x0, y0, x1, y1) in rooms:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return name
        return None

    kinds = {"二三极插座": {"items": [[2, BB], [1, (900.0, 600.0, 960.0, 612.0)]]},
             "USB插座": [[1, BB]]}   # 裸列表形式（P09 同款）也要支持
    stub = LEADER + [(890.0, 606.0, 880.0, 606.0)]   # 第二条：仅 10pt 短链
    rows, chains = leaders.attribute_rows(kinds, stub, room_of)
    assert len(rows) == 3 and len(chains) == 2
    ok = [r for r in rows if r["endpoint"]]
    assert sum(r["n"] for r in ok) == 2 + 1          # n 直取不重乘（X2 已含在 M2 的 n）
    fail = next(r for r in rows if not r["endpoint"])
    assert fail["room"] == "主人卧室"                  # 失败行锚点 (930,606) 原地落域
    agg = leaders.aggregate(rows)
    assert agg["二三极插座|客厅"] == 2 and agg["二三极插座|主人卧室"] == 1


# ── Tier B：全量重跑配准（本地图纸） ─────────────────────────────────
@pytest.mark.slow
@pytest.mark.skipif(not os.path.exists(PDF), reason="施工图纸 PDF 不在本地")
def test_registration_reproducible():
    """从零重跑配准，标定平移应复现封账值 ±2pt。"""
    import fitz
    from app.orchestrator.mep import registration as R
    doc = fitz.open(PDF)
    rooms = R.room_faces()
    assert len(rooms) >= 14
    calib = R.calibrate(rooms, doc[R.PLAN_PAGE])
    assert abs(calib["tx"] - SEALED_TX) < 2.0, f"tx 漂移 {calib['tx']}"
    assert abs(calib["ty"] - SEALED_TY) < 2.0, f"ty 漂移 {calib['ty']}"
    assert calib["wall_fit_px"] < 25.0


# M5 封账引线追踪成功数（mep_takeoff_m5.md §2/§3）
SEALED_TRACKED = {"P08": 67, "P09": 22}


@pytest.mark.slow
@pytest.mark.skipif(not os.path.exists(PDF), reason="施工图纸 PDF 不在本地")
def test_leaders_reproducible():
    """从零重跑引线归属，追踪成功数应复现封账 67/22（模块与一次性脚本同口径）。"""
    import json
    import fitz
    from app.orchestrator.mep import leaders as L
    doc = fitz.open(PDF)
    idx, _ = load_index(ART)
    art = os.path.join(ROOT, "outputs", "preview", "vlm", "mep")
    c18 = json.load(open(os.path.join(art, "m2_counts_clean.json"), encoding="utf-8"))["18"]
    segs18 = L.thin_segments(doc[18])
    rows18, _ = L.attribute_rows(c18, segs18, idx.room_of)
    n18 = sum(1 for r in rows18 if r["endpoint"])
    assert n18 == SEALED_TRACKED["P08"], f"P08 追踪成功 {n18} != 封账 {SEALED_TRACKED['P08']}"
    # 二三极归属 Σ 回到 M2 封账 28（不含待复核）
    ok28 = sum(r["n"] for r in rows18 if r["endpoint"] and r["kind"] == "二三极插座")
    assert ok28 + sum(r["n"] for r in rows18
                      if not r["endpoint"] and r["kind"] == "二三极插座") == 28


# ── Tier A：封账数字 → 计算表组装（scripts/build_mep_workbook.py） ────
def _wb():
    """按路径加载组装器(非包内模块, 不能直接 import)。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'build_mep_workbook',
        os.path.join(ROOT, 'scripts', 'build_mep_workbook.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_mep_workbook_identities(tmp_path):
    """组装器恒等: sheet合计≡封账json; build() 内断言不破账; xlsx 渲染冒烟。
    纪律: 本用例封住"M2~M5封账数字接入计价/BOQ"链路的数值同一性——
    任何封账工件被改动而组装器未同步, 此处即红。"""
    m = _wb()
    data = m.build()   # 内部任一 AssertionError = 破账
    assert data['totals'] == {'P08': 107, 'P09': 24, 'hvac': 18, 'elv': 23,
                              'sprinkler': 33, 'control_wires': 93}
    # ①数量列合计 = 各封账之和(205 口径: 107+24+18+23+33, 烟感0不计)
    assert sum(r[4] for r in data['sheet1']) == 107 + 24 + 18 + 23 + 33
    # P08 重封账链不得断: M2 封账 86 + M5f 补账 21 = 107，且①内 P08 两路行数可追
    sm = data['supp_meta']
    assert (sm['m2'], sm['supp'], sm['sealed']) == (86, 21, 107)
    assert sm['box_n'] == 3, '盒类 3 处另立项，已在 107 之外排除'
    assert len(data['sheet5']) == 24, '⑤补账明细 = 计入21 + 不计3'
    assert sum(r[2].startswith('计入') for r in data['sheet5']) == 21
    # 归属待核(M5c §8 撞车 + M5f 补账注记 + M5 §11 末端标记未命中): 数量不动仅状态降级
    # rows_all == rows: 未能归属行不重复打降级原因(挂账优先级更高, §11.3)
    assert data['pending'] == {'P08': {'rows': 45, 'n': 50, 'rows_all': 45},
                               'P09': {'rows': 15, 'n': 15, 'rows_all': 15}}
    # 待核拆行聚合不得丢量/重量: 待核件数 65 = 50(P08) + 15(P09)
    assert sum(r[4] for r in data['sheet1'] if r[7].startswith('归属待核')) == 65
    # §11 箭头规则: 状态真变了 16 行; 有箭头凭证但仍待核的行只披露不升档(24 条)
    assert data['arrow']['n_noarrow_downgraded'] == 16
    assert len(data['sheet4_hitpend']) == 24
    # ④对账表每行差值恒为 0
    assert all(r[1] == r[2] for r in data['sheet4_recon'])
    out = str(tmp_path / 'wb_test.xlsx')
    m.write_xlsx(data, out)
    assert os.path.exists(out) and os.path.getsize(out) > 5000


def test_manual_pend_ruling_mechanism():
    """人工裁定例外机制(§10)的纪律仍成立 —— 但清单已清空(§8.5/§11)。

    §11 箭头规则生效后, M5g 那条唯一裁定(冰箱+400HT, 全形态 15.65pt)已由**入库证据**
    自动推出 → 能从仓库复算出来的降级不得藏在人工清单里(否则机器判据永远暴露
    不出它盖不住哪)。机制本身保留给规则确实盖不到的个案, 所以用合成条目守住纪律。
    """
    m = _wb()
    assert m.MANUAL_PEND == [], \
        '能由入库证据(§11 末端标记普查)推出的降级请交给规则, 不要留在人工清单'

    rows = [{'kind': '冰箱插座', 'anchor': [746.0, 742.5], 'endpoint': [726.4, 575.2], 'room': '客厅/茶室+餐厅+玄白'},
            {'kind': '二三极插座', 'anchor': [10.0, 10.0], 'room': '主卧'}]
    e = {'kind': '冰箱插座', 'anchor': [746.0, 742.5],
         'room_ruled': '客厅/茶室+餐厅+玄白', 'why': '用户裁定X'}

    # 正常路径: 只钉住一行, 只填原本未降级的行
    assert m._manual_flags(rows, ['', ''], [e]) == ['用户裁定X', '']
    # 不抢已有原因(与 §11 箭头规则同构: 只填空, 不覆盖)
    assert m._manual_flags(rows, ['端点与其类引线共用', ''], [e])[0] == '端点与其类引线共用'
    # 坐标漂移(命中 0/2 行) / 房间漂移 / 无端点行 → 直接构建失败, 逼重新裁定
    with pytest.raises(AssertionError, match='命中 0 行'):
        m._manual_flags(rows, ['', ''], [dict(e, anchor=[1.0, 1.0])])
    with pytest.raises(AssertionError, match='命中 2 行'):
        m._manual_flags(rows + [dict(rows[0])], ['', '', ''], [e])
    with pytest.raises(AssertionError, match='房间漂移'):
        m._manual_flags(rows, ['', ''], [dict(e, room_ruled='书房')])
    with pytest.raises(AssertionError, match='无端点'):
        m._manual_flags(rows, ['', ''],
                        [dict(e, kind='二三极插座', anchor=[10.0, 10.0], room_ruled='主卧')])


def _fake_art_dir(tmp_path):
    """把组装器要读的封账工件拷到 tmp_path(测试要改副本做篡改, 不动仓内工件)。"""
    import shutil
    need = ['m2_counts_clean.json', 'm2_textcounts.json', 'm3_circuits.json',
            'p24_sprinkler_split.json', 'm5_P08_rows.json', 'm5_P09_rows.json',
            'm5f_supplement_P08.json', 'm5h_arrows_P08.json', 'm5h_arrows_P09.json']
    for f in need:
        shutil.copy(os.path.join(MEP_ART, f), str(tmp_path / f))
    return str(tmp_path)


def test_arrow_evidence_cannot_go_stale(tmp_path):
    """§11 证据链不得吃旧普查: 工件 md5 不符 / 行数或坐标与归属行对不上 → 构建失败。

    封住的是一个实际发生过的风险: rows 工件重封账而普查未重跑时,
    逐行距离会静默地对着错的行, 而降级看上去仍然“合理”。"""
    m = _wb()
    art = _fake_art_dir(tmp_path)
    m.ART = art
    base = m.build()                     # 副本必须能跑出与仓内一致的账
    assert base['pending'] == {'P08': {'rows': 45, 'n': 50, 'rows_all': 45},
                               'P09': {'rows': 15, 'n': 15, 'rows_all': 15}}

    def tamper(name, mutate):
        _fake_art_dir(tmp_path)          # 每轮从仓内工件重新拷, 上一轮篡改不泄漏
        p = os.path.join(art, name)
        obj = json.load(open(p, encoding='utf-8'))
        mutate(obj)
        json.dump(obj, open(p, 'w', encoding='utf-8'), ensure_ascii=False)

    tamper('m5h_arrows_P08.json',
           lambda o: o['meta']['rows_md5'].update({'m5_P08_rows.json': 'deadbeef'}))
    with pytest.raises(AssertionError, match='证据过期'):
        m.build()

    tamper('m5h_arrows_P08.json', lambda o: o['meta']['rows_md5'].pop('m5_P08_rows.json'))
    with pytest.raises(AssertionError, match='证据过期'):
        m.build()

    tamper('m5h_arrows_P08.json', lambda o: o['rows'].pop(3))
    with pytest.raises(AssertionError, match='行数'):
        m.build()

    tamper('m5h_arrows_P09.json', lambda o: o['rows'][0].update(kind='口胡的类别'))
    with pytest.raises(AssertionError, match='不对齐'):
        m.build()


def test_arrow_rule_downgrades_unproven_rows_only():
    """§11 通用规则本身: 只降状态、不动数量、不升档。

    基线用“假装无末端标记证据”(dist=None)的存枛2 build() 得出 —— 即规则关掉。
    封三件事: (a) 总账/每个(专业,类别,房间)桶的 Σn 一字不动;
    (b) 箭头未命中只能把已确认降为待核(不会凭空造待核, 也不会反向升档);
    (c) 有箭头凭证但因撞车/补账仍待核的行 → 只进不升档披露清单, 不自动洗白。"""
    m = _wb()
    data = m.build()
    saved = m._arrow_evidence
    m._arrow_evidence = lambda tag, rows: ([{'dist': None, 'hit': False, 'form': None}
                                            for _ in rows], {'stub': True})
    try:
        off = m.build()
    finally:
        m._arrow_evidence = saved

    assert data['totals'] == off['totals']
    assert _bucket(data) == _bucket(off), '箭头规则不得改变任何房间桶的数量'
    assert sum(r[4] for r in data['sheet1']) == sum(r[4] for r in off['sheet1']) == 205

    why = m.WHY_NO_ARROW
    down = [r for r in data['sheet1'] if why in r[7]]
    assert len(down) == 14 and sum(r[4] for r in down) == 17
    assert not [r for r in off['sheet1'] if why in r[7]], '规则关掉时不应有该降级'
    # 只降不升: 每一条降级行所在的(kind,room) 在基线里必须还有"未挂归属疑问"的行可供降
    upgradable = ('已确认', '落域·房间名待核')
    for r in down:
        assert any(o[0:3] == r[0:3] and o[7] in upgradable for o in off['sheet1']), \
            f'无中生有的降级: {r[:3]} {r[7]}'
    _st = lambda d, p: sum(1 for r in d['sheet1'] if r[7].startswith(p))
    assert (_st(off, '已确认'), _st(data, '已确认')) == (47, 36), '已确认只能减'
    # 四态优先级: 归属待核 > 房间名待核 → 2 行原"落域·房间名待核"被箭头规则接管(原因更具体)
    assert (_st(off, '落域·房间名待核'), _st(data, '落域·房间名待核')) == (3, 1)
    # 不升档: 规则生效后的已确认(kind,room)集合只能是规则关掉时的子集
    conf = {(r[0], r[1], r[2]) for r in data['sheet1'] if r[7] == '已确认'}
    assert conf <= {(r[0], r[1], r[2]) for r in off['sheet1'] if r[7] == '已确认'}

    tol = data['arrow']['meta']['P08']['hit_tol_pt']
    assert tol == data['arrow']['meta']['P09']['hit_tol_pt'] == 1.5
    assert data['arrow']['n_noarrow_downgraded'] == 16   # =状态真的变了的行数(不含挂账行)
    assert len(data['sheet4_hitpend']) == 24
    for label, room, dist_form, reason in data['sheet4_hitpend']:
        assert label.split()[0] in ('P08', 'P09') and '(未能' not in room
        assert reason != why and '待核' not in reason, '不升档清单只列因其他证据待核的行'
        assert float(dist_form.split('pt')[0]) < tol, dist_form
        assert reason in (m.WHY_COLLIDE, m.WHY_SUPP_LEADER), reason


def test_arrow_artifact_is_self_consistent():
    """普查工件可独立复算: 距离能由 marks 重算出来, hit 与容差一致。

    防的是手工改工件蒙过构建器; 也顺手定住 §8.5 的形态计数(改算法必须同步改本节)。"""
    from app.orchestrator.mep import arrows
    expect = {'P08': {'forms': {'F1': 92, 'F2': 2, 'F3': 196}, 'n_triangles': 46, 'n_marks': 287},
              'P09': {'forms': {'F1': 28, 'F2': 49, 'F3': 293}, 'n_triangles': 14, 'n_marks': 371}}
    for tag, want in expect.items():
        art = json.load(open(os.path.join(MEP_ART, f'm5h_arrows_{tag}.json'), encoding='utf-8'))
        meta = art['meta']
        assert meta['hit_tol_pt'] == arrows.HIT_TOL_PT
        for k, v in want.items():
            assert meta[k] == v, f'{tag} 普查形态计数漂移({k}): {meta[k]} != {v} → 重跑并同步 §8.5'
        assert len(art['marks']) == meta['n_marks']
        assert sum(1 for k, _x, _y, _s in art['marks'] if k == 'tri') == meta['n_triangles']
        assert meta['n_triangles'] < meta['n_marks']
        marks = [(k, (x, y), s) for k, x, y, s in art['marks']]
        for r in art['rows']:
            if not r.get('endpoint'):
                assert not r.get('hit')
                continue
            d, _form = arrows.nearest(marks, tuple(r['endpoint']))
            assert d == r['dist_pt'], f'{tag} 距离不可复算: 重算{d} vs 记录{r["dist_pt"]}'
            assert r['hit'] is bool(d < meta['hit_tol_pt'])


def _bucket(data):
    """(专业,类别,房间) -> Σ数量, 用于证明裁定只改状态不改量。"""
    g = {}
    for r in data['sheet1']:
        g[(r[0], r[1], r[2])] = g.get((r[0], r[1], r[2]), 0) + r[4]
    return g
