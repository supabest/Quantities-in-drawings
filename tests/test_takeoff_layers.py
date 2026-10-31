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


def test_affected_divisions_by_role():
    assert affected_divisions({"wall_solid"}) == {1, 2, 3, 5}
    assert affected_divisions({"door", "window"}) == {2, 4}
    assert affected_divisions({"curb"}) == {1, 3}
    assert affected_divisions({"room_label"}) == set()
    assert affected_divisions(set()) == set()
