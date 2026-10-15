"""
Decoration Drawing Automated Measurement Engine — v3.1
=====================================================
Implements the 8-division BOQ calculation rules from the Project Instructions.

Divisions:
  1. 楼地面工程 — Flooring (m²)
  2. 墙柱面工程 — Walls/Columns (m², deduct openings)
  3. 天棚工程 — Ceilings (m², horizontal projection)
  4. 门窗工程 — Doors/Windows (樘/m²)
  5. 踢脚线工程 — Baseboard (m, deduct doors & cabinets)
  6. 固定柜台工程 — Fixed Cabinets (m²/m/个)
  7. 给排水工程 — Plumbing (套/个)
  8. 电气工程 — Electrical (台/个)

Confidence & Traceability:
  - Every item has: source_tag, confidence_level, position_desc, formula_text
  - Reconciliation principle: all quantities traceable to dimension × dimension - deductions
"""

import cv2
import numpy as np
import os
import json
import math
from datetime import datetime
from flask import current_app
from app import db
from app.models import (
    Drawing, Measurement, PROJECT_TYPES, DIVISIONS,
    BASE_LAYER_RULES, HIDDEN_ITEMS, SOURCE_TAGS,
    CONFIDENCE_LEVELS
)

# ── Constants ──

MIN_ROOM_AREA_PX = 500
MAX_ROOM_AREA_RATIO = 0.95
WALL_MIN_LENGTH = 30
WALL_MAX_GAP = 20
DEFAULT_PIXELS_PER_METER = 100
DEFAULT_DOOR_HEIGHT = 2.1
DEFAULT_WINDOW_HEIGHT = 1.5

# ── Color Scheme (BGR for OpenCV) ──

COLORS = {
    1: (65, 167, 79),     # Flooring - green
    2: (66, 133, 244),    # Walls - blue
    3: (147, 112, 219),   # Ceiling - purple
    4: (234, 134, 47),    # Doors/Windows - orange
    5: (0, 191, 165),     # Baseboard - teal
    6: (219, 68, 55),     # Cabinets - red
    7: (255, 193, 7),     # Plumbing - gold
    8: (0, 150, 136),     # Electrical - dark teal
}


def process_project(project, drawings):
    """Main entry point: process all drawings for a project.

    Generates measurements organized by the 8-division system.
    """
    scale = project.scale or DEFAULT_PIXELS_PER_METER
    floor_height = project.default_floor_height or 2.80

    # Classify drawings by type. 光栅阶段只吃图片；PDF/DXF 走各自的矢量管线
    raster_exts = ('.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif')
    drawing_map = {dt: [] for dt in ['floor_plan', 'elevation', 'ceiling', 'detail']}
    raster_drawings = [d for d in drawings if d.filename.lower().endswith(raster_exts)]
    for d in raster_drawings:
        # First check if d has drawing_type attribute
        dtype = getattr(d, 'drawing_type', 'floor_plan') or 'floor_plan'
        if dtype in drawing_map:
            drawing_map[dtype].append(d)
        else:
            drawing_map['floor_plan'].append(d)

    all_measurements = []

    # Phase 1: Process floor plans → rooms, doors, base rooms
    for drawing in drawing_map['floor_plan']:
        image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], drawing.filename)
        if not os.path.exists(image_path):
            continue
        ms = _process_floor_plan(image_path, drawing, project, scale)
        all_measurements.extend(ms)

    # Phase 1.5: PDF semantic extraction (Phase 5c)
    pdf_drawings = [d for d in drawings if d.filename.lower().endswith('.pdf')]
    if pdf_drawings:
        pdf_semantics = _process_pdf_drawings(pdf_drawings, project)
        all_measurements.extend(pdf_semantics)

    # Phase 2: Process elevations → wall heights, material info
    for drawing in drawing_map['elevation']:
        image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], drawing.filename)
        if not os.path.exists(image_path):
            continue
        ms = _process_elevation(image_path, drawing, project, scale)
        all_measurements.extend(ms)

    # Phase 3: Generate derived measurements (walls, baseboard, etc.)
    derived = _generate_derived_measurements(project, floor_height, scale)
    all_measurements.extend(derived)

    # Phase 4: Generate hidden items (MEP, etc.)
    hidden = _generate_hidden_items(project)
    all_measurements.extend(hidden)

    # Generate annotated images
    for drawing in drawing_map['floor_plan']:
        image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], drawing.filename)
        if os.path.exists(image_path):
            _generate_annotated_image(image_path, all_measurements, drawing, project, scale)

    db.session.commit()
    return {'measurement_count': len(all_measurements)}


