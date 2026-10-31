"""Task 7：五分部出数（1 地面 / 2 墙面 / 3 天棚 / 4 洞口 / 5 踢脚）。

期望值取夹具栅格实测（见 tests/test_takeoff_enclosure.py 的 180mm 线宽内缩说明），
不是计划伪代码里的名义尺寸：
  客厅 9.75 m² / 周长 12.64；卧室 7.17 / 10.64；卫生间 2.81 / 6.64；
  地面/天棚合计 19.73；净墙 @2800 = 12.64×2.8−0.9×2.1 + 10.64×2.8 + 6.64×2.8 = 81.89；
  踢脚 = 12.64−0.9+10.64+6.64 = 29.02；洞口 1 樘门（宽 0.90）。
"""
import json
import os
import subprocess
import sys

import pytest

from app.core.takeoff.coverage import build_coverage
from app.core.takeoff.enclosure import _decode_text, build_rooms
from app.core.takeoff.layers import resolve_roles
from app.core.takeoff.reconcile import apply as reconcile_apply
from app.core.takeoff.rows import to_measurement
from app.core.takeoff.takeoff import ceiling_label, rows_for_divisions
from app.core.takeoff.types import Row, TakeoffParams
from tests.test_takeoff_enclosure import _plan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    """净高优先级：吊顶净高 > 业态默认；待参数业态按**房间净高证据**逐间判。

    T10 修复轮 I4 裁定：requires_floor_height 的业态（mall/hotel/未知）里，
    无 ceiling_levels 的房间**整间不出墙面行**（不给分母、不拿兜底 2800 冒充），
    有吊顶标高的房间照常用 soffit_mm 出墙。原用例 3「未知业态兜底 2800」按此裁定
    已不成立，改为断言不出行 + 有标高时用标高。
    """
    # 1) 吊顶净高 2600 → 14.00 × 2.600 = 36.4
    wall = [r for r in _synth(_box_rooms(soffit_mm=2600)) if r.division_id == 2][0]
    assert abs(wall.value - 36.4) <= 0.05
    assert "2.600" in wall.formula_text
    # 2) 无 soffit、业态 office → 默认 3000 → 14.00 × 3.000 = 42.0
    wall = [r for r in _synth(_box_rooms(), params=TakeoffParams(project_type="office"))
            if r.division_id == 2][0]
    assert abs(wall.value - 42.0) <= 0.05
    assert "3.000" in wall.formula_text
    # 3) 未知业态（requires_floor_height）且无 ceiling_levels → 无墙面行
    rows = _synth(_box_rooms(), params=TakeoffParams(project_type="mystery"))
    assert [r for r in rows if r.division_id == 2] == []
    assert [r for r in rows if r.division_id in (1, 3, 5)]      # 非墙面分部照出
    #    同业态有吊顶标高 → 用 soffit 出墙：14.00 × 2.750 = 38.5
    wall = [r for r in _synth(_box_rooms(ceiling_levels=[2750.0], soffit_mm=2750.0),
                              params=TakeoffParams(project_type="mystery"))
            if r.division_id == 2][0]
    assert abs(wall.value - 38.5) <= 0.05
    assert "2.750" in wall.formula_text


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


# ---- Task 9：覆盖矩阵 / 两条判定带对账 / Row 落库适配 ----
# 覆盖状态语义：有源=几何/参数来源存在（数值 0 仍是有源）；无源=无 poly/无来源；
# 待参数=来源存在但必需参数缺失（不猜默认值）。


def test_coverage_marks_params_status(tmp_path):
    """mall 无显式层高：墙面来源存在但缺层高 → 待参数；地面有 poly → 有源。"""
    data = _data(tmp_path)
    cov = build_coverage(data, TakeoffParams(project_type="mall"))
    cells = {(r["space"], r["part"]): r["status"] for r in cov.rows()}
    assert cells[("客厅", "墙面")] == "待参数"
    assert cells[("客厅", "地面")] == "有源"


