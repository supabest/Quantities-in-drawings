"""Task 10：Phase 1.2 接线 / 清旧行 / 钉行冻结 / 层高闸门 的 Web 级验收。

夹具复用 T6 的三间房 DXF（`tests.test_takeoff_enclosure._plan`，净面积合计 19.73）。
三间房已校准数值：地面 19.73 / 天棚 19.73 / 墙面 81.89(逐行舍入后 81.88) /
踢脚 29.02 / 洞口 1 樘。
"""
import json
import os
import shutil
import sys
from types import SimpleNamespace

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)

import ezdxf  # noqa: E402
import config as _cfg  # noqa: E402
from tests.test_takeoff_enclosure import _plan  # noqa: E402

TOL = 0.05


@pytest.fixture()
def web_env(tmp_path):
    """临时 DB + 上传/报告目录；管理员密码显式钉定供登录。"""
    up = tmp_path / "uploads"
    rp = tmp_path / "reports"
    up.mkdir()
    rp.mkdir()

    class T(_cfg.Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp_path / "t.db")
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        SECRET_KEY = "t"
        UPLOAD_FOLDER = str(up)
        REPORTS_FOLDER = str(rp)
        ADMIN_EMAIL = "admin@decoration-quant.com"
        ADMIN_PASSWORD = "admin123456"
        WTF_CSRF_ENABLED = False

    from app import create_app, db

    app = create_app(T)
    with app.app_context():
        from app.models import Project, User

        admin = User.query.filter_by(is_admin=True).first()
        project = Project(name="三间房", project_type="sample_flat",
                          default_floor_height=2.8, user_id=admin.id)
        db.session.add(project)
        db.session.commit()
        pid, uid = project.id, admin.id
    yield app, pid, uid


def _copy_plan(tmp_path, app, name="three.dxf"):
    """_plan 已 saveas 到 tmp_path/three.dxf；复制进上传目录并返回路径。"""
    _plan(tmp_path)
    dst = os.path.join(app.config["UPLOAD_FOLDER"], name)
    shutil.copy(tmp_path / "three.dxf", dst)
    return dst


def _add_dxf_drawing(pid, name="three.dxf"):
    from app import db
    from app.models import Drawing

    d = Drawing(filename=name, original_name=name, file_type="dxf", project_id=pid)
    db.session.add(d)
    db.session.commit()


def _division_sums(pid):
    """div1/2/3 面积、div4 樘数、div5 长度。"""
    from app.models import Measurement

    rows = Measurement.query.filter_by(project_id=pid).all()
    sums = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0, 5: 0.0}
    for m in rows:
        if m.division_id in (1, 2, 3):
            sums[m.division_id] += m.area or 0.0
        elif m.division_id == 4:
            sums[4] += m.count or 0
        elif m.division_id == 5:
            sums[5] += m.length or 0.0
    return sums


def test_process_twice_does_not_accumulate(web_env, tmp_path):
    app, pid, uid = web_env
    _copy_plan(tmp_path, app)
    with app.app_context():
        from app import db
        from app.models import Measurement, Project
        from app.processing import process_project

        _add_dxf_drawing(pid)
        p = Project.query.get(pid)
        process_project(p, p.drawings.all())
        n1 = Measurement.query.filter_by(project_id=pid).count()
        s1 = _division_sums(pid)

        process_project(p, p.drawings.all())
        n2 = Measurement.query.filter_by(project_id=pid).count()
        s2 = _division_sums(pid)

        # 清旧行 → 行数稳定，数值不得叠加（尤其 div3 不得 19.73→39.46）
        assert n2 == n1
        assert abs(s1[1] - 19.73) <= TOL
        assert abs(s1[2] - 81.89) <= TOL
        assert abs(s1[3] - 19.73) <= TOL
        assert s1[4] == 1
        assert abs(s1[5] - 29.02) <= TOL
        for div in (1, 2, 3, 5):
            assert abs(s2[div] - s1[div]) < 1e-6, (div, s1[div], s2[div])
        assert s2[4] == s1[4]


