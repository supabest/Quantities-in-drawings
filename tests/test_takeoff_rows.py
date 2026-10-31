"""Task 7：五分部出数（1 地面 / 2 墙面 / 3 天棚 / 4 洞口 / 5 踢脚）。

期望值取夹具栅格实测（见 tests/test_takeoff_enclosure.py 的 180mm 线宽内缩说明），
不是计划伪代码里的名义尺寸：
  客厅 9.75 m² / 周长 12.64；卧室 7.17 / 10.64；卫生间 2.81 / 6.64；
  地面/天棚合计 19.73；净墙 @2800 = 12.64×2.8−0.9×2.1 + 10.64×2.8 + 6.64×2.8 = 81.89；
  踢脚 = 12.64−0.9+10.64+6.64 = 29.02；洞口 1 樘门（宽 0.90）。
"""
from app.core.takeoff.enclosure import build_rooms
from app.core.takeoff.layers import resolve_roles
from app.core.takeoff.takeoff import rows_for_divisions
from app.core.takeoff.types import TakeoffParams
from tests.test_takeoff_enclosure import _plan


def _data(tmp_path):
    doc = _plan(tmp_path)
    roles = resolve_roles(doc)
    return build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800.0))


def _rows(tmp_path):
    return rows_for_divisions(_data(tmp_path), TakeoffParams(floor_height_mm=2800.0))[0]


def test_floor_and_ceiling_totals(tmp_path):
    rows = _rows(tmp_path)
    floor = [r for r in rows if r.division_id == 1]
    ceil = [r for r in rows if r.division_id == 3]
    assert len(floor) == 3 and len(ceil) == 3
    assert abs(sum(r.value for r in floor) - 19.73) <= 0.05
    assert abs(sum(r.value for r in ceil) - 19.73) <= 0.05
    assert all(r.unit == "m2" and "围合" in r.formula_text for r in floor + ceil)
    assert all(r.position_desc and "comp_id=" in r.position_desc for r in floor + ceil)


def test_wall_deducts_door_and_has_formula(tmp_path):
    rows = _rows(tmp_path)
    wall = [r for r in rows if r.division_id == 2]
    assert len(wall) == 3
    assert abs(sum(r.value for r in wall) - 81.89) <= 0.15
    living = [r for r in wall if "客厅" in r.label][0]
    assert abs(living.value - 33.50) <= 0.05
    assert "−" in living.formula_text
    assert "2.10" in living.formula_text
    assert "12.64" in living.formula_text
    assert "×" in living.formula_text
    assert "comp_id=" in living.position_desc
    assert living.formula_text.strip()


def test_baseboard_deducts_door(tmp_path):
    rows = _rows(tmp_path)
    bb = [r for r in rows if r.division_id == 5]
    assert len(bb) == 3
    assert all(r.unit == "m" for r in bb)
    assert abs(sum(r.value for r in bb) - 29.02) <= 0.15
    living = [r for r in bb if "客厅" in r.label][0]
    assert abs(living.value - (12.64 - 0.90)) <= 0.06


def test_openings_counted_by_tatami_unit(tmp_path):
    rows = _rows(tmp_path)
    openings = [r for r in rows if r.division_id == 4]
    assert len(openings) == 1
    assert all(r.unit == "樘" and r.value == 1 for r in openings)
    assert "0.90" in openings[0].formula_text
    assert "comp_id=" in openings[0].position_desc