def test_coverage_source_and_unplaced(tmp_path):
    """显式层高 → 墙面有源；有 doors → 洞口有源；unplaced 锚点 → 围合待确认且署源。"""
    data = _data(tmp_path)
    cov = build_coverage(data, TakeoffParams(floor_height_mm=2800))
    cells = {(r["space"], r["part"]): r for r in cov.rows()}
    assert cells[("客厅", "墙面")]["status"] == "有源"
    assert cells[("客厅", "墙面")]["source"]
    assert cells[("(项目)", "洞口")]["status"] == "有源"
    assert cells[("(项目)", "房间围合")]["status"] == "有源"
    assert cells[("(项目)", "房间围合")]["source"] == "anchors"

    data["unplaced_anchors"] = [(0, 0, "压墙"), (10, 10, "压墙"), (5, 5, "游离")]
    cov = build_coverage(data, TakeoffParams(floor_height_mm=2800))
    cells = {(r["space"], r["part"]): r for r in cov.rows()}
    cell = cells[("(项目)", "房间围合")]
    assert cell["status"] == "待确认"
    assert "压墙" in cell["source"] and "游离" in cell["source"]
    assert cell["source"].count("压墙") == 1          # 去重后再披露


def test_coverage_no_poly_is_no_source():
    """无 poly 的室内房：四部位全无源；无门窗 → 洞口无源。0 值有 poly 仍是有源。"""
    data = {"rooms": [
        {"comp_id": 9, "names": ["空房"], "is_out": False, "poly": None, "area": 0.0},
        {"comp_id": 10, "names": ["零面积"], "is_out": False,
         "poly": [(0, 0), (1, 0), (1, 1)], "area": 0.0},
    ], "doors": [], "windows": [], "unplaced_anchors": []}
    cov = build_coverage(data, TakeoffParams(floor_height_mm=2800))
    cells = {(r["space"], r["part"]): r["status"] for r in cov.rows()}
    for part in ("地面", "天棚", "墙面", "踢脚"):
        assert cells[("空房", part)] == "无源"
    assert cells[("(项目)", "洞口")] == "无源"
    # 0 值 ≠ 缺行：有 poly 的零面积房间仍算有源
    assert cells[("零面积", "地面")] == "有源"


def test_coverage_naming_and_residual_strips():
    """无名域按 comp_id 出「未命名空间N」；窄条只计地面；同名命名房 #comp 消歧。

    残条判据是几何（平均宽 2A/P ≤250mm），不再依赖“图里有没有别的房间名”；
    无名但足够宽的围合域照常四部位有源（与 rows_for_divisions 同口径）。
    """
    data = {"rooms": [
        # 227 同款：两块无名窄条（10cm 宽长条），面积不同但几何同型
        {"comp_id": 3, "names": [], "is_out": False,
         "poly": [(0, 0), (4600, 0), (4600, 100), (0, 100)], "area": 0.46},
        {"comp_id": 24, "names": [], "is_out": False,
         "poly": [(2000, 0), (13400, 0), (13400, 100), (2000, 100)], "area": 1.14},
        {"comp_id": 7, "names": ["客厅"], "is_out": False,
         "poly": [(4000, 0), (5000, 0), (5000, 1000), (4000, 1000)], "area": 1.0},
        {"comp_id": 8, "names": ["客厅"], "is_out": False,
         "poly": [(6000, 0), (7000, 0), (7000, 1000), (6000, 1000)], "area": 1.0},
        # is_out 同名房不参与消歧：户内「客厅」仍是两间同名
        {"comp_id": 9, "names": ["客厅"], "is_out": True,
         "poly": [(8000, 0), (9000, 0), (9000, 1000), (8000, 1000)], "area": 1.0},
    ], "doors": [], "windows": [], "unplaced_anchors": []}
    cov = build_coverage(data, TakeoffParams(floor_height_mm=2800))
    rows = cov.rows()
    spaces = {r["space"] for r in rows}
    assert {"未命名空间3", "未命名空间24", "客厅#7", "客厅#8"} <= spaces
    assert "客厅" not in spaces                  # 同名户内房一律消歧
    assert "(无标注)" not in spaces
    cells = {(r["space"], r["part"]): r["status"] for r in rows}
    for space in ("未命名空间3", "未命名空间24"):
        assert cells[(space, "地面")] == "有源"
        for part in ("天棚", "墙面", "踢脚"):
            assert cells[(space, part)] == "无源"
    # 四个室内容间各 4 格（残条 1 有源 + 3 无源）+ 项目级 2 格
    assert len(rows) == 4 * 4 + 2


