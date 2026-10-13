# -*- coding: utf-8 -*-
"""tests/test_decoration_delivery.py — 装修交付物契约：工程量计算表(含备注列) + Web 打通。

分两层(与 test_takeoff_golden.py 同构)：
  Tier A  零图纸、CI 可跑：导出列契约、路由注册与登录保护、备注规则的静态完整性。
  Tier B  @slow 且需图纸：主表逐行必须"交代得清"——恒等断言、每行有备注、
          低置信必带需补资料、规则不得空转、口径类规则必须覆盖该口径的**全部**行。

纪律：断言只认"行内容"而非行号，改口径必须显式改本文件，防止表与测试各说各话。
"""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
DRAWINGS = os.path.join(ROOT, "装修图纸")


# ─────────────────────────────── Tier A：零图纸
@pytest.fixture()
def flask_app(tmp_path):
    """强制走临时库：不用 env 传递(conda/已 import config 时 env 不生效，
    会误写仓库里的 app.db)。"""
    from app import create_app, db
    from config import Config

    class TestConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp_path / "t.db")
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app


def _mk_measurement(db, project_id, **kw):
    from app.models import Measurement
    base = dict(project_id=project_id, label="列项", unit="m²", area=1.0,
                confidence_level="high", source_tag="图纸实测", source_tag_icon="📐",
                remarks="无", data_json="{}")
    base.update(kw)
    return Measurement(**base)


def _seed_project(db):
    from app.models import Project, User
    u = User(username="t", email="t@t", is_admin=True)
    u.set_password("x")
    p = Project(name="P", user_id=0, status="completed")
    db.session.add_all([u, p])
    db.session.commit()
    p.user_id = u.id
    db.session.commit()
    return u, p


def test_export_workbook_carries_remark_and_need_columns(flask_app):
    """导出表必须有「备注(不确定原因)」与「需补充的资料」两列，且低置信行进待确认清单。"""
    from app import db
    from app.export import HEADERS, build_workbook
    _, p = _seed_project(db)
    ms = [
        _mk_measurement(db, p.id, confidence_level="low", remarks="待核：开口性质未定",
                        data_json=json.dumps({"need": "需门窗表"}, ensure_ascii=False)),
        _mk_measurement(db, p.id, unit="m", area=None, length=12.5, confidence_level="medium",
                        remarks="未扣落地窗断点", source_tag="几何推算",
                        data_json=json.dumps({"need": "需窗台高度"}, ensure_ascii=False)),
        _mk_measurement(db, p.id, unit="个", area=None, count=3),
    ]
    db.session.add_all(ms)
    db.session.commit()

    buf = build_workbook(p, ms)
    assert buf is not None
    from openpyxl import load_workbook
    wb = load_workbook(buf)
    ws = wb["工程量计算表"]
    hdr = [c.value for c in ws[1]]
    assert hdr == HEADERS, "表头漂移即交付口径变了: %s" % hdr
    assert "备注(不确定原因)" in hdr and "需补充的资料" in hdr
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(rows) == 3
    # 三制取数：m²→area、m→length、个→count，放错列等于错量
    q = {r[7]: r[6] for r in rows}
    assert q["m²"] == 1.0 and q["m"] == 12.5 and q["个"] == 3
    assert rows[1][11] == "需窗台高度", "medium 行的需补资料同样必须出列"
    assert rows[0][10] == "待核：开口性质未定", "备注列必须原样带出不确定原因"
    pend = list(wb["待确认清单"].iter_rows(min_row=2, values_only=True))
    assert len(pend) == 2, "low+medium 应入待确认清单(high 不入)"
    assert pend[0][8] == "低", "待确认清单必须低置信在前"


def test_export_route_registered_and_login_guarded(flask_app):
    """/project/<id>/export.xlsx 必须存在且未登录不可下载。"""
    with flask_app.app_context():
        from app import db
        rules = {r.rule for r in flask_app.url_map.iter_rules()}
        assert "/project/<int:project_id>/export.xlsx" in rules
        _, p = _seed_project(db)
        db.session.add(_mk_measurement(db, p.id))
        db.session.commit()
        pid = p.id                      # commit 后会 expire，先在 session 内取号
    c = flask_app.test_client()
    r = c.get("/project/%d/export.xlsx" % pid)
    assert r.status_code in (301, 302), "导出路由漏了 @login_required"
    assert "/auth/login" in r.headers["Location"]
    # 空项目 → 不崩(走 flash 回详情页)
    with flask_app.app_context():
        from app.models import Measurement
        Measurement.query.delete()
        db.session.commit()
    c.post("/auth/login", data={"email": "t@t", "password": "x"})
    r = c.get("/project/%d/export.xlsx" % pid, follow_redirects=True)
    assert r.status_code == 200


