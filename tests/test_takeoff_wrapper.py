"""Task 6 包装层：dxf 入口与 227 默认入口的数值等价与缺省值隔离。

默认入口（dxf=None）是封账链路：固定 227 窗口 + SEALS + 结构层高 3170。
显式 dxf 入口不得把这两样 227 专属数据注入别的图（seals/层高不继承）；
吊顶标高自 Task 8 起由 enclosure 按**传入 doc/bounds** 通用提取，dxf 入口
读的是本图自己的 +2xxx，不再是 227 的或一刀切兜底。显式传入同口径层高后
必须与默认入口逐间同值（含吊顶四字段）。
"""
import copy
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
    # V 必须同值（显式传同口径层高）；吊顶四字段自 T8 起也由 enclosure 按窗口
    # 通用提取，两入口同图同窗口 → 逐间全等（T6 handoff 验收点）。
    assert dxf_data["V"] == default_data["V"]
    assert sorted(map(_room_sig, default_data["rooms"])) == \
        sorted(map(_room_sig, dxf_data["rooms"]))
    for a, b in zip(_rooms_by_name(default_data), _rooms_by_name(dxf_data)):
        assert a["cx"] == pytest.approx(b["cx"], abs=1e-3)
        assert a["cy"] == pytest.approx(b["cy"], abs=1e-3)
        assert a["poly"] == b["poly"]
        for k in ("ceiling_levels", "soffit_mm", "ceiling_drop_mm", "ceiling_min_mm"):
            assert a[k] == b[k], (a["names"], k, a[k], b[k])

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


def test_dxf_entry_reads_its_own_ceiling_levels(tmp_path):
    """dxf 入口按本图提取吊顶标高：tiny 图内 +2950 必须被采纳，但不继承 227 参数。

    标高 > 默认净高 2800 时 soffit=2950、下沉 −150mm 是真实披露（图纸标高高于
    参数假设的结构净高），不 clamp。仍不得继承 227 的 SEALS / 3170 层高。
    """
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
    # 图内自带吊顶标高 +2950（> 默认 2800 净高）：按本图采纳，soffit/下沉如实披露
    msp.add_text("+2950", height=250,
                 dxfattribs={"layer": "CEILING-ANNO"}).set_placement((1500, 600))
    path = tmp_path / "tiny.dxf"
    doc.saveas(path)

    data = B.build(dxf=str(path))
    assert data["seals"] == []
    assert data["V"]["story_height"] == 2800.0
    assert data["V"]["clear_to_soffit"] == 2800.0
    assert [r["names"] for r in data["rooms"]] == [["客厅"]]
    for r in data["rooms"]:
        assert r["ceiling_levels"] == [2950.0]
        assert r["soffit_mm"] == 2950.0
        assert r["ceiling_drop_mm"] == -150.0
        assert r["ceiling_min_mm"] == 2950.0


@pytest.mark.skipif(not os.path.isdir(DRAWINGS), reason="图纸未入库，仅本地可复算")
@pytest.mark.slow
def test_ceiling_annotation_matches_modeling(default_data):
    """迁入 enclosure 的通用标注与 modeling.ceiling_levels 在 227 上逐间全等。

    独立实现互验：老实现走 shapely+硬读 227，新实现走射线法+按入参 doc/bounds。
    """
    from modeling import ceiling_levels

    rooms = copy.deepcopy(default_data["rooms"])
    ceiling_levels.annotate({"rooms": rooms, "V": default_data["V"]})
    a = _rooms_by_name(default_data)
    b = _rooms_by_name({"rooms": rooms})
    assert len(a) == len(b)
    assert len([r for r in a if not r["is_out"]]) == 16
    assert any(r["ceiling_levels"] for r in a), "227 应至少一间命中吊顶标高"
    for x, y in zip(a, b):
        assert sorted(x["names"]) == sorted(y["names"])
        for k in ("ceiling_levels", "soffit_mm", "ceiling_drop_mm", "ceiling_min_mm"):
            assert x[k] == y[k], (x["names"], k, x[k], y[k])