# ════════════════════════════════════════════════════
# Phase 1: Floor Plan Processing (分部分项 1-4)
# ════════════════════════════════════════════════════

def _process_floor_plan(image_path, drawing, project, scale):
    """Process a floor plan image. Detects rooms, doors, windows.

    Division 1 (Flooring): Room areas
    Division 4 (Doors/Windows): Door/window counts and dimensions
    """
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"无法读取图片: {image_path}")

    drawing.height, drawing.width = img.shape[:2]
    measurements = []

    # ── 1. Room detection (Division 1: Flooring) ──
    rooms = _detect_rooms(img)
    for i, room in enumerate(rooms):
        label = _classify_room_type(room)
        area_m2 = round(room['area_m2'], 2)
        perimeter_m = round(room['perimeter_m'], 2)
        length_m = round(room.get('length_m', 0), 2)
        width_m = round(room.get('width_m', 0), 2)

        # Division 1: Floor area
        formula = f"{length_m}×{width_m} = {area_m2}m²"
        if room.get('is_complex', False):
            formula = f"净尺寸计算 = {area_m2}m²（不规则形状）"

        m = Measurement(
            label=label,
            division_id=1,
            category='room',
            area=area_m2,
            perimeter=perimeter_m,
            length=length_m,
            width=width_m,
            unit='m²',
            source_tag='图纸实测',
            source_tag_icon='📐',
            confidence_level='high',
            confidence_icon='🟢',
            confidence=0.85,
            position_desc=f"{label}-地面，平面图 {drawing.original_name}",
            formula_text=f"地面面积 = {formula}",
            project_features=f"{label}地面（净面积）",
            # bbox(px) 供分区阶段做跨区相邻(公共墙)判定
            data_json=json.dumps({'bbox': [int(v) for v in room['bbox']]}),
            project_id=project.id,
            drawing_id=drawing.id
        )
        db.session.add(m)
        measurements.append(m)

        # Division 4: Door/window detection per room
        doors_in_room = _detect_doors_and_windows(img, scale, room)
        for j, door in enumerate(doors_in_room):
            door_label = f"{label}-{'门' if door['type'] == 'door' else '窗'}"
            width_m = round(door.get('width_m', 0.9), 2)
            height_m = round(door.get('height_m', 2.1), 2)
            door_area = round(width_m * height_m, 2)
            door_count = 1

            m = Measurement(
                label=door_label,
                division_id=4,
                category=door['type'],
                length=width_m,
                height=height_m,
                area=door_area,
                count=door_count,
                unit='樘',
                source_tag='图纸实测',
                source_tag_icon='📐',
                confidence_level='high',
                confidence_icon='🟢',
                confidence=0.8,
                position_desc=f"{label}-{door.get('orientation', '')}，平面图 {drawing.original_name}",
                formula_text=f"{door_label} = {width_m}×{height_m} = {door_area}m²",
                project_features=f"{door_label}（{width_m}×{height_m}）",
                # Phase 4.5 拓扑预校验：写入中心 px，无中心则留空（旧数据可优雅跳过）
                data_json=json.dumps({'center_px': door['center_px']}) if door.get('center_px') else None,
                project_id=project.id,
                drawing_id=drawing.id
            )
            db.session.add(m)
            measurements.append(m)

    return measurements


# ════════════════════════════════════════════════════
# Phase 2: Elevation Processing (墙面材质、高度)
# ════════════════════════════════════════════════════

def _process_elevation(image_path, drawing, project, scale):
    """Process elevation drawings to extract wall materials and heights."""
    img = cv2.imread(image_path)
    if img is None:
        return []

    measurements = []

    # Detect wall surfaces in elevation
    walls = _detect_wall_surfaces(img, scale)
    for wall in walls:
        area_m2 = round(wall.get('area_m2', 0), 2)
        width_m = round(wall.get('width_m', 0), 2)
        height_m = round(wall.get('height_m', 2.8), 2)

        if area_m2 <= 0:
            continue

        base_layer = BASE_LAYER_RULES.get(wall.get('material', ''), '')

        m = Measurement(
            label=wall.get('label', '墙面'),
            division_id=2,
            category='wall',
            area=area_m2,
            length=width_m,
            height=height_m,
            unit='m²',
            source_tag='图纸实测',
            source_tag_icon='📐',
            confidence_level='high',
            confidence_icon='🟢',
            confidence=0.75,
            position_desc=f"{wall.get('room', '')}-{wall.get('orientation', '')}墙，立面图 {drawing.original_name}",
            formula_text=f"墙面面积 = {width_m}×{height_m} = {area_m2}m²",
            project_features=f"{wall.get('material', '')}饰面；{base_layer}" if base_layer else f"{wall.get('material', '')}饰面",
            material_code=wall.get('material_code', ''),
            base_layer=base_layer,
            project_id=project.id,
            drawing_id=drawing.id
        )
        db.session.add(m)
        measurements.append(m)

    return measurements


