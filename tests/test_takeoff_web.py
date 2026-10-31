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
        db.session.commit()

        clear_previous_measurements(Project.query.get(pid))
        db.session.flush()

        remaining = {m.source_tag for m in
                     Measurement.query.filter_by(project_id=pid).all()}
        assert remaining == {"历史参照", "PDF语义提取", "图纸实测"}
        assert Measurement.query.filter_by(
            project_id=pid, manual_pinned=True).count() == 1


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
