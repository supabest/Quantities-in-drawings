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