# ════════════════════════════════════════════════════
# Phase 3: Derived Measurements (推算项目)
# ════════════════════════════════════════════════════

def _generate_derived_measurements(project, floor_height, scale, rooms=None):
    """Generate derived measurements based on floor plan analysis.

    - Division 2 (Walls): Wall area = perimeter × height - openings
    - Division 3 (Ceilings): Same as floor area (horizontal projection)
    - Division 5 (Baseboard): perimeter - door widths - cabinet widths
    - Division 6 (Fixed Cabinets): by room type

    Args:
        rooms: 指定参与计算的房间列表（Phase 2 区域级并行用）；
               None 时取项目全部分部1房间（Phase 1 行为不变）。
    """
    # Get existing room and door measurements
    if rooms is None:
        rooms = Measurement.query.filter_by(
            project_id=project.id, division_id=1
        ).all()

    doors = Measurement.query.filter_by(
        project_id=project.id, division_id=4
    ).all()

    derived = []

    for room in rooms:
        label = room.label or '房间'
        perimeter_m = room.perimeter or 0
        area_m2 = room.area or 0

        # Calculate total door width in this room
        door_width_sum = 0
        room_doors = [d for d in doors if d.label and label in d.label]
        for d in room_doors:
            door_width_sum += d.length or 0

        # ── Division 2: Wall Area (墙面面积 = 周长×层高 - 门窗洞口) ──
        openings_area = sum((d.length or 0) * (d.height or DEFAULT_DOOR_HEIGHT) for d in room_doors)
        wall_area = round(perimeter_m * floor_height - openings_area, 2)

        if wall_area > 0:
            m = Measurement(
                label=f"{label}-墙面",
                division_id=2,
                category='wall',
                area=wall_area,
                perimeter=perimeter_m,
                length=perimeter_m,
                height=floor_height,
                unit='m²',
                source_tag='几何推算',
                source_tag_icon='🔢',
                confidence_level='medium',
                confidence_icon='🟡',
                confidence=0.7,
                position_desc=f"{label}-四周墙面",
                formula_text=f"墙面面积 = {label}周长{perimeter_m}×层高{floor_height} - 门窗洞口{openings_area} = {wall_area}m²",
                project_features=f"墙面（含基层处理+面层）",
                project_id=project.id
            )
            db.session.add(m)
            derived.append(m)

        # ── Division 3: Ceiling Area (天棚面积 = 水平投影面积 = 地面面积) ──
        m = Measurement(
            label=f"{label}-天棚",
            division_id=3,
            category='ceiling',
            area=area_m2,
            perimeter=perimeter_m,
            unit='m²',
            source_tag='几何推算',
            source_tag_icon='🔢',
            confidence_level='high',
            confidence_icon='🟢',
            confidence=0.85,
            position_desc=f"{label}-天花",
            formula_text=f"天棚面积 = 地面净面积 = {area_m2}m²（水平投影）",
            project_features="轻钢龙骨石膏板吊顶" if area_m2 > 20 else "平顶乳胶漆",
            project_id=project.id
        )
        db.session.add(m)
        derived.append(m)

        # ── Division 5: Baseboard (踢脚线 = 净周长 - 门洞 - 固定柜) ──
        cabinet_deduction = 1.5 if any(kw in label for kw in ['主卧', '次卧', '卧室']) else 0
        baseboard_length = round(perimeter_m - door_width_sum - cabinet_deduction, 2)

        if baseboard_length > 0:
            deduction_note = f"（扣门洞{door_width_sum}m"
            if cabinet_deduction > 0:
                deduction_note += f"+固定柜{cabinet_deduction}m"
            deduction_note += "）"

            m = Measurement(
                label=f"{label}-踢脚线",
                division_id=5,
                category='baseboard',
                length=baseboard_length,
                count=1,
                unit='m',
                source_tag='几何推算',
                source_tag_icon='🔢',
                confidence_level='medium',
                confidence_icon='🟡',
                confidence=0.7,
                position_desc=f"{label}-四周地面",
                formula_text=f"踢脚线长度 = 净周长{perimeter_m} - 门洞宽{door_width_sum} - 柜体{cabinet_deduction} = {baseboard_length}m",
                project_features=f"踢脚线{deduction_note}",
                project_id=project.id
            )
            db.session.add(m)
            derived.append(m)

    return derived


