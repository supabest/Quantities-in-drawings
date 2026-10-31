"""Task 5：平面复制簇切分与底图选定。坐标单位 mm。"""
import types

import ezdxf

from app.core.takeoff import frames
from app.core.takeoff.frames import PlanCluster, cluster, pick_base
from app.core.takeoff.layers import resolve_roles

LAYERS = ("WALL", "P-TEXT", "DOOR", "WINDOW", "CURB")


def _doc():
    doc = ezdxf.new("R2018", setup=True)
    for name in LAYERS:
        doc.layers.add(name)
    return doc


def _plan(msp, dx=0.0, dy=0.0, labels=True, doors=True, windows=True, curbs=True):
    """4.4×3.4 外框、200mm 双线墙围合平面（8 段墙），可挂门/窗/反坎/房名。"""
    def add(a, b, layer):
        msp.add_line((a[0] + dx, a[1] + dy), (b[0] + dx, b[1] + dy),
                     dxfattribs={"layer": layer})

    for y in (0, 200):
        add((0, y), (4400, y), "WALL")
    for y in (3200, 3400):
        add((0, y), (4400, y), "WALL")
    for x in (0, 200):
        add((x, 0), (x, 3400), "WALL")
    for x in (4200, 4400):
        add((x, 0), (x, 3400), "WALL")
    if doors:
        add((600, 0), (1500, 0), "DOOR")
    if windows:
        add((3000, 3400), (3900, 3400), "WINDOW")
    if curbs:
        add((400, 0), (4400, 0), "CURB")
    if labels:
        msp.add_text("客厅", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement(
            (1000 + dx, 1500 + dy))


def test_cluster_and_pick_single_plan():
    doc = _doc()
    _plan(doc.modelspace(), windows=False, curbs=False)

    clusters = cluster(doc, resolve_roles(doc))
    assert len(clusters) == 1
    assert clusters[0].source == "model"
    assert clusters[0].drawings == []
    assert clusters[0].score >= 4

    base, issues = pick_base(clusters)
    assert base is clusters[0]
    assert issues == []


def test_pick_base_reports_tie():
    a = PlanCluster("model", (0.0, 0.0, 4400.0, 3400.0), 5, ["wall=8"], [])
    b = PlanCluster("model", (50000.0, 0.0, 54400.0, 3400.0), 5, ["wall=8"], [])

    base, issues = pick_base([a, b])
    assert base is None
    assert issues[0].code == "base_ambiguous"
    assert len(issues[0].detail["candidates"]) == 2


def test_cluster_splits_separated_copies():
    doc = _doc()
    msp = doc.modelspace()
    _plan(msp, 0, 0)
    _plan(msp, 50000, 0, labels=False, doors=False)

    clusters = cluster(doc, resolve_roles(doc))
    model = [c for c in clusters if c.source == "model"]
    assert len(model) == 2
    left = [c for c in model if c.bounds[0] < 10000]
    right = [c for c in model if c.bounds[0] >= 10000]
    assert len(left) == 1 and len(right) == 1
    assert left[0].bounds[2] < 10000          # 不得是全局 bounds
    assert right[0].bounds[0] >= 10000

    base, _issues = pick_base(clusters)
    assert base is left[0]


def test_copy_missing_doors_does_not_win():
    """227 套0（门/窗齐全）vs 套2（无门）的回归：门窗必须拆成两个子信号。"""
    doc = _doc()
    msp = doc.modelspace()
    _plan(msp, 0, 0)                                     # 6 分
    _plan(msp, 50000, 0, doors=False)                    # 5 分

    clusters = cluster(doc, resolve_roles(doc))
    left = [c for c in clusters if c.source == "model" and c.bounds[0] < 10000]
    right = [c for c in clusters if c.source == "model" and c.bounds[0] >= 10000]
    assert left[0].score == 6
    assert right[0].score == 5

    base, issues = pick_base(clusters)
    assert base is left[0]
    assert [i.code for i in issues] == ["base_alternatives"]


def test_layout_with_only_viewport_is_not_candidate():
    doc = _doc()
    _plan(doc.modelspace(), windows=False, curbs=False)
    sheet = doc.layouts.new("Sheet")
    sheet.add_viewport(center=(0, 0), size=(100, 100),
                       view_center_point=(0, 0), view_height=100)

    clusters = cluster(doc, resolve_roles(doc))
    assert [c.source for c in clusters] == ["model"]
    assert len(clusters) == 1


def test_paper_only_plan_reports_explicit_error():
    doc = _doc()  # 模型空间为空
    sheet = doc.layouts.new("Sheet")
    _plan(sheet, windows=False, curbs=False)

    clusters = cluster(doc, resolve_roles(doc))
    paper = [c for c in clusters if c.source == "paper"]
    assert len(paper) == 1
    assert paper[0].drawings == ["Sheet"]

    base, issues = pick_base(clusters)
    assert base is None
    codes = [i.code for i in issues]
    assert "paper_base_unconsumable" in codes
    assert [i for i in issues if i.code == "paper_base_unconsumable"][0].level == "error"


def test_scoped_roles_match_cluster():
    doc = _doc()
    msp = doc.modelspace()
    _plan(msp, 0, 0)
    _plan(msp, 50000, 0, labels=False, doors=False)

    clusters = cluster(doc, resolve_roles(doc))
    base, _issues = pick_base(clusters)
    assert base is not None
    scoped = resolve_roles(doc, scope_bounds=base.bounds)
    assert len(scoped.segments["wall_solid"]) == 8
    assert max(s[2] for s in scoped.segments["wall_solid"]) <= 4400


def test_pick_base_no_clusters_errors():
    base, issues = pick_base([])
    assert base is None
    assert len(issues) == 1
    assert issues[0].code == "no_geometry"
    assert issues[0].level == "error"


def test_pick_base_pinned_false_is_skipped():
    c = PlanCluster("model", (0.0, 0.0, 10.0, 10.0), 3, ["wall=1"], [])
    base, issues = pick_base([c], pinned=False)
    assert base is None
    assert issues[0].code == "drawing_not_base"


def test_paper_score_not_lower_than_model_warns():
    doc = _doc()
    _plan(doc.modelspace(), labels=False, doors=False, windows=False, curbs=False)
    sheet = doc.layouts.new("Sheet")
    _plan(sheet)

    clusters = cluster(doc, resolve_roles(doc))
    model = [c for c in clusters if c.source == "model"]
    paper = [c for c in clusters if c.source == "paper"]
    assert len(model) == 1 and model[0].score == 1
    assert len(paper) == 1 and paper[0].score == 6

    base, issues = pick_base(clusters)
    assert base is model[0]
    assert "paper_plan_not_consumable" in [i.code for i in issues]


def test_link_gap_default_and_components():
    assert frames.LINK_GAP_MM == 3000.0
    a = (0.0, 0.0, 1000.0, 1000.0)
    b = (3000.0, 0.0, 4000.0, 1000.0)          # 净距 2000mm
    assert len(frames._components([a, b], gap=3000)) == 1
    assert len(frames._components([a, b], gap=100)) == 2


def test_label_on_boundary_counts():
    score, reasons = frames._score(["wall_solid"], [(100.0, 50.0, "x")],
                                   (100.0, 50.0, 200.0, 100.0))
    assert score == 3
    assert "label=1" in reasons


def _wall_rect(dx=0.0, dy=0.0):
    """4.4×3.4 的 200mm 双线墙围合（8 段），与 _plan 同口径。"""
    segs = []
    for y in (0.0, 200.0):
        segs.append((dx, dy + y, dx + 4400.0, dy + y))
    for y in (3200.0, 3400.0):
        segs.append((dx, dy + y, dx + 4400.0, dy + y))
    for x in (0.0, 200.0):
        segs.append((dx + x, dy, dx + x, dy + 3400.0))
    for x in (4200.0, 4400.0):
        segs.append((dx + x, dy, dx + x, dy + 3400.0))
    return segs


def test_wall_fin_axial_does_not_bridge_copies():
    roles = types.SimpleNamespace(
        segments={"wall_solid": _wall_rect(0.0) + _wall_rect(50000.0),
                  "wall_fin_axial": [(0.0, 0.0, 54400.0, 0.0)],
                  "wall_fin_part": [], "door": [], "window": [], "curb": []},
        labels=[])
    assert len(frames._model_clusters(roles)) == 2


def test_paper_layout_block_geometry_is_candidate():
    doc = _doc()  # 模型空间为空
    blk = doc.blocks.new("PLAN")
    for s in _wall_rect():
        blk.add_line((s[0], s[1]), (s[2], s[3]), dxfattribs={"layer": "WALL"})
    sheet = doc.layouts.new("Sheet")
    sheet.add_blockref("PLAN", (0, 0))

    clusters = cluster(doc, resolve_roles(doc))
    paper = [c for c in clusters if c.source == "paper"]
    assert len(paper) == 1
    assert paper[0].drawings == ["Sheet"]
    assert paper[0].score == 1

