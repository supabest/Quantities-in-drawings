"""Task 7：五分部出数（1 地面 / 2 墙面 / 3 天棚 / 4 洞口 / 5 踢脚）。

期望值取夹具栅格实测（见 tests/test_takeoff_enclosure.py 的 180mm 线宽内缩说明），
不是计划伪代码里的名义尺寸：
  客厅 9.75 m² / 周长 12.64；卧室 7.17 / 10.64；卫生间 2.81 / 6.64；
  地面/天棚合计 19.73；净墙 @2800 = 12.64×2.8−0.9×2.1 + 10.64×2.8 + 6.64×2.8 = 81.89；
  踢脚 = 12.64−0.9+10.64+6.64 = 29.02；洞口 1 樘门（宽 0.90）。
"""
from app.core.takeoff.enclosure import _decode_text, build_rooms
from app.core.takeoff.layers import resolve_roles
from app.core.takeoff.takeoff import ceiling_label, rows_for_divisions
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
    # 矢量墙线直接围合量出 → 图纸实测（默认口径）
    assert all(r.source_tag == "图纸实测" for r in floor + ceil)


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
    # 公式断言不只看客厅：卧室周长 10.64 必须真的进了算式
    bedroom = [r for r in wall if "卧室" in r.label][0]
    assert "10.64" in bedroom.formula_text
    # 净高、门洞扣减均属参数派生 → 几何推算
    assert all(r.source_tag == "几何推算" for r in wall)


def test_baseboard_deducts_door(tmp_path):
    rows = _rows(tmp_path)
    bb = [r for r in rows if r.division_id == 5]
    assert len(bb) == 3
    assert all(r.unit == "m" for r in bb)
    assert abs(sum(r.value for r in bb) - 29.02) <= 0.15
    living = [r for r in bb if "客厅" in r.label][0]
    assert abs(living.value - (12.64 - 0.90)) <= 0.06
    bedroom = [r for r in bb if "卧室" in r.label][0]
    assert "10.64" in bedroom.formula_text
    assert all(r.source_tag == "几何推算" for r in bb)


def test_openings_counted_by_tatami_unit(tmp_path):
    rows = _rows(tmp_path)
    openings = [r for r in rows if r.division_id == 4]
    assert len(openings) == 1
    assert all(r.unit == "樘" and r.value == 1 for r in openings)
    assert "0.90" in openings[0].formula_text
    assert "comp_id=" in openings[0].position_desc
    assert all(r.source_tag == "几何推算" for r in openings)


# ---- 以下为合成数据用例：不依赖 _plan 夹具，直接构造 data 打 rows_for_divisions ----


def _box_rooms(**fields):
    """4000×3000 方盒房间列表；fields 覆盖/附加到首个房间。"""
    room = {"comp_id": 1, "names": ["A"], "is_out": False,
            "poly": [(0, 0), (4000, 0), (4000, 3000), (0, 3000)], "area": 12.0}
    room.update(fields)
    return [room]


def _synth(rooms, doors=(), windows=(), params=None):
    data = {"rooms": rooms, "doors": list(doors), "windows": list(windows)}
    return rows_for_divisions(data, params or TakeoffParams())[0]


def test_openings_counted_by_physical_tatami_not_segments():
    """共享墙两侧各画一条门线：物理洞口 1 樘，不得按线段数计成 2。"""
    rooms = [
        {"comp_id": 1, "names": ["A"], "is_out": False,
         "poly": [(0, 0), (4000, 0), (4000, 3000), (0, 3000)], "area": 12.0},
        {"comp_id": 2, "names": ["B"], "is_out": False,
         "poly": [(4000, 0), (8000, 0), (8000, 3000), (4000, 3000)], "area": 12.0},
    ]
    # 同一樘门两条平行线，均在两侧房间 x=4000 共享墙的 400mm 容差内
    doors = [(3960, 1000, 3960, 1900), (3990, 1000, 3990, 1900)]
    rows = _synth(rooms, doors=doors)
    assert len(doors) == 2                    # 前提：线段确实两条，排除假通过
    openings = [r for r in rows if r.division_id == 4]
    assert len(openings) == 1
    assert openings[0].unit == "樘" and openings[0].value == 1
    assert "0.90" in openings[0].formula_text