# ════════════════════════════════════════════════════
# Phase 1.5: PDF Semantic Extraction (Phase 5c)
# ════════════════════════════════════════════════════

def _process_pdf_drawings(pdf_drawings, project):
    """从上传的 PDF 图纸提取语义：房间名、材料代号、尺寸标注、天花分区。

    去重收敛：同名标签/同代号全局合并（保留首次定位+出现页数），
    避免整套施工图产生上千条噪声行污染清单。
    天花分区仅在识别为天花图的页面输出。
    """
    import fitz
    from collections import defaultdict
    from app.core.pdf_parser import (
        PDFPage, extract_room_labels, extract_material_codes,
        extract_dimensions, flood_zones, extract_page_title,
    )

    MAX_ZONES_PER_PAGE = 15

    upload_folder = current_app.config['UPLOAD_FOLDER']

    rooms = {}       # name -> (x, y, first_page, pages set)
    codes = {}       # code -> (x, y, first_page, pages set)
    dims = {}        # (value, unit) -> (x, y, first_page, pages set)
    zones = []       # (page_idx, zone dict)

    for drawing in pdf_drawings:
        pdf_path = os.path.join(upload_folder, drawing.filename)
        if not os.path.exists(pdf_path):
            continue
        try:
            doc = fitz.open(pdf_path)
        except Exception:
            continue

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            title = extract_page_title(page)

            for name, x, y in extract_room_labels(page, x_min=200):
                if name not in rooms:
                    rooms[name] = (x, y, page_idx, set())
                rooms[name][3].add(page_idx)

            for code, x, y in extract_material_codes(page, x_min=200):
                if code not in codes:
                    codes[code] = (x, y, page_idx, set())
                codes[code][3].add(page_idx)

            for val, x, y, unit in extract_dimensions(page, x_min=200):
                key = (val, unit)
                if key not in dims:
                    dims[key] = (x, y, page_idx, set())
                dims[key][3].add(page_idx)

            # 天花分区仅对图名含天花的页面提取（逐页栅格化开销大）
            if '天花' in title or '吊顶' in title:
                for z in flood_zones(page, dpi=100, min_area_m2=0.5)[:MAX_ZONES_PER_PAGE]:
                    zones.append((page_idx, z, title))

        doc.close()

    measurements = []

    def emit(**kwargs):
        # 图标是实体列且有默认值（📐/🟢=图纸实测+高置信），不显式赋值会把
        # PDF 行标成别的溯源、并把 medium 显示成绿色高置信
        tag = kwargs['source_tag']
        lvl = kwargs['confidence_level']
        m = Measurement(
            project_id=project.id,
            source_tag_icon=dict((t[0], t[1]) for t in SOURCE_TAGS).get(tag, ''),
            confidence_icon=CONFIDENCE_LEVELS[lvl]['icon'],
            **kwargs
        )
        db.session.add(m)
        measurements.append(m)

    for name, (x, y, pidx, pages) in rooms.items():
        emit(
            label=f"PDF房间标签: {name}",
            division_id=1,
            category='pdf_room_label',
            source_tag='PDF语义提取',
            confidence_level='medium',
            position_desc=f"PDF p{pidx} ({x:.0f},{y:.0f})",
            formula_text=f"矢量文字提取: {name}（出现 {len(pages)} 页）",
            remarks=f"pages={sorted(pages)}",
        )

    for code, (x, y, pidx, pages) in codes.items():
        emit(
            label=f"PDF材料: {code}",
            division_id=2,
            category='pdf_material_code',
            source_tag='PDF语义提取',
            confidence_level='medium',
            position_desc=f"PDF p{pidx} ({x:.0f},{y:.0f})",
            formula_text=f"矢量文字提取: {code}（出现 {len(pages)} 页）",
            remarks=f"pages={sorted(pages)}",
        )

    for (val, unit), (x, y, pidx, pages) in dims.items():
        emit(
            label=f"PDF尺寸: {val}{unit}",
            division_id=1,
            category='pdf_dimension',
            source_tag='PDF语义提取',
            confidence_level='high' if unit == 'mm' else 'medium',
            position_desc=f"PDF p{pidx} ({x:.0f},{y:.0f})",
            formula_text=f"矢量尺寸提取: {val}{unit}（出现 {len(pages)} 页）",
            remarks=f"pages={sorted(pages)}",
        )

    for pidx, z, title in zones:
        emit(
            label=f"PDF天花域 p{pidx}#{z['label']}",
            division_id=3,
            category='pdf_ceiling_zone',
            area=z['area_m2'],
            source_tag='PDF语义提取',
            confidence_level='medium',
            position_desc=f"PDF p{pidx}({title}) centroid={z['centroid_pt']}",
            formula_text=f"洪水填充域面积: {z['area_m2']}m²",
            remarks=f"page={pidx} label={z['label']} title={title}",
        )

    return measurements


