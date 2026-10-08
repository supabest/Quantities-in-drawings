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
M5_ROWS = os.path.join(ROOT, "outputs", "preview", "vlm", "mep", "m5_{tag}_rows.json")

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
def test_mep_workbook_identities(tmp_path):
    """组装器恒等: sheet合计≡封账json; build() 内断言不破账; xlsx 渲染冒烟。
    纪律: 本用例封住"M2~M5封账数字接入计价/BOQ"链路的数值同一性——
    任何封账工件被改动而组装器未同步, 此处即红。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'build_mep_workbook',
        os.path.join(ROOT, 'scripts', 'build_mep_workbook.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    data = m.build()   # 内部任一 AssertionError = 破账
    assert data['totals'] == {'P08': 86, 'P09': 24, 'hvac': 18, 'elv': 23,
                              'sprinkler': 33, 'control_wires': 93}
    # ①数量列合计 = 各封账之和(184 口径: 86+24+18+23+33, 烟感0不计)
    assert sum(r[4] for r in data['sheet1']) == 86 + 24 + 18 + 23 + 33
    # ④对账表每行差值恒为 0
    assert all(r[1] == r[2] for r in data['sheet4_recon'])
    out = str(tmp_path / 'wb_test.xlsx')
    m.write_xlsx(data, out)
    assert os.path.exists(out) and os.path.getsize(out) > 5000