def test_net_height_priority_soffit_office_default_fallback():
    """净高优先级：吊顶净高 > 业态默认 > 兜底 2800（不得直传 floor_height 绕过业态）。"""
    # 1) 吊顶净高 2600 → 14.00 × 2.600 = 36.4
    wall = [r for r in _synth(_box_rooms(soffit_mm=2600)) if r.division_id == 2][0]
    assert abs(wall.value - 36.4) <= 0.05
    assert "2.600" in wall.formula_text
    # 2) 无 soffit、业态 office → 默认 3000 → 14.00 × 3.000 = 42.0
    wall = [r for r in _synth(_box_rooms(), params=TakeoffParams(project_type="office"))
            if r.division_id == 2][0]
    assert abs(wall.value - 42.0) <= 0.05
    assert "3.000" in wall.formula_text
    # 3) 无 soffit、未知业态 → effective 为 None → 兜底 2800 → 39.2
    wall = [r for r in _synth(_box_rooms(), params=TakeoffParams(project_type="mystery"))
            if r.division_id == 2][0]
    assert abs(wall.value - 39.2) <= 0.05
    assert "2.800" in wall.formula_text


# ---- Task 8：吊顶标高按 doc/bounds 提取 + 天棚造型/平顶列项（投影不展开） ----


def test_ceiling_label_rule():
    """单标高=平顶；多标高（主顶+跌级）=造型，特征须披露灯槽/检修口/不展开。"""
    assert ceiling_label({"ceiling_levels": [2800.0]})[0] == "天棚刮腻子/涂料(基层)"
    assert ceiling_label({"ceiling_levels": []})[0] == "天棚刮腻子/涂料(基层)"
    assert ceiling_label({})[0] == "天棚刮腻子/涂料(基层)"
    name, feat = ceiling_label({"ceiling_levels": [2800.0, 2600.0]})
    assert name in ("跌级造型石膏板天花", "跌级造型天花")
    assert "灯槽" in feat and "检修口" in feat and "不展开" in feat


def test_shaped_ceiling_still_projection(tmp_path):
    """造型天棚仍按围合水平投影计量（不展开），合计不得因造型而变。"""
    data = _data(tmp_path)
    room = data["rooms"][0]
    room["ceiling_levels"] = [2750.0, 2600.0]
    name = "+".join(room["names"])
    rows, _ = rows_for_divisions(data, TakeoffParams(floor_height_mm=2800.0))
    ceil = [r for r in rows if r.division_id == 3]
    assert len(ceil) == 3
    assert abs(sum(r.value for r in ceil) - 19.73) <= 0.05   # 投影不展开
    shaped = [r for r in ceil if r.label.startswith(name + "-")][0]
    assert "跌级" in shaped.label or "造型" in shaped.label
    assert "灯槽" in shaped.project_features and "不展开" in shaped.project_features
    assert shaped.value == round(room["area"], 2)
    assert "ceiling_levels=+2750/+2600" in shaped.remarks
    assert "comp_id=" in shaped.remarks


def test_build_rooms_harvests_ceiling_levels(tmp_path):
    """吊顶标高按传入 doc/bounds 提取：+2700 落客厅 → 客厅 soffit 2700、下沉 100。"""
    doc = _plan(tmp_path)
    doc.layers.add("CEILING-ANNO")
    doc.modelspace().add_text(
        "+2700", height=250,
        dxfattribs={"layer": "CEILING-ANNO"}).set_placement((2000, 1500))
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800.0))
    by_name = {"".join(r["names"]): r for r in data["rooms"]}
    living = by_name["客厅"]
    assert living["ceiling_levels"] == [2700.0]
    assert living["soffit_mm"] == 2700.0
    assert living["ceiling_drop_mm"] == 100.0
    assert living["ceiling_min_mm"] == 2700.0
    for nm in ("卧室", "卫生间"):
        assert by_name[nm]["ceiling_levels"] == []
        assert by_name[nm]["soffit_mm"] == 2800.0
    names = [t for r in data["rooms"] for t in r["names"]]
    assert "+2700" not in names


# ---- Task 8 修复轮：标高映射兜底/收集窗口/GBK 解码/MTEXT+块属性/无 doc 兜底 ----


def test_level_nearest_centroid_fallback_and_far_reject(tmp_path):
    """落点不在任何 poly 内：最近质心 ≤3000 兜底收；>3000 一律拒（不当房间名）。"""
    doc = _plan(tmp_path)
    doc.layers.add("CEILING-ANNO")
    msp = doc.modelspace()
    # 客厅右墙外 500mm（内墙面 x=4200，墙带走 4200..4400）→ 不在 poly 内
    msp.add_text("+2700", height=250,
                 dxfattribs={"layer": "CEILING-ANNO"}).set_placement((4300, 1700))
    # 远角：距最近质心（卫生间 12210,1090）约 5.8m，超过 3000 容差
    msp.add_text("+2400", height=250,
                 dxfattribs={"layer": "CEILING-ANNO"}).set_placement((18000, 1700))
    roles = resolve_roles(doc)
    # bounds 是 20 的整数倍，栅格相位与 roles.bounds 一致；窗口外扩 200 仍含 18000
    data = build_rooms(doc, roles, (0, 0, 20000, 5000),
                       TakeoffParams(floor_height_mm=2800))
    by_name = {"".join(r["names"]): r for r in data["rooms"]}
    living = by_name["客厅"]
    assert living["ceiling_levels"] == [2700.0]      # 质心兜底 ≤3000 生效
    assert living["soffit_mm"] == 2700.0
    assert 2400.0 not in living["ceiling_levels"]    # 远的没被兜进来
    for r in data["rooms"]:
        assert 2400.0 not in r["ceiling_levels"]     # >3000 拒绝
        if "".join(r["names"]) != "客厅":
            assert r["soffit_mm"] == 2800.0          # 其余房间仍兜底结构净高
    names = [t for r in data["rooms"] for t in r["names"]]
    assert "+2700" not in names and "+2400" not in names   # 标高不当房间名


