"""Task 6：围合算法搬出 modeling.base_extract，纯几何模块的回归测试。

夹具为三间独立房间（墙厚 200 双线墙）。手算面积口径（与 227 封账同一套栅格判据）：
净空 = 双线墙内面之间，再扣掉栅格线宽 180 的内缩 —— 180mm 线宽在 20mm 网格上
实测每面内缩 110mm（半线宽 90 + 一格取整 20），故净面积 = (w-220)*(h-220)。
名义尺寸 4000×3000 的客厅，画出来实际净空是 4000×2800（上墙 200 向内），
手算 9.75 而非计划伪代码里的 12.00；计划那组 12/9/4/25 与夹具几何不自洽。
"""
from types import SimpleNamespace

import ezdxf
import numpy as np
import pytest

from app.core.takeoff.enclosure import build_rooms, detect_leaks
from app.core.takeoff.layers import resolve_roles
from app.core.takeoff.types import TakeoffParams

KEYS = {"bounds", "res", "V", "rooms", "walls_strict", "enc", "doors", "windows",
        "seals", "grid", "lab", "n", "border", "anchors", "unplaced_anchors", "counts"}
# 栅格内缩：180mm 线宽 / 20mm 网格 → 每面 110mm（见模块 docstring）
INSET_MM = 110


def _net(w, h):
    """双线墙净空 w×h（mm）→ 栅格围合后的房间净面积（m2）。"""
    return (w - 2 * INSET_MM) * (h - 2 * INSET_MM) / 1e6


def _plan(tmp_path):
    """三间独立房间，墙厚 200；双线墙内面净空 4000×2800 / 3000×2800 / 2000×1800。

    （计划伪代码注释写 4000×3000，把上墙 200 算丢了；见模块 docstring。）
    """
    doc = ezdxf.new("R2018", setup=True)
    for n in ("WALL", "P-TEXT", "DOOR"):
        doc.layers.add(n)
    msp = doc.modelspace()

    def box(x0, y0, w, h):
        for yy in (y0, y0 + h):
            msp.add_line((x0, yy), (x0 + w + 400, yy), dxfattribs={"layer": "WALL"})
            msp.add_line((x0, yy + 200), (x0 + w + 400, yy + 200), dxfattribs={"layer": "WALL"})
        for xx in (x0, x0 + 200, x0 + w + 200, x0 + w + 400):
            msp.add_line((xx, y0), (xx, y0 + h + 400), dxfattribs={"layer": "WALL"})

    box(0, 0, 4000, 3000)          # 9.75 m²（净空 4000×2800）
    box(6000, 0, 3000, 3000)       # 7.17 m²（净空 3000×2800）
    box(11000, 0, 2000, 2000)      # 2.81 m²（净空 2000×1800）
    msp.add_text("客厅", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((2000, 1500))
    msp.add_text("卧室", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((7500, 1500))
    msp.add_text("卫生间", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((12000, 1000))
    msp.add_line((1000, 0), (1900, 0), dxfattribs={"layer": "DOOR"})
    p = tmp_path / "three.dxf"
    doc.saveas(p)
    return ezdxf.readfile(p)


def test_three_rooms_area_matches_hand_calc(tmp_path):
    doc = _plan(tmp_path)
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800.0))
    inn = [r for r in data["rooms"] if not r["is_out"]]
    assert len(inn) == 3
    # 夹具净空：客厅 4000×2800 / 卧室 3000×2800 / 卫生间 2000×1800
    expect = {"客厅": _net(4000, 2800), "卧室": _net(3000, 2800), "卫生间": _net(2000, 1800)}
    assert abs(sum(r["area"] for r in inn) - sum(expect.values())) <= 0.05
    by_name = {"".join(r["names"]): r["area"] for r in inn}
    for name, area in expect.items():
        assert abs(by_name[name] - area) <= 0.05, (name, by_name[name], area)


def test_leak_detected_when_room_opens_to_border(tmp_path):
    doc = _plan(tmp_path)
    msp = doc.modelspace()
    # 卫生间上墙两线在 y=2000/2200（不是 2400）；缺口 2000 ≤ GMAX 2200，
    # 但 2000 > AUTO 1200，没有门邻触发膨胀桥接，所以仍漏向 border。
    for e in list(msp.query('LINE[layer=="WALL"]')):
        s, t = e.dxf.start, e.dxf.end
        if abs(s.y - t.y) < 1 and 1999 <= s.y <= 2201 and min(s.x, t.x) >= 11000:
            e.destroy()
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams())
    leaks = detect_leaks(data)
    assert "卫生间" in leaks
    assert "客厅" not in leaks and "卧室" not in leaks
    # border 断言要落在具体锚点上：卫生间标签所在像素确属贴边连通域
    x, y, _ = next(a for a in data["anchors"] if a[2] == "卫生间")
    gx = int(round((x - data["bounds"][0]) / data["res"]))
    gy = int(round((data["bounds"][3] - y) / data["res"]))
    assert int(data["lab"][gy, gx]) in data["border"]


