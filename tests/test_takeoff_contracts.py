"""里程碑 A 契约测试：几何主源引擎与既有 schema/接口之间的强制约定。

Task 1 只锁定三处 nullable 新列的“名字 + 可空”契约，后续任务（T5 定稿底图、
T8 吊顶参数假设、T10 钉行冻结）按这三个名字读写，改名即破链。
"""
import sqlalchemy as sa
from app import create_app, db


def _make_app(tmp_path):
    class C:
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/t.db"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        SECRET_KEY = "t"
        ADMIN_EMAIL = "t@t.local"  # create_app seed 管理员时必读，缺键会 KeyError
        UPLOAD_FOLDER = str(tmp_path / "up")
        REPORTS_FOLDER = str(tmp_path / "rp")
        WTF_CSRF_ENABLED = False

    return create_app(C)


def test_phase_a_columns_exist(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        insp = sa.inspect(db.engine)
        drawing_cols = {c["name"]: c for c in insp.get_columns("drawings")}
        project_cols = {c["name"]: c for c in insp.get_columns("projects")}
        measurement_cols = {c["name"]: c for c in insp.get_columns("measurements")}

    assert "is_base" in drawing_cols
    assert "takeoff_params" in project_cols
    assert "manual_pinned" in measurement_cols
    # 三列都必须可空：NULL 是“未人工指定/NULL 语义”的显式表达，不得退化为默认值
    assert drawing_cols["is_base"]["nullable"] is True
    assert project_cols["takeoff_params"]["nullable"] is True
    assert measurement_cols["manual_pinned"]["nullable"] is True


def test_project_type_vocabulary_contract():
    """设计 §5.2：业态枚举 5 类，商场/酒店无默认层高，列默认值为 sample_flat。"""
    from app.models import PROJECT_TYPES, Project

    assert [t[0] for t in PROJECT_TYPES] == [
        "sample_flat", "showroom", "mall", "hotel", "office",
    ]
    heights = {pt_id: fh for pt_id, _name, fh in PROJECT_TYPES}
    assert heights == {
        "sample_flat": 2.80,
        "showroom": 4.00,
        "mall": None,
        "hotel": None,
        "office": 3.00,
    }
    assert Project.project_type.default.arg == "sample_flat"


def test_explicit_null_floor_height_survives_insert(tmp_path):
    """显式 None 不得被列默认值覆盖：商场/酒店创建后必须保持 NULL。

    SQLAlchemy 的 Python 侧 `default=2.80` 在 INSERT 时对显式 None 也会生效，
    因此本测试是 models.Project.default_floor_height 去掉 default 的回归闸门。
    """
    from app.models import Project, User

    app = _make_app(tmp_path)
    with app.app_context():
        admin = User.query.filter_by(is_admin=True).first()
        for project_type in ("mall", "hotel"):
            project = Project(
                name=f"{project_type}-project",
                project_type=project_type,
                default_floor_height=None,
                user_id=admin.id,
            )
            db.session.add(project)
            db.session.commit()
            db.session.refresh(project)
            assert project.default_floor_height is None, (
                f"{project_type} 的显式 NULL 层高被默认值覆盖为 "
                f"{project.default_floor_height!r}"
            )


from app.core.takeoff.types import Coverage, Row, TakeoffParams


def test_row_unit_mapping_is_declared():
    r = Row(division_id=1, label='客厅-地面', category='dxf_floor',
            unit='m2', value=12.0, formula_text='4.0×3.0=12.00')
    assert r.check is None or isinstance(r.check, dict)
    assert r.unit == 'm2'


def test_requires_floor_height_only_for_mall_hotel_and_unknown():
    assert TakeoffParams(project_type='mall').requires_floor_height() is True
    assert TakeoffParams(project_type='hotel').requires_floor_height() is True
    assert TakeoffParams(project_type='lobby').requires_floor_height() is True   # 未知→同待遇
    assert TakeoffParams(project_type='sample_flat').requires_floor_height() is False
    assert TakeoffParams(project_type='office').requires_floor_height() is False
    assert TakeoffParams(project_type='showroom').requires_floor_height() is False


def test_gate_opens_once_floor_height_supplied():
    """闸门只看“是否已给出层高”：无默认业态一旦填写即放行，无需改 project_type。"""
    assert TakeoffParams(project_type='mall', floor_height_mm=3200).requires_floor_height() is False
    assert TakeoffParams(project_type='hotel', floor_height_mm=3200).requires_floor_height() is False


def test_effective_floor_height_prefers_user_input_then_type_default():
    assert TakeoffParams(project_type='sample_flat').effective_floor_height_mm() == 2800.0
    assert TakeoffParams(project_type='showroom').effective_floor_height_mm() == 4000.0
    assert TakeoffParams(project_type='office').effective_floor_height_mm() == 3000.0
    # mall/hotel 无业态默认：未填写时必须为 None，不得伪造层高
    assert TakeoffParams(project_type='mall').effective_floor_height_mm() is None
    assert TakeoffParams(project_type='sample_flat', floor_height_mm=3200
                         ).effective_floor_height_mm() == 3200


def test_height_source_never_fakes_business_type_default():
    """来源字符串会进客户行，无默认值时必须是“待填写”而不是“业态默认”。"""
    assert TakeoffParams(project_type='mall', floor_height_mm=3200).height_source() == '用户填写'
    assert TakeoffParams(project_type='sample_flat').height_source() == '业态默认'
    assert TakeoffParams(project_type='mall').height_source() == '待填写'
    assert TakeoffParams(project_type='hotel').height_source() == '待填写'
    assert TakeoffParams(project_type='lobby').height_source() == '待填写'   # 未知业态同待遇


def test_coverage_mark_and_rows():
    c = Coverage()
    c.mark('客厅', '地面', '有源', 'dxf')
    c.mark('客厅', '墙面', '待参数')
    rows = c.rows()
    assert {'space': '客厅', 'part': '墙面', 'status': '待参数', 'source': None} in rows
    assert len(rows) == 2

    # 同键再标记＝覆盖（不追加）：定稿底图重跑时同一格不得出现两行
    c.mark('客厅', '墙面', '有源', 'dxf')
    rows = c.rows()
    assert len(rows) == 2
    assert {'space': '客厅', 'part': '墙面', 'status': '有源', 'source': 'dxf'} in rows
    assert {'space': '客厅', 'part': '墙面', 'status': '待参数', 'source': None} not in rows
