import ezdxf
from app.core.takeoff.layers import (SurveyReport, affected_divisions, proxy_survey,
                                     role_of)


def _doc(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("WALL"); doc.layers.add("P-TEXT"); doc.layers.add("DT")
    msp = doc.modelspace()
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "WALL"})
    msp.add_text("主卧", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((10, 10))
    p = tmp_path / "a.dxf"; doc.saveas(p); return ezdxf.readfile(p)


def test_proxy_survey_counts_and_versions(tmp_path):
    rep = proxy_survey(_doc(tmp_path))
    assert isinstance(rep, SurveyReport)
    assert rep.dxf_version.startswith("AC")
    assert rep.codepage
    assert rep.entity_counts["LINE"] >= 1
    assert rep.proxies_by_layer == {}


def test_role_of_matches_227_layer_names():
    assert role_of("WALL") == "wall_solid"
    assert role_of("S-WALL") == "wall_solid"
    assert role_of("FIN") == "wall_fin"
    assert role_of("DOOR") == "door"
    assert role_of("door_plan") == "door"
    assert role_of("WINDOW") == "window"
    assert role_of("CURB") == "curb"
    assert role_of("P-TEXT") == "room_label"
    assert role_of("DT") is None


def test_role_of_override_227_window_layer_is_curb():
    assert role_of("AX-COMP-160$0$A-WINDOW") == "curb"
    assert role_of("ax-comp-160$0$a-window") == "curb"
    # 通用 A-WINDOW 仍然按窗处理，不能被覆盖波及
    assert role_of("WINDOW") == "window"
    assert role_of("A-WINDOW") == "window"


def test_role_of_xref_wall_suffix_and_walldim_negative():
    assert role_of("1#$0$S-WALL") == "wall_solid"
    assert role_of("WALL") == "wall_solid"
    assert role_of("S_WALL") == "wall_solid"
    assert role_of("Walldim") is None


def test_affected_divisions_by_role():
    assert affected_divisions({"wall_solid"}) == {1, 2, 3, 5}
    assert affected_divisions({"door", "window"}) == {2, 4}
    assert affected_divisions({"curb"}) == {1, 3}
    assert affected_divisions({"room_label"}) == set()
    assert affected_divisions(set()) == set()


def test_block_prefix_counts_case_insensitive_and_inserts_separate(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    for name in ("a$c123", "*u5", "$twtsys$0001"):
        doc.blocks.new(name)
    msp = doc.modelspace()
    msp.add_blockref("a$c123", (0, 0))
    msp.add_blockref("*u5", (100, 0))
    msp.add_blockref("$twtsys$0001", (200, 0))
    p = tmp_path / "blocks.dxf"
    doc.saveas(p)

    rep = proxy_survey(ezdxf.readfile(p))
    # 块定义按前缀分桶，且大小写不敏感
    assert rep.block_prefix_counts["A$C"] == 1
    assert rep.block_prefix_counts["*U"] == 1
    assert rep.block_prefix_counts["$TwtSys$"] == 1
    # INSERT 实例单独成账，不与块定义混算
    assert rep.insert_prefix_counts == {"A$C": 1, "*U": 1, "$TwtSys$": 1}
    assert sum(rep.block_prefix_counts.values()) > sum(rep.insert_prefix_counts.values())


def test_proxy_survey_scans_paper_space_layouts(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("P-TEXT")
    msp = doc.modelspace()
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "WALL"})
    sheet = doc.layouts.new("Sheet")
    sheet.add_text("客厅", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((0, 0))
    sheet.add_mtext("说明", dxfattribs={"char_height": 120, "layer": "P-TEXT"}).set_location((0, 50))
    sheet.add_viewport(center=(0, 0), size=(100, 100),
                       view_center_point=(0, 0), view_height=100)
    p = tmp_path / "paper.dxf"
    doc.saveas(p)

    rep = proxy_survey(ezdxf.readfile(p))
    assert "Sheet" in rep.layouts
    assert rep.entity_counts["TEXT"] == 1
    assert rep.entity_counts["MTEXT"] == 1
    assert rep.entity_counts["VIEWPORT"] == 1
    assert rep.entity_counts["LINE"] == 1  # modelspace 未被重复计数
    assert rep.viewport_count == 1
    assert 250.0 in rep.text_heights
    assert 120.0 in rep.text_heights


# ---------------------------------------------------------------- 角色取线

from app.core.takeoff.layers import resolve_roles  # noqa: E402


def _two_rooms(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    for n in ("WALL", "S-WALL", "FIN", "DOOR", "WINDOW", "CURB", "P-TEXT"):
        doc.layers.add(n)
    msp = doc.modelspace()
    # 200mm 双线墙围合 4.0×3.0 内空
    for y in (0, 200):
        msp.add_line((0, y), (4400, y), dxfattribs={"layer": "WALL"})
    for y in (3200, 3400):
        msp.add_line((0, y), (4400, y), dxfattribs={"layer": "WALL"})
    for x in (0, 200):
        msp.add_line((x, 0), (x, 3400), dxfattribs={"layer": "WALL"})
    for x in (4200, 4400):
        msp.add_line((x, 0), (x, 3400), dxfattribs={"layer": "WALL"})
    # FIN 层上的一对 200mm 平行线 → 真隔墙候选
    msp.add_line((2000, 200), (2000, 3200), dxfattribs={"layer": "FIN"})
    msp.add_line((2200, 200), (2200, 3200), dxfattribs={"layer": "FIN"})
    # 门/窗/反坎
    msp.add_line((600, 0), (1500, 0), dxfattribs={"layer": "DOOR"})
    msp.add_line((3000, 3400), (3900, 3400), dxfattribs={"layer": "WINDOW"})
    msp.add_line((400, 0), (4400, 0), dxfattribs={"layer": "CURB"})
    msp.add_text("客厅", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((1000, 1500))
    p = tmp_path / "two.dxf"
    doc.saveas(p)
    return ezdxf.readfile(p)


def test_resolve_roles_segments_and_bounds(tmp_path):
    roles = resolve_roles(_two_rooms(tmp_path))
    assert len(roles.segments["wall_solid"]) == 8
    assert len(roles.segments["wall_fin_part"]) == 2
    assert len(roles.segments["wall_fin_axial"]) == 2
    assert len(roles.segments["door"]) == 1
    assert len(roles.segments["window"]) == 1
    assert len(roles.segments["curb"]) == 1
    assert roles.labels and roles.labels[0][2] == "客厅"
    x0, y0, x1, y1 = roles.bounds
    assert x0 <= 0 and x1 >= 4400 and y1 >= 3400


def test_resolve_roles_splits_fin_double_line():
    """间距 300mm 的平行线不是间墙（超出 100~250 规则）。"""
    doc = ezdxf.new("R2018")
    doc.layers.add("WALL")
    doc.layers.add("FIN")
    msp = doc.modelspace()
    # 真墙一条同向竖线：保证 prim 非空，否则 FIN 全被方向过滤丢弃、断言空转
    msp.add_line((3000, 0), (3000, 3000), dxfattribs={"layer": "WALL"})
    msp.add_line((0, 0), (0, 3000), dxfattribs={"layer": "FIN"})
    msp.add_line((300, 0), (300, 3000), dxfattribs={"layer": "FIN"})
    roles = resolve_roles(doc)
    assert len(roles.segments["wall_fin_axial"]) == 2
    assert roles.segments["wall_fin_part"] == []


AUDIT = "$TD_AUDIT_GENERATED_GEOM"


def _audit_only(tmp_path):
    """无真墙几何，只有审计层：一条边由 LWPOLYLINE + LINE 组成，另有块内 LINE
    和一条模型空间 POLYLINE。"""
    doc = ezdxf.new("R2018")
    doc.layers.add(AUDIT)
    blk = doc.blocks.new("AUDWALL")
    blk.add_line((0, 0), (0, 200), dxfattribs={"layer": AUDIT})
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (4400, 0)], dxfattribs={"layer": AUDIT})
    msp.add_line((0, 200), (4400, 200), dxfattribs={"layer": AUDIT})
    msp.add_polyline2d([(0, 400), (4400, 400)], dxfattribs={"layer": AUDIT})
    msp.add_blockref("AUDWALL", (5000, 0))
    p = tmp_path / "audit.dxf"
    doc.saveas(p)
    return ezdxf.readfile(p)


def test_resolve_roles_audit_fallback_when_no_true_wall(tmp_path):
    roles = resolve_roles(_audit_only(tmp_path))
    assert roles.audit_fallback == [AUDIT]
    # LINE + LWPOLYLINE + POLYLINE（模型空间）+ 块内 LINE 全部纳入
    assert len(roles.segments["wall_solid"]) == 4
    x0, y0, x1, y1 = roles.bounds
    assert x1 >= 5000 and y1 >= 200


def test_resolve_roles_audit_layer_not_merged_when_true_wall_present(tmp_path):
    doc = ezdxf.new("R2018")
    doc.layers.add("WALL")
    doc.layers.add(AUDIT)
    msp = doc.modelspace()
    msp.add_line((0, 0), (4400, 0), dxfattribs={"layer": "WALL"})
    msp.add_line((0, 200), (4400, 200), dxfattribs={"layer": AUDIT})
    msp.add_line((0, 400), (4400, 400), dxfattribs={"layer": AUDIT})
    p = tmp_path / "both.dxf"
    doc.saveas(p)
    roles = resolve_roles(ezdxf.readfile(p))
    assert roles.audit_fallback == []
    assert roles.segments["wall_solid"] == [(0.0, 0.0, 4400.0, 0.0)]


def test_resolve_roles_label_filter_matches_base_extract(tmp_path):
    doc = ezdxf.new("R2018")
    doc.layers.add("WALL")
    doc.layers.add("P-TEXT")
    msp = doc.modelspace()
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "WALL"})
    for t, xy in (("客厅", (10, 10)), ("2人", (20, 20)), ("主卧", (30, 30)),
                  ("12.5 s.m.", (40, 40)), ("上", (50, 50)), ("下", (60, 60)),
                  ("\\M+5BFCD\\M+5CCFC", (70, 70))):
        msp.add_text(t, height=250, dxfattribs={"layer": "P-TEXT"}).set_placement(xy)
    texts = [t for _, _, t in resolve_roles(doc).labels]
    assert texts == ["客厅", "主卧", "客厅"]