# ════════════════════════════════════════════════════
# Phase 4: Hidden Items (隐含项目 · 经验补全)
# ════════════════════════════════════════════════════

def _generate_hidden_items(project, rooms=None):
    """Generate hidden/common items that are not shown in drawings
    but must be included based on project experience.

    Divisions 7 (Plumbing) and 8 (Electrical).
    Also includes base layer items for Division 2.

    Args:
        rooms: 指定房间列表（Phase 2 区域级并行用）；None 时取项目全部（Phase 1 行为不变）。
    """
    items = []
    existing_labels = {m.label for m in Measurement.query.filter_by(project_id=project.id).all()}

    # Get room labels for context
    if rooms is None:
        rooms = Measurement.query.filter_by(project_id=project.id, division_id=1).all()
    room_labels = [r.label or '' for r in rooms]
    has_bathroom = any('卫生间' in r for r in room_labels)
    has_kitchen = any('厨房' in r for r in room_labels)
    has_laundry = any('洗衣' in r for r in room_labels)
    has_living = any('客厅' in r for r in room_labels)
    has_bedroom = any(('卧' in r or '卧室' in r) for r in room_labels)

    # ── Division 7: Plumbing (给排水) ──
    if has_bathroom:
        plumbing_items = [
            ('面盆龙头', '套', 1),
            ('淋浴龙头/花洒', '套', 1),
            ('马桶（坐便器）', '个', 1),
            ('地漏', '个', 2),
        ]
        for name, unit, count in plumbing_items:
            item_label = f"卫生间-{name}"
            if item_label not in existing_labels:
                m = Measurement(
                    label=item_label,
                    division_id=7,
                    category='fixture',
                    count=count,
                    unit=unit,
                    source_tag='经验补全',
                    source_tag_icon='🧠',
                    confidence_level='medium',
                    confidence_icon='🟡',
                    confidence=0.6,
                    position_desc=f"卫生间",
                    formula_text=f"{name} = {count}{unit}（按卫生间功能配置经验补全）",
                    project_features=f"卫生间{name}（含角阀+软管）",
                    remarks=f"【经验补全】图纸未显示，按标准配置补入",
                    project_id=project.id
                )
                db.session.add(m)
                items.append(m)

    if has_kitchen:
        m = Measurement(
            label='厨房-水槽龙头',
            division_id=7,
            category='fixture',
            count=1,
            unit='套',
            source_tag='经验补全',
            source_tag_icon='🧠',
            confidence_level='medium',
            confidence_icon='🟡',
            confidence=0.6,
            position_desc='厨房',
            formula_text='厨房水槽龙头 = 1套',
            project_features='厨房水槽龙头（含角阀+软管）',
            remarks='【经验补全】图纸未显示',
            project_id=project.id
        )
        db.session.add(m)
        items.append(m)

    # ── Division 8: Electrical (电气) ──
    electrical_items = []
    room_count = max(len(room_labels), 1)

    if has_living:
        electrical_items.append(('客厅-配电箱', '台', 1))
        electrical_items.append(('客厅-空调插座', '个', 1))
        electrical_items.append(('客厅-开关插座', '个', 6))
        electrical_items.append(('客厅-弱电点位', '个', 2))
        electrical_items.append(('客厅-灯具预留', '个', 2))

    for r in room_labels:
        if '卧' in r:
            electrical_items.append((f'{r}-开关插座', '个', 4))
            electrical_items.append((f'{r}-灯具预留', '个', 1))
            electrical_items.append((f'{r}-空调插座', '个', 1))
        if '厨房' in r:
            electrical_items.append((f'{r}-开关插座', '个', 5))
            electrical_items.append((f'{r}-灯具预留', '个', 1))
        if '卫生间' in r:
            electrical_items.append((f'{r}-开关插座', '个', 2))
            electrical_items.append((f'{r}-灯具预留', '个', 1))
            electrical_items.append((f'{r}-浴霸/暖风机预留', '个', 1))

    for name, unit, count in electrical_items:
        if name not in existing_labels:
            m = Measurement(
                label=name,
                division_id=8,
                category='fixture',
                count=count,
                unit=unit,
                source_tag='经验补全',
                source_tag_icon='🧠',
                confidence_level='medium',
                confidence_icon='🟡',
                confidence=0.5,
                position_desc=name.split('-')[0] if '-' in name else '通用',
                formula_text=f"{name} = {count}{unit}（按房间功能配置经验补全）",
                project_features=f"{name}",
                remarks='【经验补全】电气点位图纸未完全显示，按标准配置估算',
                project_id=project.id
            )
            db.session.add(m)
            items.append(m)

    # ── Base layer items (基层做法 - Division 2 supplement) ──
    if has_bathroom:
        # Find wall area for bathroom to calculate waterproof area
        bath_walls = Measurement.query.filter_by(
            project_id=project.id, division_id=2
        ).filter(Measurement.label.like('%卫生间%')).all()
        bath_rooms = [r for r in rooms if '卫生间' in (r.label or '')]
        for br in bath_rooms:
            waterproof_area = round((br.area or 0) + (br.perimeter or 0) * 0.3, 2)
            m = Measurement(
                label=f'{br.label}-防水层',
                division_id=2,
                category='fixture',
                area=waterproof_area,
                unit='m²',
                source_tag='逻辑推理',
                source_tag_icon='📖',
                confidence_level='medium',
                confidence_icon='🟡',
                confidence=0.65,
                position_desc=f'{br.label}-地面及墙面上翻',
                formula_text=f'防水面积 = 地面面积{br.area} + 周长{br.perimeter}×上翻0.30m = {waterproof_area}m²',
                project_features='聚氨酯防水涂料1.5mm厚，上翻高度≥300mm',
                remarks='【基层做法待大样图确认】上翻高度按标准做法300mm，以大样图为准',
                project_id=project.id
            )
            db.session.add(m)
            items.append(m)

    return items