def test_unnamed_wide_region_keeps_all_parts():
    """部分房间漏标时，未命名但足够宽的围合域仍是房间：照常出 2/3/5、标低置信。"""
    data = {"rooms": [
        {"comp_id": 1, "names": ["客厅"], "is_out": False,
         "poly": [(0, 0), (4000, 0), (4000, 3000), (0, 3000)], "area": 12.0},
        {"comp_id": 2, "names": [], "is_out": False,
         "poly": [(0, 4000), (5000, 4000), (5000, 8000), (0, 8000)], "area": 20.0},
    ], "doors": [], "windows": [], "unplaced_anchors": []}
    rows, issues = rows_for_divisions(data, TakeoffParams(floor_height_mm=2800.0))
    by_div = {}
    for r in rows:
        by_div.setdefault(r.division_id, []).append(r)
    assert len(by_div[1]) == 2
    for d in (2, 3, 5):
        assert len(by_div[d]) == 2, d
    unnamed_rows = [r for r in rows if r.label.startswith("未命名空间2")]
    assert len(unnamed_rows) == 4
    assert all(r.confidence_level == "low" for r in unnamed_rows)
    assert all("非房间残条" not in r.formula_text for r in unnamed_rows)
    assert [i.code for i in issues] == ["unnamed_rooms"]
    assert issues[0].detail["comp_ids"] == [2]

    cells = {(r["space"], r["part"]): r["status"]
             for r in build_coverage(data, TakeoffParams(floor_height_mm=2800)).rows()}
    assert cells[("未命名空间2", "天棚")] == "有源"
    assert cells[("未命名空间2", "墙面")] == "有源"


def test_unnamed_region_only_floor_and_disclosed():
    """无名窄条残条只出地面：2/3/5 不出行，并由 issue 披露。

    227 封账：地面 163.75 含两块残条 1.60㎡，天棚/墙面/踢脚按 162.15/437.22/166.73
    只认有房间名的围合域（设计稿 §3.3）。判据是 2A/P ≤250mm 的无名窄条。
    """
    data = {"rooms": [
        {"comp_id": 1, "names": ["客厅"], "is_out": False,
         "poly": [(0, 0), (4000, 0), (4000, 3000), (0, 3000)], "area": 12.0},
        {"comp_id": 2, "names": [], "is_out": False,
         "poly": [(0, 4000), (5000, 4000), (5000, 4200), (0, 4200)], "area": 1.0},
    ], "doors": [], "windows": [], "unplaced_anchors": []}
    rows, issues = rows_for_divisions(data, TakeoffParams(floor_height_mm=2800.0))
    by_div = {}
    for r in rows:
        by_div.setdefault(r.division_id, []).append(r)
    assert len(by_div[1]) == 2
    for d in (2, 3, 5):
        assert len(by_div[d]) == 1, d
    assert not by_div.get(4)

    strip = [r for r in by_div[1] if r.label == "未命名空间2-地面"][0]
    assert strip.confidence_level == "low"
    assert "非房间残条" in strip.formula_text
    assert "0.19m" in strip.formula_text       # 2×1.0/10.4 = 0.192m
    assert "不参与天棚" in strip.formula_text
    living = [r for r in by_div[1] if r.label == "客厅-地面"][0]
    assert "非房间残条" not in living.formula_text

    issue = [i for i in issues if i.code == "unlabeled_enclosure_region"]
    assert len(issue) == 1 and issue[0].level == "info"
    assert issue[0].detail["regions"] == [
        {"comp_id": 2, "area": 1.0, "mean_width_mm": 192.3}]
    assert "Σ1.00㎡" in issue[0].message


def test_zero_enclosed_rooms_disclosed():
    """本图未围合出任何房间 → no_enclosed_rooms，不得静默空结果。"""
    rows, issues = rows_for_divisions(
        {"rooms": [], "doors": [], "windows": []}, TakeoffParams(floor_height_mm=2800.0))
    assert rows == []
    assert [i.code for i in issues] == ["no_enclosed_rooms"]


def test_reconcile_only_two_bands(tmp_path):
    """恰好两条带：墙地比 4.15、踢脚比 1.47 均越带；只写 check，不改 value、不增行。"""
    data = _data(tmp_path)
    rows, _ = rows_for_divisions(data, TakeoffParams(floor_height_mm=2800.0))
    assert len(rows) == 13
    before = [r.value for r in rows]
    issues = reconcile_apply(rows)
    assert {i.code for i in issues} == {"wall_floor_ratio", "baseboard_ratio"}

    wall = [r for r in rows if r.division_id == 2]
    bb = [r for r in rows if r.division_id == 5]
    assert all(r.check["wall_floor_ratio"] == 4.15 for r in wall)
    assert all(r.check["baseboard_ratio"] == 1.47 for r in bb)
    assert [r.value for r in rows] == before             # 不改数值
    assert len(rows) == 13                               # 不新增数字行

    by_code = {i.code: i for i in issues}
    wf = by_code["wall_floor_ratio"]
    assert wf.level == "warn"
    assert wf.detail["band"] == [2.0, 3.0]
    assert wf.detail["project_type"] == "sample_flat"
    assert abs(wf.detail["ratio"] - 4.15) < 0.01
    assert "4.15" in wf.message and "2.0" in wf.message and "3.0" in wf.message
    bbr = by_code["baseboard_ratio"]
    assert bbr.detail["band"] == [0.6, 1.1]
    assert "1.47" in bbr.message


