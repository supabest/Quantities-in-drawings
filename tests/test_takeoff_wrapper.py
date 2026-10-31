"""Task 6 包装层：dxf 入口与 227 默认入口的数值等价与缺省值隔离。

默认入口（dxf=None）是封账链路：固定 227 窗口 + SEALS + 结构层高 3170。
显式 dxf 入口不得把这两样 227 专属数据注入别的图；显式传入时栅格对齐后
必须与默认入口逐间同值（163.92，16 间户内）。
"""
import ezdxf
import pytest

from app.core.takeoff.enclosure import detect_leaks
from app.core.takeoff.types import TakeoffParams
from modeling import base_extract as B


@pytest.fixture(scope="module")
def default_data():
    return B.build()


@pytest.fixture(scope="module")
def dxf_data():
    return B.build(
        dxf=B.DXF, seals=B.SEALS,
        params=TakeoffParams(floor_height_mm=3170, slab_mm=100, floor_finish_mm=50))


def _room_sig(r):
    return tuple(sorted(r["names"])), r["area"]


def _rooms_by_name(data):
    return sorted(data["rooms"], key=lambda r: (sorted(r["names"]), r["area"]))


def test_default_bounds_and_params_visible(default_data):
    assert default_data["bounds"] == (B.X0, B.Y0, B.X1, B.Y1)
    assert default_data["V"] == B.V
    assert default_data["V"]["clear_to_soffit"] == 3020.0


def test_dxf_entry_matches_default_numerically(default_data, dxf_data):
    assert sorted(map(_room_sig, default_data["rooms"])) == \
        sorted(map(_room_sig, dxf_data["rooms"]))
    for a, b in zip(_rooms_by_name(default_data), _rooms_by_name(dxf_data)):
        assert a["cx"] == pytest.approx(b["cx"], abs=1e-3)
        assert a["cy"] == pytest.approx(b["cy"], abs=1e-3)

    inn = [r for r in dxf_data["rooms"] if not r["is_out"]]
    assert len(inn) == 16
    assert round(sum(r["area"] for r in inn), 2) == 163.92

    assert dxf_data["counts"] == default_data["counts"]
    assert detect_leaks(default_data) == []
    assert detect_leaks(dxf_data) == []


def test_empty_drawing_raises_no_geometry(tmp_path):
    path = tmp_path / "empty.dxf"
    ezdxf.new().saveas(path)
    with pytest.raises(ValueError, match="no_geometry"):
        B.build(dxf=str(path))


def test_dxf_entry_does_not_inherit_227(tmp_path):
    """无底图专属入参时，dxf 入口不带 227 垭口，也不用 3170 层高。"""
    doc = ezdxf.new("R2018", setup=True)
    for name in ("WALL", "P-TEXT"):
        doc.layers.add(name)
    msp = doc.modelspace()
    for y in (0, 200):
        msp.add_line((0, y), (3000, y), dxfattribs={"layer": "WALL"})
    for y in (2000, 2200):
        msp.add_line((0, y), (3000, y), dxfattribs={"layer": "WALL"})
    for x in (0, 200, 2800, 3000):
        msp.add_line((x, 0), (x, 2200), dxfattribs={"layer": "WALL"})
    msp.add_text("客厅", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((1500, 1100))
    path = tmp_path / "tiny.dxf"
    doc.saveas(path)

    data = B.build(dxf=str(path))
    assert data["seals"] == []
    assert data["V"]["story_height"] == 2800.0
    assert data["V"]["clear_to_soffit"] == 2800.0
