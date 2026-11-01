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
