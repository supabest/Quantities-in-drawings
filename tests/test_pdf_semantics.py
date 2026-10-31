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
from shapely.geometry import Point

from app.core.pdf_parser import (
    extract_page_title, extract_room_labels, extract_material_codes,
    extract_dimensions, extract_area_annotations, room_from_title,
    zone_polygons, point_in_zone, zone_of_point, trace_leaders,
)

PDF = os.path.join(BASE, '装修图纸', '227户型dxf',
                   '广州珠实金融城住宅项目--227户型施工图纸.pdf')


def _make_plan_pdf(path):
    """两页：p1 天花布置图（图签+标签+代号+尺寸+闭合分区+面积注记）；
    p2 立面图（只有图名可作房间归属，页上无房间标签）。"""
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    # 天花分区：两个闭合矩形（内空 ≥ min_area）
    page.draw_rect(fitz.Rect(100, 100, 300, 250), color=(0, 0, 0), width=1.5)
    page.draw_rect(fitz.Rect(350, 100, 600, 300), color=(0, 0, 0), width=1.5)
    # 文字：CJK 用内置字体，字号 <14 才会被当作尺寸标注
    # 客厅/茶室：斜杠合并标签 → 面积是多间合计口径，测降级
    page.insert_text((250, 180), '客厅/茶室', fontname='china-s', fontsize=10)
    page.insert_text((250, 192), '65.6 s.m.', fontsize=9)
    # 主人卧室：带定语命名，测词命中而非行首锚定
    page.insert_text((450, 200), '主人卧室', fontname='china-s', fontsize=10)
    page.insert_text((450, 214), '16.3s.m.', fontsize=9)
    # 说明句：含房间词但不是标签，测 ROOM_LABEL_NOISE 排除
    page.insert_text((250, 320), '电梯厅天花与平面仅为示意',
                     fontname='china-s', fontsize=8)
    page.insert_text((500, 260), 'A-01', fontsize=9)
    # 引线归属用：代号落在两个分区之外的空白处，只能沿细线串进分区
    page.insert_text((300, 340), 'B-02', fontsize=9)
    page.draw_line(fitz.Point(312, 337), fitz.Point(420, 260),
                   color=(0, 0, 0), width=0.25)
    page.insert_text((200, 350), '3200', fontsize=9)
    page.insert_text((550, 380), '天花', fontname='china-s', fontsize=9)
    # 图签：「图纸名称」标签 + 正下方图名
    page.insert_text((700, 500), '图纸名称', fontname='china-s', fontsize=8)
    page.insert_text((700, 512), '天花布置图', fontname='china-s', fontsize=8)

    # p2 立面图：图名 10 字 > max_len 不会被当标签，只能靠 room_from_title
    page2 = doc.new_page(width=842, height=595)
    page2.insert_text((300, 200), '8.4 s.m.', fontsize=9)
    page2.insert_text((700, 500), '图纸名称', fontname='china-s', fontsize=8)
    page2.insert_text((700, 512), '主人浴室立面图（一）',
                      fontname='china-s', fontsize=8)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture(scope='module')
def plan_pages():
    tmp = os.path.join(tempfile.mkdtemp(), 'synthetic_plan.pdf')
    _make_plan_pdf(tmp)
    doc = fitz.open(tmp)
    yield doc
    doc.close()


@pytest.fixture(scope='module')
def plan_page(plan_pages):
    return plan_pages[0]


# ── Tier A：语义原语 ────────────────────────────────────────────────────
def test_page_title_reads_below_label(plan_page):
    """图名取「图纸名称」正下方文字，而不是同基线上的冒号或图例。"""
    assert extract_page_title(plan_page) == '天花布置图'


def test_room_labels_and_material_codes(plan_page):
    names = [n for n, x, y in extract_room_labels(plan_page, x_min=200)]
    # 带定语命名靠词命中而非行首锚定；斜杠合并标签仍是标签
    assert '主人卧室' in names and '客厅/茶室' in names
    # 说明句含房间词但不是标签
    assert not any('示意' in n for n in names)
    codes = [c for c, x, y in extract_material_codes(plan_page)]
    assert 'A-01' in codes


def test_area_annotations_and_title_room(plan_pages):
    """s.m. 是 CAD 导出「平方米」写法；立面页页面无房间标签，归属只能靠图名。"""
    p1, p2 = plan_pages[0], plan_pages[1]
    a1 = {round(v, 1) for v, x, y in extract_area_annotations(p1, x_min=200)}
    assert a1 == {65.6, 16.3}, a1
    a2 = {round(v, 1) for v, x, y in extract_area_annotations(p2, x_min=200)}
    assert a2 == {8.4}, a2
    assert extract_page_title(p2) == '主人浴室立面图（一）'
    assert room_from_title(extract_page_title(p2)) == '主人浴室'
    # 非房间图名不得伪造归属
    assert room_from_title('天花布置图') == ''
    assert room_from_title('通用做法说明') == ''


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


