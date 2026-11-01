import os
import shutil
import sys
from types import SimpleNamespace

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)

from tests.test_takeoff_web import web_env  # noqa: E402,F401


def test_auto_fill_rows_have_no_quantities_and_keep_totals(web_env):
    app, pid, _ = web_env
    with app.app_context():
        from app import db
        from app.comparison import auto_fill_missing
        from app.core.takeoff import clear_previous_measurements
        from app.models import Measurement, Project

        db.session.add(Measurement(
            label="客厅-地面", division_id=1, category="dxf_floor",
            area=10.0, unit="m²", source_tag="图纸实测",
            confidence_level="high", project_id=pid))
        db.session.commit()

        items = [{"division": 6, "name": "主卧衣柜", "unit": "个",
                  "material": "衣柜", "capability": "furniture"}]
        filled = auto_fill_missing(pid, items)
        assert len(filled) == 1
        m = filled[0]
        assert m.area is None and m.length is None and m.count is None
        assert m.category == "template_gap"
        assert m.source_tag == "待确认"
        assert m.confidence_level == "low"
        assert m.unit == "个"

        floor = sum(x.area or 0 for x in Measurement.query.filter_by(
            project_id=pid, division_id=1).all())
        assert floor == 10.0

        assert auto_fill_missing(pid, items) == []       # 幂等

        clear_previous_measurements(Project.query.get(pid))
        assert Measurement.query.filter_by(
            project_id=pid, category="template_gap").count() == 1


def test_has_current_requires_division_material_and_unit():
    from app.comparison import _has_current

    rows = [SimpleNamespace(division_id=2, unit="m²", label="客厅-墙面瓷砖",
                            project_features="")]
    assert _has_current(rows, 2, "瓷砖", "m2") is True
    assert _has_current(rows, 2, "瓷砖", "m") is False      # 单位不同
    assert _has_current(rows, 1, "瓷砖", "m2") is False     # 分部不同
    assert _has_current(rows, 2, "石材", "m2") is False     # 材料不同


def test_has_current_skips_unit_check_for_composite_division_units():
    from app.comparison import _has_current

    rows = [SimpleNamespace(division_id=7, unit="个", label="卫生间-面盆龙头",
                            project_features="", category="dxf_mep")]
    assert _has_current(rows, 7, "龙头", "套/个") is True


def test_has_current_ignores_template_gap_placeholders():
    from app.comparison import _has_current

    rows = [SimpleNamespace(division_id=2, unit="m²", label="墙面瓷砖",
                            project_features="瓷砖", category="template_gap")]
    assert _has_current(rows, 2, "瓷砖", "m2") is False


def test_no_material_token_is_manual_review(web_env):
    import json
    app, pid, _ = web_env
    with app.app_context():
        from app import db
        from app.comparison import compare_with_historical
        from app.models import HistoricalProject, User

        admin = User.query.filter_by(is_admin=True).first()
        hist = HistoricalProject(
            name="历史样例", user_id=admin.id,
            summary_json=json.dumps({
                "stats": {"total_items": 1,
                          "division_items": {"2": ["某某无法识别列项"]}}}))
        db.session.add(hist)
        db.session.commit()

        result = compare_with_historical(pid, hist.id)
        assert result["missing_items"] == []
        assert [i["name"] for i in result["manual_review_items"]] == ["某某无法识别列项"]


def test_missing_items_carry_no_fabricated_quantity(web_env):
    import json
    app, pid, _ = web_env
    with app.app_context():
        from app import db
        from app.comparison import compare_with_historical
        from app.models import HistoricalProject, User

        admin = User.query.filter_by(is_admin=True).first()
        hist = HistoricalProject(
            name="历史样例2", user_id=admin.id,
            summary_json=json.dumps({
                "stats": {"total_items": 1,
                          "division_items": {"2": ["墙面瓷砖"]}}}))
        db.session.add(hist)
        db.session.commit()

        result = compare_with_historical(pid, hist.id)
        assert [i["name"] for i in result["missing_items"]] == ["墙面瓷砖"]
        assert all("historical_quantity" not in i for i in result["missing_items"])


def test_historical_audit_surfaces_manual_review(web_env):
    import json
    app, pid, _ = web_env
    with app.app_context():
        from app import db
        from app.models import HistoricalProject, Project, User
        from app.orchestrator.audit import historical_audit

        admin = User.query.filter_by(is_admin=True).first()
        hist = HistoricalProject(
            name="历史样例3", user_id=admin.id, project_id=pid,
            summary_json=json.dumps({
                "stats": {"total_items": 1,
                          "division_items": {"7": ["某某无法识别列项"]}}}))
        db.session.add(hist)
        db.session.commit()

        rep = historical_audit(Project.query.get(pid))
        assert rep["manual_review_count"] == 1
        assert rep["manual_review_items"][0]["name"] == "某某无法识别列项"