def test_manual_pinned_and_history_rows_survive_recompute(web_env, tmp_path):
    app, pid, uid = web_env
    _copy_plan(tmp_path, app)
    with app.app_context():
        from app import db
        from app.models import Measurement, Project
        from app.processing import process_project

        _add_dxf_drawing(pid)
        p = Project.query.get(pid)
        process_project(p, p.drawings.all())
        n_recognized = Measurement.query.filter_by(project_id=pid).count()

        db.session.add(Measurement(
            project_id=pid, division_id=2, label="人工钉定-墙面", area=1.23,
            unit="m²", source_tag="图纸实测", manual_pinned=True))
        db.session.add(Measurement(
            project_id=pid, division_id=6, label="历史参照-柜体", area=9.9,
            unit="m²", source_tag="历史参照"))
        db.session.commit()

        process_project(p, p.drawings.all())

        pinned = Measurement.query.filter_by(project_id=pid, manual_pinned=True).all()
        assert len(pinned) == 1
        assert abs(pinned[0].area - 1.23) < 1e-9
        history = Measurement.query.filter_by(
            project_id=pid, source_tag="历史参照").all()
        assert len(history) == 1
        assert abs(history[0].area - 9.9) < 1e-9

        recognized = [m for m in Measurement.query.filter_by(project_id=pid).all()
                      if not m.manual_pinned and m.source_tag != "历史参照"]
        assert len(recognized) == n_recognized, (len(recognized), n_recognized)


def test_clear_only_recognized_tags(web_env):
    app, pid, uid = web_env
    with app.app_context():
        from app import db
        from app.models import SOURCE_TAGS, Measurement, Project
        from app.core.takeoff import clear_previous_measurements

        for tag, _icon, _desc, _level in SOURCE_TAGS:
            db.session.add(Measurement(
                project_id=pid, division_id=6, label="tag-" + tag,
                area=1.0, unit="m²", source_tag=tag))
        db.session.add(Measurement(
            project_id=pid, division_id=6, label="钉定行", area=2.0,
            unit="m²", source_tag="图纸实测", manual_pinned=True))
        # PDF 行被人工改过 → 钉 manual_pinned=True；钉定保护优先于清识别产物
        db.session.add(Measurement(
            project_id=pid, division_id=6, label="PDF钉定行", area=3.0,
            unit="m²", source_tag="PDF语义提取", manual_pinned=True))
        db.session.commit()

        clear_previous_measurements(Project.query.get(pid))
        db.session.flush()

        # 识别产物（含 PDF语义提取）全清；历史参照整批保留；钉定行整行冻结。
        # brief 的剩余集 {"历史参照", "图纸实测"} 里后者即钉定行；本用例另加了
        # 钉定 PDF 行，故按「钉定 / 未钉定」分别断言。
        rows = Measurement.query.filter_by(project_id=pid).all()
        unpinned = {m.source_tag for m in rows if not m.manual_pinned}
        pinned = {m.source_tag for m in rows if m.manual_pinned}
        assert unpinned == {"历史参照"}
        assert pinned == {"图纸实测", "PDF语义提取"}
        # 未钉定 PDF 行被清、钉定 PDF 行保留
        pdf_rows = [m for m in rows if m.source_tag == "PDF语义提取"]
        assert [m.label for m in pdf_rows] == ["PDF钉定行"]
        assert pdf_rows[0].manual_pinned is True
        assert len([m for m in rows if m.manual_pinned]) == 2


def test_height_gate_blocks_mall_without_height(web_env):
    app, pid, uid = web_env
    with app.app_context():
        from app import db
        from app.models import Drawing, Project

        p = Project.query.get(pid)
        p.project_type = "mall"
        p.default_floor_height = None
        db.session.add(Drawing(filename="missing.dxf", original_name="missing.dxf",
                               file_type="dxf", project_id=pid))
        db.session.commit()

    client = app.test_client()
    resp = client.post("/auth/login", data={
        "email": app.config["ADMIN_EMAIL"],
        "password": app.config["ADMIN_PASSWORD"],
    }, follow_redirects=True)
    assert resp.status_code == 200

    blocked = client.post("/api/process/%d" % pid)
    assert blocked.status_code == 400
    body = blocked.get_json()
    assert body and "层高" in body.get("error", "")

    with app.app_context():
        from app import db
        from app.models import Project

        p = Project.query.get(pid)
        p.default_floor_height = 3.2
        db.session.commit()

    allowed = client.post("/api/process/%d" % pid)
    assert allowed.status_code == 200, allowed.get_data(as_text=True)[:500]