def test_level_collect_window_pad_200(tmp_path):
    """收集窗口 = 对齐后 bounds 各外扩 200mm：X1+500 收不到，X1+100 收得到。"""
    doc = _plan(tmp_path)
    doc.layers.add("CEILING-ANNO")
    msp = doc.modelspace()
    roles = resolve_roles(doc)
    X1 = roles.bounds[2]
    # 窗外 500mm：+2500 不得进任何房间，全部兜底 2800
    msp.add_text("+2500", height=250,
                 dxfattribs={"layer": "CEILING-ANNO"}).set_placement((X1 + 500, 1700))
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800))
    assert len(data["rooms"]) == 3
    for r in data["rooms"]:
        assert 2500.0 not in r["ceiling_levels"]
        assert r["ceiling_levels"] == []
        assert r["soffit_mm"] == 2800.0
    # 窗内 100mm（仍在 bounds 外）：+2600 必须被收，最近质心是卫生间
    msp.add_text("+2600", height=250,
                 dxfattribs={"layer": "CEILING-ANNO"}).set_placement((X1 + 100, 1700))
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800))
    by_name = {"".join(r["names"]): r for r in data["rooms"]}
    assert by_name["卫生间"]["ceiling_levels"] == [2600.0]
    assert by_name["卫生间"]["soffit_mm"] == 2600.0
    assert by_name["客厅"]["ceiling_levels"] == []       # 500 外的那条仍被挡


def test_decode_text_gbk_escape():
    """天正/理正 \\M+5xxxx：两字节 GBK 转义还原；无转义原样返回。"""
    raw = "客厅".encode("gbk")
    esc = "".join("\\M+5%02X%02X" % (raw[i], raw[i + 1]) for i in range(0, len(raw), 2))
    assert esc.startswith("\\M+5") and len(esc) == 8 * (len(raw) // 2)  # 前提：确实是转义串
    assert _decode_text(esc) == "客厅"
    assert _decode_text("+2700") == "+2700"
    assert _decode_text("客厅") == "客厅"
    assert _decode_text("") == ""


def test_mtext_level_collected(tmp_path):
    """MTEXT 形式的 +2xxx 同样进标高映射（走 plain_text）。"""
    doc = _plan(tmp_path)
    doc.layers.add("CEILING-ANNO")
    doc.modelspace().add_mtext("+2650", dxfattribs={
        "layer": "CEILING-ANNO"}).set_location((2000, 1500))
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800))
    by_name = {"".join(r["names"]): r for r in data["rooms"]}
    assert by_name["客厅"]["ceiling_levels"] == [2650.0]
    assert by_name["客厅"]["soffit_mm"] == 2650.0


def test_block_attrib_level_collected(tmp_path):
    """块内属性（INSERT attrib，tag LVL）同样进标高映射，坐标取 INSERT 点。"""
    doc = _plan(tmp_path)
    doc.layers.add("CEILING-ANNO")
    blk = doc.blocks.new("LEVELBLK")
    blk.add_attdef("LVL", insert=(0, 0))
    msp = doc.modelspace()
    ins = msp.add_blockref("LEVELBLK", (2000, 1500),
                           dxfattribs={"layer": "CEILING-ANNO"})
    ins.add_attrib("LVL", "+2450", insert=(2000, 1500),
                   dxfattribs={"layer": "CEILING-ANNO"})
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800))
    by_name = {"".join(r["names"]): r for r in data["rooms"]}
    assert by_name["客厅"]["ceiling_levels"] == [2450.0]
    assert by_name["客厅"]["soffit_mm"] == 2450.0


def test_build_rooms_doc_none_fallback(tmp_path):
    """doc=None（纯几何单测）不得崩：三间房全部按结构净高兜底。"""
    roles = resolve_roles(_plan(tmp_path))
    data = build_rooms(None, roles, roles.bounds, TakeoffParams(floor_height_mm=2800))
    assert len(data["rooms"]) == 3
    for r in data["rooms"]:
        assert r["ceiling_levels"] == []
        assert r["soffit_mm"] == 2800.0
        assert r["ceiling_drop_mm"] == 0.0
        assert r["ceiling_min_mm"] == 2800.0