def test_remark_rules_are_self_describing():
    """备注规则必须结构完整：文案里不许留未格式化的 %s，置信度必须取三档之一。"""
    import build_decoration_workbook as W
    for ru in W.REMARK_RULES:
        assert ru["code"] and ru["text"] and callable(ru["applies"])
        assert ru["conf"] in ("high", "medium", "low"), ru["code"]
        assert "%s" not in ru["text"] and "%d" not in ru["text"], \
            "%s 文案留了未替换的格式占位符" % ru["code"]
        if ru["conf"] == "low":
            assert ru["need"], "低置信规则必须写明需补资料: %s" % ru["code"]
    assert len({ru["code"] for ru in W.REMARK_RULES}) == len(W.REMARK_RULES)
    # 挂账清单每条也要有原因与状态，且不含"待补"字面量(防占位符残留)
    for h in W.HANGOVER:
        assert len(h) == 5 and h[3] and h[4], h
    codes = {ru["code"] for ru in W.REMARK_RULES}
    for h in W.HANGOVER:
        assert h[1] != "0" , "挂账项不应有零量条目: %s" % h[0]
    assert codes >= {"W1_OPENER_ASSUMED", "C1_CEIL_CALIBER", "P1_FIX_COUNT_NOT_GEOM"}


# ─────────────────────────────── Tier B：需图纸重跑
@pytest.mark.skipif(not os.path.isdir(DRAWINGS), reason="图纸未入库，仅本地可复算")
@pytest.mark.slow
def test_workbook_rows_match_seal_and_every_row_is_accounted():
    """72 行封账 + 恒等断言 + 逐行"交代得清"：无备注=显式"无"，低置信必带需补资料。"""
    import build_decoration_workbook as W
    rows, hits = W.collect_rows()
    m2, m, ge, _ = W.seal_checks(rows)
    assert len(rows) == W.SEAL["n_rows"]
    assert abs(m2 - W.SEAL["m2"]) < 0.05 and abs(m - W.SEAL["m"]) < 0.05
    assert ge == W.SEAL["ge"]
    for r in rows:
        assert r["remark"], "备注列不允许留空(不确定就写原因，确证无问题就写'无')"
        assert r["states"], "每行必须署名三态: %s/%s" % (r["section"], r["item"])
        if r["conf"] == "low":
            assert W.need_info(r), "低置信行必须写明需补什么: %s" % r["item"]
    # 规则不得空转(在真实行上)
    assert all(v > 0 for v in hits.values()), hits

    # 口径类规则必须覆盖该口径的**全部**行 —— 漏一行就是"部分行没披露口径"
    ceil = [r for r in rows if r["section"].startswith("天花")]
    assert len(ceil) == 12
    assert all("C1_CEIL_CALIBER" in r["codes"] for r in ceil), \
        "天棚全部行都受投影口径影响，漏标即漏披露: %s" % [
            r["part"] for r in ceil if "C1_CEIL_CALIBER" not in r["codes"]]
    wall_m2 = [r for r in rows if r["section"].startswith("墙面") and r["unit"] == "m2"]
    assert all("W1_OPENER_ASSUMED" in r["codes"] for r in wall_m2), "墙面 m² 行全部含洞口假设"
    skirt = [r for r in rows if r["item"] == "踢脚线"]
    assert len(skirt) == 12 and all("K1_SKIRT_NO_WIN_CUT" in r["codes"] for r in skirt)
    fix = [r for r in rows if r["section"].startswith("固定家具")]
    assert len(fix) == 20 and all("P1_FIX_COUNT_NOT_GEOM" in r["codes"] for r in fix)

    low = [r for r in rows if r["conf"] == "low"]
    assert low, "全表无低置信项 = 规则失效或口径已被悄悄收敛，须人工确认"
    # 电梯厅那行必须同时带假设与待核(历史遗留项 e5elevwin 的落点)
    elev = [r for r in wall_m2 if r["part"] == "电梯厅"]
    assert len(elev) == 1 and "W2_ELEV_WINDOW_PEND" in elev[0]["codes"]
    assert elev[0]["conf"] == "low"


@pytest.mark.skipif(not os.path.isdir(DRAWINGS), reason="图纸未入库，仅本地可复算")
@pytest.mark.slow
def test_seal_constants_track_workbook_not_vice_versa():
    """SEAL 表里的派生恒等式必须自洽：改封账必须同时改这几处，否则本测试炸。"""
    import build_decoration_workbook as W
    S = W.SEAL
    assert abs(S["ceil_m2"] - (S["floor_m2"] - S["ceil_anon_m2"])) < 0.01
    assert abs(S["wall_m2"] - (S["wall_gross_m2"] - S["wall_strip_out_m2"])) < 0.01
    assert abs(S["p3b_flood_m2"] + S["p3b_niche_m2"] + S["p3b_cut_m2"]
               - S["p3b_total_m2"]) < 0.01
    assert abs(S["cove_raw_m"] - S["cove_ruling_m"] - S["cove_sealed_m"]) < 0.01
    assert abs(S["m2"] - (S["floor_m2"] + S["wall_m2"] + S["ceil_m2"])) < 0.02, \
        "合计=m²分项之和(踢脚走 m、固装走件)"
    assert S["m"] == S["wall_m"] and S["ge"] == 21