def test_params_from_project_maps_json():
    from app.core.takeoff import params_from_project

    project = SimpleNamespace(
        project_type="sample_flat",
        default_floor_height=3.17,
        takeoff_params=json.dumps({
            "slab_thickness_mm": 100,
            "floor_finish_mm": 50,
            "seals": [[0, 0, 1, 0]],
            "window_sill_mm": 600,
        }),
    )
    params = params_from_project(project)
    assert params.floor_height_mm == 3170
    assert params.slab_mm == 100
    assert params.floor_finish_mm == 50
    assert params.window_sill_mm == 600
    assert params.seals == [(0, 0, 1, 0)]
    assert params.requires_floor_height() is False
    assert params.height_source() == "用户填写"


def test_phase3_skips_dxf_rooms_and_flagged_divisions(web_env):
    app, pid, uid = web_env
    with app.app_context():
        from app import db
        from app.models import Measurement, Project
        from app.processing import _generate_derived_measurements

        db.session.add(Measurement(
            project_id=pid, division_id=1, category="room", label="图片房间",
            area=10.0, perimeter=14.0, unit="m²"))
        db.session.add(Measurement(
            project_id=pid, division_id=1, category="dxf_floor", label="DXF房间",
            area=7.0, perimeter=20.0, unit="m²"))
        db.session.commit()

        derived = _generate_derived_measurements(
            Project.query.get(pid), 2.8, 1.0, skip_divisions={2, 5})

        assert not [m for m in derived if m.division_id in (2, 5)]
        ceilings = [m for m in derived if m.division_id == 3]
        assert len(ceilings) == 1
        assert abs(ceilings[0].area - 10.0) < 1e-9


def _one_room_dxf(path, label_xy=(0, 1500)):
    """单间双线墙 + 一个压在墙线上的房间标签。"""
    doc = ezdxf.new("R2018", setup=True)
    for name in ("WALL", "P-TEXT"):
        doc.layers.add(name)
    msp = doc.modelspace()
    x0, y0, w, h = 0, 0, 4000, 3000
    for yy in (y0, y0 + h):
        msp.add_line((x0, yy), (x0 + w + 400, yy), dxfattribs={"layer": "WALL"})
        msp.add_line((x0, yy + 200), (x0 + w + 400, yy + 200),
                     dxfattribs={"layer": "WALL"})
    for xx in (x0, x0 + 200, x0 + w + 200, x0 + w + 400):
        msp.add_line((xx, y0), (xx, y0 + h + 400), dxfattribs={"layer": "WALL"})
    msp.add_text("压墙房", height=250,
                 dxfattribs={"layer": "P-TEXT"}).set_placement(label_xy)
    doc.saveas(path)
    return doc


def test_run_reports_unplaced_room_label(tmp_path):
    from app.core.takeoff import run

    path = tmp_path / "wall_label.dxf"
    _one_room_dxf(path)

    project = SimpleNamespace(id=1, project_type="sample_flat",
                              default_floor_height=2.8,
                              takeoff_params=None, scale_count=None)
    drawing = SimpleNamespace(filename="wall_label.dxf",
                              original_name="wall_label.dxf", is_base=None)
    result = run(project, [drawing], str(tmp_path))

    codes = [issue.code for issue in result.issues]
    assert "unplaced_room_labels" in codes
    issue = next(i for i in result.issues if i.code == "unplaced_room_labels")
    assert "压墙房" in issue.detail["labels"]
    assert issue.detail.get("anchors")


# ---- T10 修复轮 1：C1 单图兜底 / I1 清 PDF 行 / I2 单 DXF / I4 逐间层高闸门 ----
# 合成 DXF 一律现场造（不读 装修图纸/），纯 run() 用例沿用 SimpleNamespace 写法。


