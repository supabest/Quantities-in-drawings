"""里程碑 A 契约测试：几何主源引擎与既有 schema/接口之间的强制约定。

Task 1 只锁定三处 nullable 新列的“名字 + 可空”契约，后续任务（T5 定稿底图、
T8 吊顶参数假设、T10 钉行冻结）按这三个名字读写，改名即破链。
"""
import sqlalchemy as sa
from app import create_app, db


def test_phase_a_columns_exist(tmp_path):
    class C:
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/t.db"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        SECRET_KEY = "t"
        ADMIN_EMAIL = "t@t.local"  # create_app seed 管理员时必读，缺键会 KeyError
        UPLOAD_FOLDER = str(tmp_path / "up")
        REPORTS_FOLDER = str(tmp_path / "rp")
        WTF_CSRF_ENABLED = False

    app = create_app(C)
    with app.app_context():
        db.create_all()
        insp = sa.inspect(db.engine)
        assert "is_base" in {c["name"] for c in insp.get_columns("drawings")}
        assert "takeoff_params" in {c["name"] for c in insp.get_columns("projects")}
        assert "manual_pinned" in {c["name"] for c in insp.get_columns("measurements")}
        cols = {c["name"]: c for c in insp.get_columns("measurements")}
        assert cols["manual_pinned"]["nullable"] is True


def test_project_type_vocabulary_contract():
    """设计 §5.2：业态枚举 5 类，商场/酒店无默认层高，列默认值为 sample_flat。"""
    from app.models import PROJECT_TYPES, Project

    assert [t[0] for t in PROJECT_TYPES] == [
        "sample_flat", "showroom", "mall", "hotel", "office",
    ]
    heights = {pt_id: fh for pt_id, _name, fh in PROJECT_TYPES}
    assert heights["mall"] is None
    assert heights["hotel"] is None
    assert Project.project_type.default.arg == "sample_flat"