# ════════════════════════════════════════════════════
# Image Analysis Functions
# ════════════════════════════════════════════════════

def _detect_rooms(img):
    """Detect rooms in a floor plan image using contour analysis.

    Returns list of room dicts with area, perimeter, bounding box.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoised = cv2.medianBlur(gray, 5)
    _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    kernel = np.ones((5, 5), np.uint8)
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(closed, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    img_h, img_w = img.shape[:2]
    img_area = img_h * img_w
    rooms = []

    for contour in contours:
        area_px = cv2.contourArea(contour)
        if area_px < MIN_ROOM_AREA_PX:
            continue
        if area_px > img_area * MAX_ROOM_AREA_RATIO:
            continue

        epsilon = 0.02 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)

        perimeter_px = cv2.arcLength(approx, True)
        x, y, w, h = cv2.boundingRect(approx)
        circularity = (4 * math.pi * area_px) / (perimeter_px * perimeter_px) if perimeter_px > 0 else 0

        rooms.append({
            'contour': approx,
            'area_m2': area_px / (100 * 100),
            'perimeter_m': perimeter_px / 100,
            'length_m': max(w, h) / 100,
            'width_m': min(w, h) / 100,
            'circularity': circularity,
            'bbox': (x, y, w, h),
            'is_complex': len(approx) > 6,
        })

    rooms.sort(key=lambda r: r['area_m2'], reverse=True)
    return rooms


def _classify_room_type(room):
    """Classify room type based on area and proportions."""
    area = room['area_m2']
    wl_ratio = room['length_m'] / max(room['width_m'], 0.01)

    if area < 1.5:
        return '过道/储藏间'
    if area < 3:
        return '小卫生间' if area < 2.5 else '卫生间'

    if area < 6 and wl_ratio < 1.8:
        return '卫生间'
    if area < 6:
        return '厨房'

    if 6 < area < 12 and wl_ratio > 1.6:
        return '厨房'

    if 8 < area < 25:
        return '主卧' if area > 15 else '次卧'

    if area >= 25:
        return '客厅' if wl_ratio < 1.8 else '客餐厅'

    if area >= 12:
        return '餐厅' if wl_ratio > 1.5 else '客厅'

    return f'房间{room.get("id", "")}'


def _detect_doors_and_windows(img, scale, room=None):
    """Detect doors and windows in floor plan near a given room.

    Uses Hough line detection to find door-sized segments.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=50,
                            minLineLength=30, maxLineGap=15)

    doors = []
    if lines is None:
        return doors

    rx, ry, rw, rh = room['bbox'] if room else (0, 0, img.shape[1], img.shape[0])

    for line in lines:
        x1, y1, x2, y2 = np.ravel(line)[:4]   # OpenCV 5.x HoughLinesP 返回 (N,4)，旧版 (N,1,4)——ravel 兼容
        length = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
        length_m = length / scale

        # Check if line is within or near room boundary
        margin = 20
        near_room = (rx - margin <= (x1 + x2) / 2 <= rx + rw + margin and
                     ry - margin <= (y1 + y2) / 2 <= ry + rh + margin)

        if not near_room:
            continue

        if 0.6 < length_m < 1.5:
            orientation = '东' if x2 > x1 + abs(y2 - y1) else ('西' if x1 > x2 + abs(y2 - y1)
                                                               else ('南' if y2 > y1 else '北'))
            doors.append({
                'type': 'door',
                'width_m': length_m,
                'height_m': DEFAULT_DOOR_HEIGHT,
                'confidence': 0.5,
                'orientation': orientation,
                # Phase 4.5 拓扑预校验：门窗中心（px），后续可升级为沿墙参数 t
                'center_px': [int(round((x1 + x2) / 2)), int(round((y1 + y2) / 2))],
            })

    return doors


