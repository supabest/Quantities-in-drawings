"""Task 6 包装层：dxf 入口与 227 默认入口的数值等价与缺省值隔离。

默认入口（dxf=None）是封账链路：固定 227 窗口 + SEALS + 结构层高 3170。
显式 dxf 入口不得把这两样 227 专属数据注入别的图，也不得读 227 的吊顶标高
（soffit 一律兜底结构净高）；显式传入同口径层高后必须与默认入口逐间同值
（163.92，16 间户内）。
"""
import os

import ezdxf
import pytest

from app.core.takeoff.enclosure import detect_leaks
from app.core.takeoff.types import TakeoffParams
from modeling import base_extract as B

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAWINGS = os.path.join(ROOT, "装修图纸", "227户型dxf")
# 读 227 图的两个用例走 Tier B（图纸不入库，与 test_takeoff_golden.py 同约定）：
# 本地才跑，CI 用 -m "not slow" 排除。合成图/空图用例不标，且不使用 module 级
# 227 夹具（fixture 懒加载，不会在 CI 触发读图）。


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


@pytest.mark.skipif(not os.path.isdir(DRAWINGS), reason="图纸未入库，仅本地可复算")
@pytest.mark.slow
def test_default_bounds_and_params_visible(default_data):
    assert default_data["bounds"] == (B.X0, B.Y0, B.X1, B.Y1)
    assert default_data["V"] == B.V
    assert default_data["V"]["clear_to_soffit"] == 3020.0
    # 井道名（弱电井/消防水管井）天生不是房间，不进 unplaced
    assert default_data["unplaced_anchors"] == []


@pytest.mark.skipif(not os.path.isdir(DRAWINGS), reason="图纸未入库，仅本地可复算")
@pytest.mark.slow
def test_dxf_entry_matches_default_numerically(default_data, dxf_data):
    # V 必须同值（显式传同口径层高）；soffit_mm 不比较：dxf 入口暂不做吊顶标注
    # （ceiling_levels.annotate 硬编码读 227 图），T8 迁移通用标注后再对齐。
    assert dxf_data["V"] == default_data["V"]
    assert sorted(map(_room_sig, default_data["rooms"])) == \
        sorted(map(_room_sig, dxf_data["rooms"]))
    for a, b in zip(_rooms_by_name(default_data), _rooms_by_name(dxf_data)):
        assert a["cx"] == pytest.approx(b["cx"], abs=1e-3)
        assert a["cy"] == pytest.approx(b["cy"], abs=1e-3)
        assert a["poly"] == b["poly"]

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


def test_dxf_entry_does_not_inherit_227(tmp_path, monkeypatch):
    """无底图专属入参时，dxf 入口不带 227 垭口，也不用 3170 层高、不读 227 吊顶标高。"""
    from modeling import ceiling_levels

    # 护栏：dxf 入口一旦调用 227 标注就记一笔（annotate 硬读 227 图，与入参无关）
    calls = []
    monkeypatch.setattr(ceiling_levels, "annotate", lambda data: calls.append(data))

    doc = ezdxf.new("R2018", setup=True)
    for name in ("WALL", "P-TEXT", "CEILING-ANNO"):
        doc.layers.add(name)
    msp = doc.modelspace()
    for y in (0, 200):
        msp.add_line((0, y), (3000, y), dxfattribs={"layer": "WALL"})
    for y in (2000, 2200):
        msp.add_line((0, y), (3000, y), dxfattribs={"layer": "WALL"})
    for x in (0, 200, 2800, 3000):
        msp.add_line((x, 0), (x, 2200), dxfattribs={"layer": "WALL"})
    msp.add_text("客厅", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((1500, 1100))
    # 图内自带吊顶标高 +2950（> 默认 2800 净高）：dxf 入口不得据此抬高 soffit 或出负下沉
    msp.add_text("+2950", height=250,
                 dxfattribs={"layer": "CEILING-ANNO"}).set_placement((1500, 600))
    path = tmp_path / "tiny.dxf"
    doc.saveas(path)

    data = B.build(dxf=str(path))
    assert calls == []
    assert data["seals"] == []
    assert data["V"]["story_height"] == 2800.0
    assert data["V"]["clear_to_soffit"] == 2800.0
    assert [r["names"] for r in data["rooms"]] == [["客厅"]]
    for r in data["rooms"]:
        assert r["ceiling_levels"] == []
        assert r["soffit_mm"] == 2800.0
        assert r["ceiling_drop_mm"] == 0.0
        assert r["ceiling_min_mm"] == 2800.0