def test_reconcile_band_respects_project_type(tmp_path):
    """带按 params.project_type 取：showroom 用 (2.5,3.5)；落入带内不报。"""
    data = _data(tmp_path)
    rows, _ = rows_for_divisions(data, TakeoffParams(floor_height_mm=2800.0))
    issues = reconcile_apply(rows, TakeoffParams(project_type="showroom"))
    wf = [i for i in issues if i.code == "wall_floor_ratio"][0]
    assert wf.detail["project_type"] == "showroom"
    assert wf.detail["band"] == [2.5, 3.5]
    assert abs(wf.detail["ratio"] - 4.15) < 0.01          # 同一 rows 仍越 showroom 带

    # 自选构造：墙地 30/10=3.0 落 showroom 带、踢脚 8/10=0.8 落踢脚带 → 两条都不报
    synth = [
        Row(1, "地面", "dxf_floor", "m2", 10.0, "f"),
        Row(2, "墙面", "dxf_wall", "m2", 30.0, "f", check={"gross_m2": 30.0}),
        Row(5, "踢脚", "dxf_baseboard", "m", 8.0, "f"),
    ]
    assert reconcile_apply(synth, TakeoffParams(project_type="showroom")) == []
    assert synth[1].check["wall_floor_ratio"] == 3.0
    assert synth[2].check["baseboard_ratio"] == 0.8       # 原 check=None → 新建 dict


def test_reconcile_ratio_rounded_before_band_check():
    """比值先舍入到 2 位：3.004→3.00 不报；3.006→3.01 报，check/message/detail 同值。"""
    def synth(wall_value):
        return [
            Row(1, "地面", "dxf_floor", "m2", 10.0, "f"),
            Row(2, "墙面", "dxf_wall", "m2", wall_value, "f"),
        ]

    in_band = synth(30.04)                       # 30.04/10 = 3.004 → 3.00
    assert reconcile_apply(in_band) == []
    assert in_band[1].check["wall_floor_ratio"] == 3.0

    out_band = synth(30.06)                      # 30.06/10 = 3.006 → 3.01
    issues = reconcile_apply(out_band)
    wf = [i for i in issues if i.code == "wall_floor_ratio"][0]
    assert wf.detail["ratio"] == 3.01
    assert "3.01" in wf.message
    assert out_band[1].check["wall_floor_ratio"] == 3.01


def test_reconcile_empty_and_zero_totals_short_circuit():
    """空 rows、floor=0、baseboard=0：不报、不崩、不写无定义比值。"""
    assert reconcile_apply([]) == []

    # floor=0（只有 div2/div5）：两个比值都无定义 → 不判、不建 check
    no_floor = [
        Row(2, "墙面", "dxf_wall", "m2", 30.0, "f"),
        Row(5, "踢脚", "dxf_baseboard", "m", 8.0, "f"),
    ]
    assert reconcile_apply(no_floor) == []
    assert no_floor[0].check is None and no_floor[1].check is None

    # baseboard=0（有 floor/wall、无 div5）：只判墙地比 25/10=2.5 落带内 → 不报
    no_baseboard = [
        Row(1, "地面", "dxf_floor", "m2", 10.0, "f"),
        Row(2, "墙面", "dxf_wall", "m2", 25.0, "f", check={"gross_m2": 25.0}),
    ]
    assert reconcile_apply(no_baseboard) == []
    assert no_baseboard[1].check == {"gross_m2": 25.0, "wall_floor_ratio": 2.5}
    assert "baseboard_ratio" not in no_baseboard[1].check


