import json
import os

from app.core.takeoff import template
from app.core.takeoff.types import Coverage

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tpl_dir(tmp_path, items, ranges=None):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "sample_flat.json").write_text(json.dumps({
        "type": "sample_flat", "version": 1, "items": items,
        "ranges": ranges or {"wall_floor_ratio": [2.0, 3.0],
                             "baseboard_perimeter": [0.6, 1.1]},
    }, ensure_ascii=False), encoding="utf-8")
    return d


def _item(**kwargs):
    base = {"id": "1:地面:m2", "division": 1, "name": "地面", "unit": "m2",
            "material": None, "capability": "floor_area", "group_only": False}
    base.update(kwargs)
    return base


def _row(div, label="行", value=1.0, unit="m2", category="dxf_floor", source="图纸实测"):
    return {"division_id": div, "label": label, "value": value, "unit": unit,
            "category": category, "source_tag": source}


def _cov(*pairs):
    cov = Coverage()
    for space, part, status in pairs:
        cov.mark(space, part, status, "dxf")
    return cov


def test_audit_true_miss_when_part_has_source(tmp_path):
    d = _tpl_dir(tmp_path, [_item()])
    audit, issues = template.audit([], _cov(("客厅", "地面", "有源")),
                                   "sample_flat", templates_dir=d)
    assert audit["counts"]["missing"] == 1
    assert audit["missing"][0]["reason"] == "真漏算"
    assert [i.code for i in issues] == ["template_item_missing"]
    assert issues[0].level == "warn"


def test_audit_no_source_when_coverage_empty(tmp_path):
    d = _tpl_dir(tmp_path, [_item()])
    audit, issues = template.audit([], _cov(), "sample_flat", templates_dir=d)
    assert audit["counts"]["no_source"] == 1
    assert audit["missing"] == []
    assert [i.code for i in issues] == ["template_no_source"]
    assert issues[0].level == "info"


def test_audit_present_when_division_row_exists(tmp_path):
    d = _tpl_dir(tmp_path, [_item()])
    audit, issues = template.audit([_row(1)], _cov(("客厅", "地面", "有源")),
                                   "sample_flat", templates_dir=d)
    assert audit["counts"]["present"] == 1
    assert audit["counts"]["missing"] == 0
    assert issues == []


def test_group_only_is_skipped(tmp_path):
    d = _tpl_dir(tmp_path, [_item(group_only=True)])
    audit, issues = template.audit([], _cov(), "sample_flat", templates_dir=d)
    assert audit["counts"]["group_only"] == 1
    assert audit["counts"]["checked"] == 0
    assert issues == []


def test_excluded_item_is_not_missing(tmp_path):
    d = _tpl_dir(tmp_path, [_item(id="1:地面:m2")])
    audit, issues = template.audit([], _cov(), "sample_flat",
                                   excluded={"1:地面:m2"}, templates_dir=d)
    assert audit["counts"]["excluded"] == 1
    assert audit["counts"]["missing"] == 0
    assert [i.code for i in issues] == ["template_item_excluded"]


def test_gap_rows_have_no_quantities(tmp_path):
    d = _tpl_dir(tmp_path, [_item(id="6:衣柜:个", division=6, name="衣柜",
                                  unit="个", capability="furniture")])
    audit, _ = template.audit([], _cov(), "sample_flat", templates_dir=d)
    rows = template.gap_rows(audit)
    assert len(rows) == 1
    assert rows[0].value is None
    assert rows[0].category == "template_gap"
    assert rows[0].source_tag == "待确认"
    assert rows[0].confidence_level == "low"


def test_evaluate_ranges_uses_perimeter_denominator():
    totals = {"floor_m2": 8.0, "wall_m2": 25.0,
              "baseboard_m": 10.0, "perimeter_m": 16.0}
    # 样板房带 2.0~3.0（templates/sample_flat.json）：3.12 越带、0.62 落带。
    # 售楼部带 2.5~3.5 会把 3.12 算成落带，与本用例「越带要报」的断言矛盾，
    # 且该带值被 Task 3 的 test_reconcile_band_respects_project_type 锁定，故取 sample_flat。
    rows, issues = template.evaluate_ranges(totals, "sample_flat")
    by = {r["code"]: r for r in rows}
    assert by["wall_floor_ratio"]["value"] == 3.12
    assert by["baseboard_ratio"]["value"] == 0.62
    assert by["baseboard_ratio"]["in_band"] is True
    assert [i.code for i in issues] == ["wall_floor_ratio"]


def test_evaluate_ranges_skips_density_without_rows():
    rows, issues = template.evaluate_ranges(
        {"floor_m2": 10.0, "wall_m2": 25.0, "baseboard_m": 8.0,
         "perimeter_m": 10.0}, "sample_flat")
    assert {r["code"] for r in rows} == {"wall_floor_ratio", "baseboard_ratio"}
    assert issues == []


def test_sample_flat_ranges_come_from_json():
    assert template.ranges("sample_flat")["wall_floor_ratio"] == [2.0, 3.0]
    assert template.ranges("unknown_type")["wall_floor_ratio"] == [1.5, 4.0]
