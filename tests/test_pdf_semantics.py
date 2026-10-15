"""Phase 5c PDF 语义提取回归测试。

Tier A：合成 PDF（CI 必跑，不依赖本地图纸），钉住文字/图名/分区原语与 Phase 1.5 落库。
Tier B：真实 111 页 227 施工图纸（仅本地），钉住去重收敛后的行数分布。
"""
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)

import fitz
import pytest

from app.core.pdf_parser import (
    extract_page_title, extract_room_labels, extract_material_codes,
    extract_dimensions, zone_polygons, point_in_zone,
)

PDF = os.path.join(BASE, '装修图纸', '227户型dxf',
                   '广州珠实金融城住宅项目--227户型施工图纸.pdf')


def _make_plan_pdf(path):
    """合成一页：图签图名 + 房间标签 + 材料代号 + 尺寸 + 闭合天花分区。"""
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    # 天花分区：两个闭合矩形（内空 ≥ min_area）
    page.draw_rect(fitz.Rect(100, 100, 300, 250), color=(0, 0, 0), width=1.5)
    page.draw_rect(fitz.Rect(350, 100, 600, 300), color=(0, 0, 0), width=1.5)
    # 文字：CJK 用内置字体，字号 <14 才会被当作尺寸标注
    page.insert_text((250, 180), '客厅', fontname='china-s', fontsize=10)
    page.insert_text((450, 200), '主卧', fontname='china-s', fontsize=10)
    page.insert_text((500, 260), 'A-01', fontsize=9)
    page.insert_text((200, 320), '3200', fontsize=9)
    page.insert_text((550, 350), '天花', fontname='china-s', fontsize=9)
    # 图签：「图纸名称」标签 + 正下方图名
    page.insert_text((700, 500), '图纸名称', fontname='china-s', fontsize=8)
    page.insert_text((700, 512), '天花布置图', fontname='china-s', fontsize=8)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture(scope='module')
def plan_page():
    tmp = os.path.join(tempfile.mkdtemp(), 'synthetic_plan.pdf')
    _make_plan_pdf(tmp)
    doc = fitz.open(tmp)
    yield doc[0]
    doc.close()


# ── Tier A：语义原语 ────────────────────────────────────────────────────
def test_page_title_reads_below_label(plan_page):
    """图名取「图纸名称」正下方文字，而不是同基线上的冒号或图例。"""
    assert extract_page_title(plan_page) == '天花布置图'


def test_room_labels_and_material_codes(plan_page):
    names = [n for n, x, y in extract_room_labels(plan_page, x_min=200)]
    assert '客厅' in names and '主卧' in names
    codes = [c for c, x, y in extract_material_codes(plan_page)]
    assert 'A-01' in codes


def test_dimensions_value_and_unit_group_captured(plan_page):
    """DIM_WITH_UNIT_RE 的单位组必须是捕获组，否则 group(2) 取不到单位。"""
    dims = extract_dimensions(plan_page)
    assert any(v == 3200 and u == 'mm' for v, x, y, u in dims)


def test_point_in_zone_uses_point_not_degenerate_polygon(plan_page):
    """落域判据必须构造 Point；早先用 Polygon([单点]) 恒不命中。"""
    zones = zone_polygons(plan_page, dpi=100, min_area_m2=0.5)
    assert len(zones) >= 2
    inside = zones[0]['centroid']
    assert point_in_zone(inside[0], inside[1], zones) == zones[0]['label']
    assert point_in_zone(-50, -50, zones, default=None) is None


# ── Tier A：Phase 1.5 落库 ─────────────────────────────────────────────
def _mk_project(name):
    """projects.user_id NOT NULL：必须先建用户再建项目。"""
    from app import db
    from app.models import Project, User

    u = User(username='t', email='t@t.local', is_approved=True, is_active=True)
    u.set_password('xxxxxx')
    db.session.add(u)
    db.session.commit()
    p = Project(name=name, project_type='residential', user_id=u.id)
    db.session.add(p)
    db.session.commit()
    return p


def test_pdf_semantics_persisted_with_project_id(tmp_path):
    """PDF 行必须带 project_id 并入库，否则 Web 端计量后清单为空。"""
    import config as _cfg
    from app import create_app, db
    from app.models import Measurement

    db_path = str(tmp_path / 't.db')
    up_dir = tmp_path / 'uploads'
    up_dir.mkdir()

    class T(_cfg.Config):
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + db_path
        SQLALCHEMY_ENGINE_OPTIONS = {}
        UPLOAD_FOLDER = str(up_dir)
        REPORTS_FOLDER = str(tmp_path / 'reports')
        WTF_CSRF_ENABLED = False

    (tmp_path / 'reports').mkdir()
    app = create_app(T)
    with app.app_context():
        db.create_all()
        p = _mk_project('合成图纸项目')
        pdf_name = 'synthetic.pdf'
        _make_plan_pdf(os.path.join(str(up_dir), pdf_name))
        from types import SimpleNamespace
        from app.processing import _process_pdf_drawings
        rows = _process_pdf_drawings([SimpleNamespace(filename=pdf_name)], p)
        db.session.commit()

        assert len(rows) > 0
        stored = Measurement.query.filter_by(project_id=p.id).all()
        assert len(stored) == len(rows)
        assert all(m.project_id == p.id for m in stored)
        assert all(m.source_tag == 'PDF语义提取' for m in stored)
        # 图标是实体列带默认值(📐/🟢)，不赋值会把 PDF 行伪装成图纸实测+高置信
        assert all(m.source_tag_icon == '📄' for m in stored)
        medium = [m for m in stored if m.confidence_level == 'medium']
        assert medium and all(m.confidence_icon == '🟡' for m in medium)
        assert any(m.category == 'pdf_ceiling_zone' for m in stored)
        # 图名门控：非天花页不应产出分区行
        assert any('天花布置图' in (m.position_desc or '') for m in stored)


# ── Tier B：真实图纸收敛基准 ───────────────────────────────────────────
@pytest.mark.skipif(not os.path.exists(PDF), reason='施工图纸 PDF 不在本地')
def test_full_set_dedup_convergence():
    """111 页整套图纸去重后必须收敛到 389 行及既定分类分布。"""
    import config as _cfg
    from app import create_app, db
    from app.processing import _process_pdf_drawings
    from collections import Counter

    class T(_cfg.Config):
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + tempfile.mktemp(suffix='.db')
        SQLALCHEMY_ENGINE_OPTIONS = {}
        UPLOAD_FOLDER = os.path.join(BASE, '装修图纸', '227户型dxf')
        REPORTS_FOLDER = tempfile.mkdtemp()

    app = create_app(T)
    with app.app_context():
        db.create_all()
        p = _mk_project('227')
        from types import SimpleNamespace
        rows = _process_pdf_drawings(
            [SimpleNamespace(filename=os.path.basename(PDF))], p)
        dist = Counter(m.category for m in rows)
        assert len(rows) == 389, len(rows)
        assert dist == {'pdf_room_label': 106, 'pdf_material_code': 126,
                        'pdf_dimension': 18, 'pdf_ceiling_zone': 139}
        pages = sorted({int(m.remarks.split('page=')[1].split()[0])
                        for m in rows if m.category == 'pdf_ceiling_zone'})
        assert pages == [12, 13, 14, 15, 46, 47, 48, 49, 50, 51, 52, 53]