def test_to_measurement_maps_units_and_trace(tmp_path):
    """单位映射 + 来源/置信图标 + 计算式/部位原样 + check 落 data_json。"""
    data = _data(tmp_path)
    rows, _ = rows_for_divisions(data, TakeoffParams(floor_height_mm=2800.0))
    reconcile_apply(rows)

    floor = [r for r in rows if r.division_id == 1][0]
    m = to_measurement(floor, project_id=7)
    assert m.area == floor.value and m.unit == "m²"
    assert m.length is None and m.count is None
    assert m.source_tag == "图纸实测" and m.source_tag_icon == "📐"
    assert m.confidence_level == "high" and m.confidence_icon == "🟢"
    assert m.drawing_id is None and m.data_json is None

    wall = [r for r in rows if r.division_id == 2][0]
    mw = to_measurement(wall, project_id=7, drawing_id=3)
    assert mw.area == wall.value and mw.unit == "m²"
    assert mw.source_tag == "几何推算" and mw.source_tag_icon == "🔢"
    assert mw.confidence_level == "medium" and mw.confidence_icon == "🟡"
    assert mw.formula_text == wall.formula_text
    assert mw.position_desc == wall.position_desc
    assert mw.drawing_id == 3
    assert json.loads(mw.data_json)["check"] == wall.check
    assert "wall_floor_ratio" in json.loads(mw.data_json)["check"]

    bb = [r for r in rows if r.division_id == 5][0]
    mb = to_measurement(bb, project_id=7)
    assert mb.length == bb.value and mb.area is None and mb.count is None
    assert mb.unit == "m"
    assert "baseboard_ratio" in json.loads(mb.data_json)["check"]

    opening = [r for r in rows if r.division_id == 4][0]
    mo = to_measurement(opening, project_id=7)
    assert mo.count == 1 and mo.unit == "樘"
    assert mo.area is None and mo.length is None

    # 未知来源/置信度：不抛，图标按口径兜底（未知来源空串；未知置信度用 medium）
    weird = Row(2, "怪", "dxf_wall", "m2", 1.0, "f",
                source_tag="不存在的来源", confidence_level="unknown")
    mw2 = to_measurement(weird, project_id=7)
    assert mw2.source_tag_icon == ""
    assert mw2.confidence_icon == "🟡"


def test_to_measurement_unknown_unit_raises():
    """未知单位必须显式 ValueError，不得静默按 area 落库。"""
    with pytest.raises(ValueError, match="未知单位"):
        to_measurement(Row(1, "怪", "dxf_floor", "kg", 1.0, "f"), project_id=7)


def test_rows_module_import_is_lazy():
    """只 import rows 模块不得拉 ORM；to_measurement 调用时才惰性 import app.models。"""
    code = "\n".join([
        "import sys",
        "import app.core.takeoff.rows as rows_mod",
        "assert 'app.models' not in sys.modules, 'ORM 在模块导入期即被拉入'",
        "from app.core.takeoff.types import Row",
        "m = rows_mod.to_measurement(",
        "    Row(1, 'A-地面', 'dxf_floor', 'm2', 1.0, 'f'), 1)",
        "assert 'app.models' in sys.modules, 'to_measurement 应惰性加载 ORM'",
        "print(m.unit)",
    ])
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "m²"


def test_no_labels_anywhere_still_measures_all_parts():
    """全图无房间名（标签层缺失）不得把每间都当残条：2/3/5 照常出量并披露。

    宽 4×3 的围合域（平均宽 1.71m）几何上不是窄条，按「未命名空间1」出量、标低置信。
    """
    data = {"rooms": [
        {"comp_id": 1, "names": [], "is_out": False,
         "poly": [(0, 0), (4000, 0), (4000, 3000), (0, 3000)], "area": 12.0},
    ], "doors": [], "windows": [], "unplaced_anchors": []}
    rows, issues = rows_for_divisions(data, TakeoffParams(floor_height_mm=2800.0))
    assert {r.division_id for r in rows} == {1, 2, 3, 5}
    assert [i.code for i in issues] == ["no_room_labels"]
    assert all(r.label.startswith("未命名空间1-") for r in rows)
    assert all(r.confidence_level == "low" for r in rows)
    floor = [r for r in rows if r.division_id == 1][0]
    assert "非房间残条" not in floor.formula_text

    cells = {(r["space"], r["part"]): r["status"]
             for r in build_coverage(data, TakeoffParams(floor_height_mm=2800)).rows()}
    assert cells[("未命名空间1", "天棚")] == "有源"
    assert cells[("未命名空间1", "墙面")] == "有源"