def _project(**kw):
    base = dict(id=1, project_type="sample_flat", default_floor_height=2.8,
                takeoff_params=None, scale_count=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _drawing(name, **kw):
    base = dict(filename=name, original_name=name, is_base=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _new_doc(layers=("WALL", "P-TEXT")):
    doc = ezdxf.new("R2018", setup=True)
    for name in layers:
        doc.layers.add(name)
    return doc, doc.modelspace()


def _box(msp, x0, y0, w, h):
    """200 厚双线墙盒（与 _one_room_dxf 同构：内净空 w×(h-200)）。"""
    for yy in (y0, y0 + h):
        msp.add_line((x0, yy), (x0 + w + 400, yy), dxfattribs={"layer": "WALL"})
        msp.add_line((x0, yy + 200), (x0 + w + 400, yy + 200),
                     dxfattribs={"layer": "WALL"})
    for xx in (x0, x0 + 200, x0 + w + 200, x0 + w + 400):
        msp.add_line((xx, y0), (xx, y0 + h + 400), dxfattribs={"layer": "WALL"})


def _two_clusters_dxf(path):
    """两个 4000×2800 净空盒相距 30m；只有一簇有房间标签（打分唯一）。"""
    doc, msp = _new_doc()
    _box(msp, 0, 0, 4000, 3000)
    _box(msp, 30000, 0, 4000, 3000)
    msp.add_text("客厅", height=250,
                 dxfattribs={"layer": "P-TEXT"}).set_placement((2000, 1500))
    doc.saveas(path)


def _square_room_dxf(path):
    """3500×3500 净空单间（200 厚双线墙），墙地比 ≈3.39。"""
    doc, msp = _new_doc()
    _box(msp, 0, 0, 3500, 3700)
    msp.add_text("客厅", height=250,
                 dxfattribs={"layer": "P-TEXT"}).set_placement((1750, 1850))
    doc.saveas(path)


def _leaky_room_dxf(path):
    """单间上墙留 2000 缺口（>AUTO 1200 且无门 → 不会被桥接）。"""
    doc, msp = _new_doc()
    _box(msp, 0, 0, 4000, 3000)
    for e in list(msp.query('LINE[layer=="WALL"]')):
        s, t = e.dxf.start, e.dxf.end
        if abs(s.y - t.y) < 1 and 2999 <= s.y <= 3201:
            e.destroy()
    for yy in (3000, 3200):
        msp.add_line((0, yy), (1000, yy), dxfattribs={"layer": "WALL"})
        msp.add_line((3000, yy), (4400, yy), dxfattribs={"layer": "WALL"})
    msp.add_text("卫生间", height=250,
                 dxfattribs={"layer": "P-TEXT"}).set_placement((2000, 1500))
    doc.saveas(path)


def _two_rooms_dxf(path, levels=()):
    """客厅（4000×2800 净空）+ 卧室（3000×2800 净空），可注入吊顶标高文字。"""
    doc, msp = _new_doc(("WALL", "P-TEXT", "CEILING-ANNO"))
    _box(msp, 0, 0, 4000, 3000)
    _box(msp, 6000, 0, 3000, 3000)
    msp.add_text("客厅", height=250,
                 dxfattribs={"layer": "P-TEXT"}).set_placement((2000, 1500))
    msp.add_text("卧室", height=250,
                 dxfattribs={"layer": "P-TEXT"}).set_placement((7500, 1500))
    for text, xy in levels:
        msp.add_text(text, height=250,
                     dxfattribs={"layer": "CEILING-ANNO"}).set_placement(xy)
    doc.saveas(path)


def _single_line_dxf(path):
    """只有一根水平线：簇 bbox 零高，build_rooms 必须抛错。"""
    doc, msp = _new_doc(("WALL",))
    msp.add_line((0, 0), (4000, 0), dxfattribs={"layer": "WALL"})
    doc.saveas(path)


def _div_sums(result):
    sums = {}
    for r in result.rows:
        sums[r.division_id] = sums.get(r.division_id, 0.0) + (r.value or 0.0)
    return sums


def test_run_measures_only_base_cluster_not_global_bounds(tmp_path):
    """I5-1 变异守卫（全局 roles + 全局 bounds → 簇范围）：两簇相距 30m、只有一簇有
    标签。实测「全局 roles + base.bounds」与「局部 roles + base.bounds」同为 9.75
    （build_rooms 的栅格窗口会裁掉框外段，roles 契约本就要求先按 scope 裁），
    真正会翻倍的是「全局 roles + 全局 bounds」→ 19.50；本测试钉住后者。"""
    from app.core.takeoff import run

    _two_clusters_dxf(tmp_path / "two_clusters.dxf")
    result = run(_project(), [_drawing("two_clusters.dxf")], str(tmp_path))

    floor = [r for r in result.rows if r.division_id == 1]
    assert len(floor) == 1, [r.label for r in floor]
    assert abs(sum(r.value for r in floor) - 9.75) <= TOL     # 单间净空，不是两间


def test_run_reconcile_band_follows_project_params(tmp_path):
    """I5-2 变异守卫（reconcile.apply 丢 params）：3.5×3.5 净空单间墙地比 3.39，
    sample_flat 带 (2.0~3.0) 必须报 wall_floor_ratio；showroom 带 (2.5~3.5) 不报。"""
    from app.core.takeoff import run

    _square_room_dxf(tmp_path / "square.dxf")
    drawing = _drawing("square.dxf")

    sample = run(_project(), [drawing], str(tmp_path))
    sums = _div_sums(sample)
    assert abs(sums[1] - 10.76) <= TOL and abs(sums[2] - 36.51) <= TOL
    ratio = round(sums[2] / sums[1], 2)
    assert 3.0 < ratio <= 3.5                 # 前提：越 sample_flat 带、落 showroom 带
    assert "wall_floor_ratio" in {i.code for i in sample.issues}

    showroom = run(_project(project_type="showroom"), [drawing], str(tmp_path))
    assert "wall_floor_ratio" not in {i.code for i in showroom.issues}
    assert abs(_div_sums(showroom)[2] - sums[2]) < 1e-6   # 同一行，只是判定带不同


def test_run_reports_enclosure_leak_with_labels(tmp_path):
    """I5-3 变异守卫（enclosure_leak 缺失）：顶部留 2000 缺口 → 漏房必须显式披露。"""
    from app.core.takeoff import run

    _leaky_room_dxf(tmp_path / "leak.dxf")
    result = run(_project(), [_drawing("leak.dxf")], str(tmp_path))

    leaks = [i for i in result.issues if i.code == "enclosure_leak"]
    assert len(leaks) == 1
    assert "卫生间" in leaks[0].detail["labels"]
    assert "卫生间" in leaks[0].message
    assert not [r for r in result.rows if r.division_id == 1]


def test_mall_division2_gated_per_room_by_ceiling_levels(tmp_path):
    """I4 变异守卫（整批丢 div2 → 逐间判）：mall 无层高时无标高的房间不出墙面行，
    有 +2800 吊顶标高的房间照常用 soffit 出墙；param issue 项目级只发一次。"""
    from app.core.takeoff import run

    path = tmp_path / "mall.dxf"
    _two_rooms_dxf(path)
    mall = _project(project_type="mall", default_floor_height=None)
    drawing = _drawing("mall.dxf")

    dropped = run(mall, [drawing], str(tmp_path))
    assert not [r for r in dropped.rows if r.division_id == 2]
    assert len([r for r in dropped.rows if r.division_id == 1]) == 2   # 地面照出
    gate = [i for i in dropped.issues if i.code == "param_required_floor_height"]
    assert len(gate) == 1                                    # 不按图纸重复追加

    _two_rooms_dxf(path, levels=[("+2800", (2000, 1500))])   # 客厅有吊顶标高
    kept = run(mall, [drawing], str(tmp_path))
    wall = [r for r in kept.rows if r.division_id == 2]
    assert len(wall) == 1 and "客厅" in wall[0].label
    assert "2.800" in wall[0].formula_text                   # 用吊顶净高，不用兜底
    assert "吊顶净高" in wall[0].remarks
    assert abs(wall[0].value - 35.39) <= 0.1
    assert len([i for i in kept.issues
                if i.code == "param_required_floor_height"]) == 1


def test_run_reports_no_dxf_base(tmp_path):
    """I2 降级披露：项目没有 DXF 时不得静默返回空结果。"""
    from app.core.takeoff import run

    result = run(_project(), [], str(tmp_path))
    assert result.rows == []
    assert [i.code for i in result.issues] == ["no_dxf_base"]


def test_run_reports_missing_dxf_file(tmp_path):
    """I5-5 变异守卫（dxf_missing_file 缺失）：文件名指向不存在路径必须披露。"""
    from app.core.takeoff import run

    result = run(_project(), [_drawing("nope.dxf")], str(tmp_path))
    issues = [i for i in result.issues if i.code == "dxf_missing_file"]
    assert len(issues) == 1
    assert issues[0].detail["file"] == "nope.dxf"
    assert result.rows == []


def test_run_isolates_dxf_processing_failure(tmp_path):
    """C1 变异守卫（几何管线异常外抛）：单根水平线（簇 bbox 零高）→ 只废这张图，
    好图行仍在、issue=dxf_processing_failed。里程碑 A 默认拒算多 DXF，故两图均
    人工钉定后才同轮处理（I2 裁定：is_base=True 只选文件）。"""
    from app.core.takeoff import run

    _single_line_dxf(tmp_path / "line.dxf")
    _square_room_dxf(tmp_path / "good.dxf")
    result = run(_project(), [_drawing("line.dxf", is_base=True),
                              _drawing("good.dxf", is_base=True)], str(tmp_path))

    failed = [i for i in result.issues if i.code == "dxf_processing_failed"]
    assert len(failed) == 1
    assert failed[0].detail["file"] == "line.dxf"
    floor = [r for r in result.rows if r.division_id == 1]
    assert abs(sum(r.value for r in floor) - 10.76) <= TOL
    assert abs(_div_sums(result)[2] - 36.51) <= TOL


def test_run_refuses_multi_dxf_unless_pinned(tmp_path):
    """I2 变异守卫（多 DXF 求和/猜图）：无钉定必须拒算且 rows 为空；钉定后只量
    钉定的那张，数值不得翻倍。"""
    from app.core.takeoff import run

    _square_room_dxf(tmp_path / "a.dxf")
    shutil.copy(tmp_path / "a.dxf", tmp_path / "b.dxf")

    refused = run(_project(), [_drawing("a.dxf"), _drawing("b.dxf")], str(tmp_path))
    issues = [i for i in refused.issues if i.code == "multi_dxf_unsupported"]
    assert len(issues) == 1
    assert issues[0].detail["files"] == ["a.dxf", "b.dxf"]
    assert refused.rows == []
    assert refused.coverage.rows() == []

    pinned = run(_project(), [_drawing("a.dxf", is_base=True), _drawing("b.dxf")],
                 str(tmp_path))
    assert "multi_dxf_unsupported" not in {i.code for i in pinned.issues}
    floor = [r for r in pinned.rows if r.division_id == 1]
    assert len(floor) == 1
    assert abs(sum(r.value for r in floor) - 10.76) <= TOL   # 单间，不翻倍


def test_run_drops_divisions_with_role_layer_proxies(tmp_path, monkeypatch):
    """I5-8 变异守卫（t20 分级降级丢失）：WALL 层有代理对象 → 1/2/3/5 分部整批
    不出数，4 分部（该图 1 樘门）照出，issue detail 保留受影响分部。"""
    from app.core.takeoff import run
    from app.core.takeoff import layers as layers_mod

    _plan(tmp_path)          # three.dxf：三间房 + 1 樘门
    monkeypatch.setattr(
        layers_mod, "proxy_survey",
        lambda doc: layers_mod.SurveyReport(proxies_by_layer={"WALL": 1}))
    result = run(_project(), [_drawing("three.dxf")], str(tmp_path))

    assert {r.division_id for r in result.rows} == {4}
    assert len(result.rows) == 1
    issues = [i for i in result.issues if i.code == "t20_proxy_on_role_layer"]
    assert len(issues) == 1
    assert issues[0].detail["divisions"] == [1, 2, 3, 5]
    assert issues[0].detail["layers"] == ["WALL"]


def test_api_rolls_back_measurements_on_failure(web_env, tmp_path, monkeypatch):
    """C1 变异守卫（api 失败时直接 commit）：重算中途 flush 了删除再抛错，
    必须先 rollback —— 旧行仍在、status='failed'。"""
    app, pid, uid = web_env
    _copy_plan(tmp_path, app)
    with app.app_context():
        from app import db
        from app.models import Project

        _add_dxf_drawing(pid)
        p = Project.query.get(pid)
        p.historical_ref_prompted = True
        db.session.commit()

    client = app.test_client()
    resp = client.post("/auth/login", data={
        "email": app.config["ADMIN_EMAIL"],
        "password": app.config["ADMIN_PASSWORD"],
    }, follow_redirects=True)
    assert resp.status_code == 200

    first = client.post("/api/process/%d" % pid)
    assert first.status_code == 200, first.get_data(as_text=True)[:500]
    with app.app_context():
        from app.models import Measurement

        before = Measurement.query.filter_by(project_id=pid).count()
    assert before > 0                      # 前提：上一轮确有可被误删的行

    import app.api as api_mod

    def boom(project, drawings):
        from app import db
        from app.core.takeoff import clear_previous_measurements

        clear_previous_measurements(project)   # delete + flush，尚未 commit
        db.session.flush()
        raise RuntimeError("模拟几何阶段崩溃")

    monkeypatch.setattr(api_mod, "process_project", boom)
    failed = client.post("/api/process/%d" % pid)
    assert failed.status_code == 500

    with app.app_context():
        from app.models import Measurement, Project

        assert Measurement.query.filter_by(project_id=pid).count() == before
        assert Project.query.get(pid).status == "failed"


def test_params_from_project_tolerates_dirty_json():
    """I5-10 变异守卫（脏 JSON 直接抛错）：floor_height 非数值按缺省、seals 非
    列表整条丢弃；不得让整轮重算 500。"""
    from app.core.takeoff import params_from_project

    dirty = params_from_project(SimpleNamespace(
        project_type="sample_flat",
        takeoff_params=json.dumps({
            "floor_height_mm": "abc", "seals": 1, "slab_mm": "x",
            "floor_finish_mm": "y", "door_height_mm": "z"})))
    assert dirty.floor_height_mm is None
    assert dirty.slab_mm is None
    assert dirty.floor_finish_mm is None
    assert dirty.door_height_mm == 2100.0     # 脏值 → 保留长期默认口径
    assert dirty.seals == []

    partly = params_from_project(SimpleNamespace(
        project_type="sample_flat",
        takeoff_params=json.dumps({"seals": [[0, 0, 1, 0], "x", [1, 2, 3]]})))
    assert partly.seals == [(0.0, 0.0, 1.0, 0.0)]      # 合法项保留，坏项丢弃


DRAWINGS = os.path.join(BASE, "装修图纸", "227户型dxf")
DXF_227 = os.path.join(DRAWINGS, "05.227户型平面图 （施工图纸） rev-33.dxf")
SEAL_227 = (52176828, 227805421, 52179628, 227805421)


@pytest.mark.skipif(not os.path.isfile(DXF_227), reason="227 图纸未入库，仅本地跑")
@pytest.mark.slow
def test_web_chain_227_sealed_values(web_env):
    """里程碑 A 过关线：227 干净 DXF 经 process_project 落库 = 封账值，且重算不叠加。

    封账：地面 163.75 / 天棚 162.15 / 墙面 437.22 / 合计 763.12 m²，
    踢脚 166.73 m，洞口 42 樘。只认 Web Measurement 行，不读报告文本。
    """
    app, pid, _ = web_env
    shutil.copy(DXF_227, os.path.join(app.config["UPLOAD_FOLDER"], "227.dxf"))
    with app.app_context():
        from app import db
        from app.models import Measurement, Project
        from app.processing import process_project

        _add_dxf_drawing(pid, "227.dxf")
        p = Project.query.get(pid)
        p.takeoff_params = json.dumps({
            "floor_height_mm": 3170, "slab_mm": 100, "floor_finish_mm": 50,
            "seals": [list(SEAL_227)]})
        db.session.commit()

        process_project(p, p.drawings.all())
        n1 = Measurement.query.filter_by(project_id=pid).count()
        s1 = _division_sums(pid)
        assert abs(s1[1] - 163.75) <= TOL, s1
        assert abs(s1[3] - 162.15) <= TOL, s1
        assert abs(s1[2] - 437.22) <= TOL, s1
        assert abs(s1[1] + s1[2] + s1[3] - 763.12) <= 0.02, s1
        assert abs(s1[5] - 166.73) <= TOL, s1
        assert s1[4] == 42, s1

        process_project(p, p.drawings.all())
        n2 = Measurement.query.filter_by(project_id=pid).count()
        s2 = _division_sums(pid)
        assert n2 == n1
        for div in (1, 2, 3, 5):
            assert abs(s2[div] - s1[div]) < 1e-6, (div, s1[div], s2[div])
        assert s2[4] == s1[4]