def test_zone_of_point_picks_smallest_containing_zone():
    """回字形分区：外环的 wkt 是外轮廓，会把中间独立岛的标注整体吞掉。

    命中多个域时必须按**最小**含点域归属，不能按连通域编号顺序取第一个，
    否则岛内标注全判给外环——这正是早期 124 个代号全落进同一个域的原因。
    """
    tmp = os.path.join(tempfile.mkdtemp(), 'donut.pdf')
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.draw_rect(fitz.Rect(50, 50, 550, 350), color=(0, 0, 0), width=1.5)
    page.draw_rect(fitz.Rect(200, 150, 400, 250), color=(0, 0, 0), width=1.5)
    doc.save(tmp)
    doc.close()

    doc = fitz.open(tmp)
    # 1:100 缩放下这页折合 21×14m，外环净面积已超默认 max_area_m2=100，须放宽
    zones = zone_polygons(doc[0], dpi=100, min_area_m2=0.02, max_area_m2=500)
    doc.close()

    outer_pt, inner_pt = Point(100, 100), Point(300, 200)
    ring = [z for z in zones if z['polygon'].contains(outer_pt)]
    island = [z for z in zones if z['polygon'].contains(inner_pt)]
    assert len(ring) == 1, '外环应只有一个域'
    assert len(island) >= 1, '岛内至少要有一个真内含域'
    # 岛内点同时被外环外轮廓命中时，归属必须给面积更小的岛
    assert zone_of_point(300, 200, zones)[0] == min(island, key=lambda z: z['polygon'].area)['label']
    assert point_in_zone(300, 200, zones) == min(island, key=lambda z: z['polygon'].area)['label']
    # 落在环内、岛外：归属外环，且 distance=0 表示真内含而非吸附
    label, _, dist = zone_of_point(100, 100, zones)
    assert label == ring[0]['label'] and dist == 0.0


def test_trace_leaders_attributes_offzone_code(plan_page):
    """B-02 本体落在两个分区之外，只有沿 0.25pt 细线串到尖端才归得到分区。

    钉住两件事：① 归属对象必须是入账域集合里的 label（早期 leaders 自己
    再栅格化一次回探连通域，编号和入账域对不上）；② 起点在域外时
    mode 得是 leader 而不是被容差吸附成 direct。
    """
    zones = zone_polygons(plan_page, dpi=100, min_area_m2=0.5)
    labels = {z['label'] for z in zones}
    codes = extract_material_codes(plan_page)
    res = trace_leaders(plan_page, candidates=[(x, y) for c, x, y in codes],
                        zones=zones)
    by = {c: r for (c, x, y), r in zip(codes, res)}

    assert by['B-02']['mode'] == 'leader'
    assert by['B-02']['target_label'] in labels
    assert by['B-02']['leader_length'] > 50
    # A-01 本身就在分区内，不该被引线带走
    assert by['A-01']['target_label'] in labels


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

        AREA_CATS = ('pdf_floor_area', 'pdf_wall_area', 'pdf_ceiling_area')
        import json as _json
        areas = [m for m in stored if m.category in AREA_CATS]
        assert len(areas) == 3, [(m.category, m.area) for m in areas]
        # 注记不是工程量：area 不落值，unit 标「注记」，原值留档在 data_json.check
        assert all(m.area is None and m.unit == '注记' for m in areas)
        assert all(_json.loads(m.data_json)['check']['note_m2'] > 0 for m in areas)
        assert all('证据行·不计量' in (m.remarks or '') for m in areas)
        # 每行必须带计算式与可追溯位置（算量核心原则）
        assert all(m.formula_text and 'page=' in (m.remarks or '')
                   for m in areas)
        # 立面页面积行归属取自图名，不是页面标签
        wall = [m for m in areas if m.category == 'pdf_wall_area']
        assert len(wall) == 1 and '主人浴室' in wall[0].label
        # 斜杠合并标签=多间合计口径，必须降级，防止与分间注记重复计数
        merged = [m for m in areas if '客厅/茶室' in m.label]
        assert len(merged) == 1 and merged[0].confidence_level == 'low'
        assert '合计口径' in merged[0].remarks

        # 分区行：候选证据，不落 area、带 WKT、引线归属到入账域
        from shapely import wkt as swkt
        zones = [m for m in stored if m.category == 'pdf_ceiling_zone']
        assert zones and all(m.area is None and m.unit == '候选' for m in zones)
        assert all(swkt.loads(_json.loads(m.data_json)['wkt']).area > 0
                   for m in zones)
        labeled = [m for m in zones if m.project_features]
        assert labeled, 'B-02 应经引线串链归属到某个分区'
        assert all(_json.loads(m.data_json)['area_m2'] > 0 for m in zones)