def _detect_wall_surfaces(img, scale):
    """Detect wall surfaces in elevation drawings."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120, apertureSize=3)

    kernel = np.ones((2, 2), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=1)

    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80,
                            minLineLength=WALL_MIN_LENGTH, maxLineGap=WALL_MAX_GAP)

    surfaces = []
    if lines is None:
        return surfaces

    # Group lines into wall segments
    segments = []
    for line in lines:
        x1, y1, x2, y2 = np.ravel(line)[:4]   # 兼容 OpenCV 5.x (N,4) 与旧版 (N,1,4)
        length = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
        length_m = length / scale

        if length_m > 0.5:
            segments.append({
                'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                'length_m': length_m,
            })

    # Rough estimation: use image bounds
    h, w = img.shape[:2]
    img_width_m = w / scale
    img_height_m = h / scale

    surfaces.append({
        'label': '墙面',
        'area_m2': img_width_m * img_height_m,
        'width_m': img_width_m,
        'height_m': img_height_m,
        'material': '乳胶漆',
        'material_code': '',
        'room': '',
        'orientation': '',
        'confidence': 0.6,
    })

    return surfaces


# ════════════════════════════════════════════════════
# Annotation Generation
# ════════════════════════════════════════════════════

def _generate_annotated_image(image_path, measurements, drawing, project, scale):
    """Generate annotated image with division-colored overlays."""
    img = cv2.imread(image_path)
    if img is None:
        return None

    overlay = img.copy()

    # Draw room contours in Division 1 color
    rooms = _detect_rooms(img)
    for room in rooms:
        color = COLORS[1]
        cv2.drawContours(overlay, [room['contour']], -1, color, 2)
        overlay_tint = (color[0] // 2, color[1] // 2, color[2] // 2)
        cv2.drawContours(overlay, [room['contour']], -1, overlay_tint, -1)

        x, y, w, h = room['bbox']
        label = f"{room['area_m2']:.1f}m²"
        cv2.putText(overlay, label, (x + 5, y + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        cv2.putText(overlay, label, (x + 5, y + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

    # Blend with original
    result = cv2.addWeighted(overlay, 0.6, img, 0.4, 0)

    annotated_filename = f"annotated_{drawing.filename}"
    annotated_path = os.path.join(current_app.config['UPLOAD_FOLDER'], annotated_filename)
    cv2.imwrite(annotated_path, result)

    drawing.annotated_path = annotated_filename
    db.session.commit()

    return annotated_path
