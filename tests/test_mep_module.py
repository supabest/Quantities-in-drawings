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

from app.orchestrator.mep import counts  # noqa: E402
from app.orchestrator.mep.registration import K, load_index  # noqa: E402

PDF = os.path.join(ROOT, "装修图纸", "227户型dxf",
                   "广州珠实金融城住宅项目--227户型施工图纸.pdf")
ART = os.path.join(ROOT, "outputs", "preview", "vlm", "mep", "m2_attribution.json")

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