def test_candidate_zone_area_must_not_book():
    """候选分区合计远大于已入账天棚量时必须报警，够close时不报。

    分区是逐页栅格化的跨页候选，同一块天棚在十余页天花图里各出现一次；
    早期它直接落 area 进第3分部，把 72m² 户型的天棚撑到 600m²。
    """
    import json as _json
    from app.models import Measurement
    from app.api import _check_reasonableness

    def mk(cat, area=None, dj=None):
        return Measurement(category=cat, area=area, data_json=dj,
                           division_id=3, label=cat)

    fired = [w for w in _check_reasonableness([
        mk('pdf_ceiling_zone', None, _json.dumps({'area_m2': 100})),
        mk('pdf_ceiling_area', 20.0),
    ]) if '候选分区' in w]
    assert fired and '禁止计入清单' in fired[0], fired

    quiet = [w for w in _check_reasonableness([
        mk('pdf_ceiling_zone', None, _json.dumps({'area_m2': 30})),
        mk('pdf_ceiling_area', 20.0),
    ]) if '候选分区' in w]
    assert quiet == []

    # 注记不再入账后 booked_area 可为 0：告警仍须出现，但不得写「远大于 0」
    zero = [w for w in _check_reasonableness([
        mk('pdf_ceiling_zone', None, _json.dumps({'area_m2': 10})),
        mk('pdf_ceiling_area', None, _json.dumps({'check': {'note_m2': 20.0}})),
    ]) if '候选分区' in w]
    assert zero and '尚未入账' in zero[0], zero


# ── Tier B：真实图纸收敛基准 ───────────────────────────────────────────
@pytest.mark.skipif(not os.path.exists(PDF), reason='施工图纸 PDF 不在本地')
def test_full_set_dedup_convergence():
    """111 页整套图纸必须收敛到 376 行及既定分类分布。

    房间标签从 106 降到 72：说明句里的房间词被 ROOM_LABEL_NOISE 排除；
    s.m. 面积注记（地面 17 + 立面 4）保留为**证据行**（unit=注记、不计量），
    不再直接补进分部合计——注记口径含墙/轴线注释，入账会污染工程量。
    """
    import config as _cfg
    from app import create_app, db
    from app.processing import _process_pdf_drawings
    from collections import Counter
    from shapely import wkt as swkt

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
        assert len(rows) == 376, len(rows)
        assert dist == {'pdf_room_label': 72, 'pdf_material_code': 126,
                        'pdf_dimension': 18, 'pdf_floor_area': 17,
                        'pdf_wall_area': 4, 'pdf_ceiling_zone': 139}
        pages = sorted({int(m.remarks.split('page=')[1].split(';')[0])
                        for m in rows if m.category == 'pdf_ceiling_zone'})
        assert pages == [12, 13, 14, 15, 46, 47, 48, 49, 50, 51, 52, 53]

        import json as _json
        area_rows = [m for m in rows
                     if m.category in ('pdf_floor_area', 'pdf_wall_area')]
        assert len(area_rows) == 21
        # 注记只作证据：area 不落值、unit=注记，原值留在 data_json.check
        assert all(m.area is None and m.unit == '注记' and m.formula_text
                   for m in area_rows)
        assert all(_json.loads(m.data_json)['check']['note_m2'] > 0
                   for m in area_rows)
        assert all('证据行·不计量' in (m.remarks or '') for m in area_rows)
        # 降级必须精确命中这两条：合并标签合计口径 + 图名与标签冲突
        lows = {m.label for m in area_rows if m.confidence_level == 'low'}
        assert lows == {'PDF面积标注: 客厅/茶室 65.6m²',
                        'PDF面积标注: 浴室一 5.6m²'}, lows

        # 分区行是「候选分区」证据：不得进分部合计，否则 12 页同一块天棚
        # 会被重复累加（72m² 户型撑到 600m²）
        zones = [m for m in rows if m.category == 'pdf_ceiling_zone']
        assert all(m.area is None and m.unit == '候选' for m in zones)
        assert sum(m.area or 0 for m in rows if m.division_id == 3) == 0.0

        payloads = [_json.loads(m.data_json) for m in zones]
        assert all(swkt.loads(d['wkt']).area > 0 for d in payloads)
        assert all(d['crs'] == 'pdf_pt_y_down' for d in payloads)
        assert all(d['area_m2'] for d in payloads)
        # 引线归属确有产出（227 图纸实测 12 块分区拿到材料代号）
        assert sum(1 for d in payloads if d['material_codes']) == 12