def test_resolve_roles_polyline2d_segments():
    """POLYLINE 没有 get_points()，2D POLYLINE 曾整类被静默丢弃。"""
    doc = ezdxf.new("R2018")
    doc.layers.add("WALL")
    msp = doc.modelspace()
    msp.add_polyline2d([(0, 0), (1000, 0), (1000, 500)], dxfattribs={"layer": "WALL"})
    segs = resolve_roles(doc).segments["wall_solid"]
    assert segs == [(0.0, 0.0, 1000.0, 0.0), (1000.0, 0.0, 1000.0, 500.0)]


def test_resolve_roles_polyline3d_segments():
    """3D POLYLINE 取前两维成段（忽略 z）。"""
    doc = ezdxf.new("R2018")
    doc.layers.add("WALL")
    msp = doc.modelspace()
    msp.add_polyline3d([(0, 0, 500), (1000, 0, 500), (1000, 500, 0)],
                       dxfattribs={"layer": "WALL"})
    segs = resolve_roles(doc).segments["wall_solid"]
    assert segs == [(0.0, 0.0, 1000.0, 0.0), (1000.0, 0.0, 1000.0, 500.0)]


def _scope_doc(tmp_path, name, off_band):
    """带内 4.0×3.0 净空双线墙房间 + 房内两条 45° FIN；可选带外 45° 墙线与房名。"""
    doc = ezdxf.new("R2018")
    for n in ("WALL", "FIN", "P-TEXT"):
        doc.layers.add(n)
    msp = doc.modelspace()
    for y in (0, 200):
        msp.add_line((0, y), (4400, y), dxfattribs={"layer": "WALL"})
    for y in (3200, 3400):
        msp.add_line((0, y), (4400, y), dxfattribs={"layer": "WALL"})
    for x in (0, 200):
        msp.add_line((x, 0), (x, 3400), dxfattribs={"layer": "WALL"})
    for x in (4200, 4400):
        msp.add_line((x, 0), (x, 3400), dxfattribs={"layer": "WALL"})
    msp.add_line((500, 500), (900, 900), dxfattribs={"layer": "FIN"})
    msp.add_line((1600, 500), (2000, 900), dxfattribs={"layer": "FIN"})
    msp.add_text("客厅", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((1000, 1500))
    if off_band:
        msp.add_line((20000, 20000), (25000, 25000), dxfattribs={"layer": "WALL"})
        msp.add_text("主卧", height=250,
                     dxfattribs={"layer": "P-TEXT"}).set_placement((21000, 21000))
    p = tmp_path / f"{name}.dxf"
    doc.saveas(p)
    return ezdxf.readfile(p)


def test_resolve_roles_scope_bounds_excludes_offband_geometry(tmp_path):
    band = (0, 0, 4400, 3400)
    doc_off = _scope_doc(tmp_path, "with_off", True)
    base = resolve_roles(_scope_doc(tmp_path, "base", False))

    # 无裁剪：带外 45° 墙把 45° 计入主方向，房内 45° FIN 被误判为轴向
    glob = resolve_roles(doc_off)
    assert len(glob.segments["wall_solid"]) == 9          # 8 带内 + 1 带外 45°
    assert [t for _, _, t in glob.labels] == ["客厅", "主卧"]
    assert len(glob.segments["wall_fin_axial"]) == 2

    # 按房间边界裁剪：带外墙线与标签剔除，45° 不是带内主方向 → 无轴向 FIN
    scoped = resolve_roles(doc_off, scope_bounds=band)
    assert scoped.bounds == band
    assert scoped.segments["wall_solid"] == base.segments["wall_solid"]
    assert len(scoped.segments["wall_solid"]) == 8
    assert [t for _, _, t in scoped.labels] == ["客厅"]
    assert scoped.segments["wall_fin_axial"] == base.segments["wall_fin_axial"] == []
    assert scoped.segments["wall_fin_part"] == []


def test_resolve_roles_scope_bounds_kept_when_doc_empty():
    doc = ezdxf.new("R2018")
    band = (52161000.0, 227791000.0, 52186500.0, 227815500.0)
    roles = resolve_roles(doc, scope_bounds=band)
    assert roles.bounds == band
    assert roles.labels == []
    assert roles.audit_fallback == []
    assert all(segs == [] for segs in roles.segments.values())
