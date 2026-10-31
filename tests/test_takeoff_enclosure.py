"""Task 6：围合算法搬出 modeling.base_extract，纯几何模块的回归测试。

夹具为三间独立房间（墙厚 200 双线墙）。手算面积口径（与 227 封账同一套栅格判据）：
净空 = 双线墙内面之间，再扣掉栅格线宽 180 的内缩 —— 180mm 线宽在 20mm 网格上
实测每面内缩 110mm（半线宽 90 + 一格取整 20），故净面积 = (w-220)*(h-220)。
名义尺寸 4000×3000 的客厅，画出来实际净空是 4000×2800（上墙 200 向内），
手算 9.75 而非计划伪代码里的 12.00；计划那组 12/9/4/25 与夹具几何不自洽。
"""
import ezdxf

from app.core.takeoff.enclosure import build_rooms, detect_leaks
from app.core.takeoff.layers import resolve_roles
from app.core.takeoff.types import TakeoffParams

KEYS = {"bounds", "res", "V", "rooms", "walls_strict", "enc", "doors", "windows",
        "seals", "grid", "lab", "n", "border", "anchors", "counts"}
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
    # 卫生间上墙实际在 y=2000 与 y=2200（不是 2400）；两条都删，缺口 2400>GMAX 2200
    for e in list(msp.query('LINE[layer=="WALL"]')):
        s, t = e.dxf.start, e.dxf.end
        if abs(s.y - t.y) < 1 and 1999 <= s.y <= 2201 and min(s.x, t.x) >= 11000:
            e.destroy()
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams())
    leaks = detect_leaks(data)
    assert "卫生间" in leaks
    assert data["border"]  # 确实存在贴边连通域


def test_build_rooms_keys_and_counts(tmp_path):
    doc = _plan(tmp_path)
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800.0))
    assert set(data) >= KEYS
    assert data["V"]["clear_to_soffit"] == 2800.0
    assert len(data["seals"]) == 0
    assert set(data["counts"]) == {"wall_solid", "FIN_axial", "FIN_part",
                                   "door", "window", "curb"}


def test_detect_leaks_empty_when_closed(tmp_path):
    doc = _plan(tmp_path)
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800.0))
    assert detect_leaks(data) == []