def test_build_rooms_keys_and_counts(tmp_path):
    doc = _plan(tmp_path)
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800.0))
    assert set(data) >= KEYS
    assert data["V"]["clear_to_soffit"] == 2800.0
    assert len(data["seals"]) == 0
    assert set(data["counts"]) == {"wall_solid", "FIN_axial", "FIN_part",
                                   "door", "window", "curb"}
    # 3 个盒子 × 8 条双线墙 = 24；DOOR 1 条；其余角色 0
    assert data["counts"] == {"wall_solid": 24, "FIN_axial": 0, "FIN_part": 0,
                              "door": 1, "window": 0, "curb": 0}


def test_detect_leaks_empty_when_closed(tmp_path):
    doc = _plan(tmp_path)
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800.0))
    assert detect_leaks(data) == []
    assert data["unplaced_anchors"] == []


def test_detect_leaks_skips_out_keys_at_same_pixel():
    """同一 border 像素上的井道名不报漏房，真实房间名照报。"""
    lab = np.ones((4, 4), np.int32) * 7
    data = {"bounds": (0, 0, 60, 60), "res": 20.0, "lab": lab, "border": {7},
            "anchors": [(0, 60, "风井"), (0, 60, "卫生间")]}
    assert detect_leaks(data) == ["卫生间"]


@pytest.mark.parametrize("anchor", [(1000, 500, "边界"), (500, 0, "边界")])
def test_boundary_anchor_does_not_crash_and_reports_leak(anchor):
    """x==X1 / y==Y0 的锚点取像素会越界一格；clamp 后仍参与漏房判定。"""
    roles = SimpleNamespace(segments={}, labels=[anchor])
    data = build_rooms(None, roles, (0, 0, 1000, 1000), TakeoffParams())
    assert detect_leaks(data) == ["边界"]


def test_anchor_on_wall_line_goes_to_unplaced():
    """压在墙线上的标签不再 IndexError，也不再静默消失：进 unplaced_anchors。"""
    walls = [(0, 0, 1000, 0), (1000, 0, 1000, 1000),
             (1000, 1000, 0, 1000), (0, 1000, 0, 0)]
    roles = SimpleNamespace(segments={"wall_solid": walls}, labels=[(1000, 500, "压墙")])
    data = build_rooms(None, roles, (0, 0, 1000, 1000), TakeoffParams())
    assert (1000, 500, "压墙") in data["unplaced_anchors"]


@pytest.mark.parametrize("bounds", [None, (0, 0, 0, 100), (0, 0, 100, 0), (100, 0, 0, 100)])
def test_invalid_bounds_raise_value_error(bounds):
    roles = SimpleNamespace(segments={}, labels=[])
    with pytest.raises(ValueError):
        build_rooms(None, roles, bounds, TakeoffParams())


def test_minibar_without_master_stays_unplaced(tmp_path):
    """没有「主人卧室」时 Minibar 不得假装并入：该锚点进 unplaced_anchors。"""
    doc = ezdxf.new("R2018", setup=True)
    for n in ("WALL", "P-TEXT"):
        doc.layers.add(n)
    msp = doc.modelspace()

    def box(x0, y0, w, h):          # 单线墙盒
        pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            msp.add_line(a, b, dxfattribs={"layer": "WALL"})

    box(0, 0, 4000, 3000)           # 大房 ≈10.5 m²，含「客厅」
    # 小屋 600×600：180 线宽 + CLOSE×2 后净空 ≈420×420 ≈0.18 m² < 0.4 未成房。
    # （300×300 的内腔会被 180 线宽整个吃掉，锚点落墙线 → 测不到 Minibar 分支。）
    box(6000, 0, 600, 600)
    msp.add_text("客厅", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((2000, 1500))
    msp.add_text("Minibar", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((6300, 300))
    p = tmp_path / "minibar.dxf"
    doc.saveas(p)

    doc = ezdxf.readfile(p)
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800.0))
    assert [r["names"] for r in data["rooms"]] == [["客厅"]]
    assert (6300, 300, "Minibar") in data["unplaced_anchors"]


def test_minibar_merge_does_not_inflate_master_area(tmp_path):
    """Minibar 只做标签归属、不加净面积（227 封账：主卧 17.15，不是 17.32）。"""
    def build(path, with_minibar):
        doc = ezdxf.new("R2018", setup=True)
        for n in ("WALL", "P-TEXT"):
            doc.layers.add(n)
        msp = doc.modelspace()

        def box(x0, y0, w, h):
            pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
            for a, b in zip(pts, pts[1:] + pts[:1]):
                msp.add_line(a, b, dxfattribs={"layer": "WALL"})

        box(0, 0, 5000, 4000)
        box(6000, 0, 600, 600)
        msp.add_text("主人卧室", height=250,
                     dxfattribs={"layer": "P-TEXT"}).set_placement((2500, 2000))
        if with_minibar:
            msp.add_text("Minibar", height=250,
                         dxfattribs={"layer": "P-TEXT"}).set_placement((6300, 300))
        doc.saveas(path)
        doc = ezdxf.readfile(path)
        roles = resolve_roles(doc)
        return build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800.0))

    plain = build(tmp_path / "plain.dxf", False)
    merged = build(tmp_path / "merged.dxf", True)
    master = lambda d: [r for r in d["rooms"] if r["names"] == ["主人卧室"]][0]
    assert master(merged)["area"] == master(plain)["area"]
    labels = master(merged)["merged_labels"]
    assert [m["label"] for m in labels] == ["Minibar"]
    assert labels[0]["area"] == pytest.approx(0.14, abs=0.02)
    assert merged["unplaced_anchors"] == []
