import os
import json
import uuid
import math
from datetime import datetime
from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required, current_user
from app import db
from app.models import (
    Project, Drawing, Measurement, Report, HistoricalProject,
    SelfCheckItem, DIVISIONS, CONFIDENCE_LEVELS, SOURCE_TAGS, HIDDEN_ITEMS
)
from app.processing import process_project

api_bp = Blueprint('api', __name__)


def allowed_file(filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in current_app.config.get('ALLOWED_EXTENSIONS', {'png', 'jpg', 'jpeg', 'bmp', 'tiff', 'dxf', 'pdf'})


# ═══════════════════════════════
# Drawing Upload
# ═══════════════════════════════

@api_bp.route('/upload', methods=['POST'])
@login_required
def upload_drawing():
    project_id = request.form.get('project_id', type=int)
    if not project_id:
        return jsonify({'success': False, 'error': '缺少项目ID'}), 400

    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权操作此项目'}), 403

    if 'files' not in request.files:
        return jsonify({'success': False, 'error': '未选择文件'}), 400

    files = request.files.getlist('files')
    uploaded = []

    for file in files:
        if file.filename == '':
            continue
        if not allowed_file(file.filename):
            continue

        # 中文名保留用于展示；落盘路径只用 uuid，避免 secure_filename 抹掉中文
        display_name = os.path.basename(file.filename)
        ext = display_name.rsplit('.', 1)[-1].lower()
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
        file.save(upload_path)
        size = os.path.getsize(upload_path)

        # Detect drawing type from filename
        drawing_type = 'floor_plan'
        name_lower = display_name.lower()
        if any(k in name_lower for k in ['立面', 'elevation', 'el']):
            drawing_type = 'elevation'
        elif any(k in name_lower for k in ['天花', '吊顶', 'ceiling', 'ceil']):
            drawing_type = 'ceiling'
        elif any(k in name_lower for k in ['大样', '节点', 'detail', 'det']):
            drawing_type = 'detail'

        drawing = Drawing(
            filename=unique_name,
            original_name=display_name,
            file_type=ext,
            file_size=size,
            drawing_type=drawing_type,
            project_id=project.id
        )
        db.session.add(drawing)
        uploaded.append({
            'id': drawing.id,
            'name': display_name,
            'size': size,
            'type': ext,
            'drawing_type': drawing_type,
            'url': f'/static/uploads/{unique_name}'
        })

    if uploaded:
        project.status = 'pending'
        db.session.commit()

    return jsonify({'success': True, 'count': len(uploaded), 'files': uploaded})


# ═══════════════════════════════
# Processing Engine (v3.1)
# ═══════════════════════════════

@api_bp.route('/process/<int:project_id>', methods=['POST'])
@login_required
def start_processing(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权操作此项目'}), 403

    drawings = project.drawings.all()
    if not drawings:
        return jsonify({'success': False, 'error': '没有可处理的图纸'}), 400

    # Check if historical reference was prompted
    if not project.historical_ref_prompted:
        project.historical_ref_prompted = True
        db.session.commit()
        # Return warning but still process
        # We'll include the warning in the response

    project.status = 'processing'
    db.session.commit()

    try:
        results = process_project(project, drawings)
        project.status = 'completed'

        measurements = Measurement.query.filter_by(project_id=project.id).order_by(
            Measurement.division_id, Measurement.id
        ).all()
        summary = _generate_boq_summary(measurements)
        report_html = _render_report_html(project, measurements, summary)

        report_path = os.path.join(current_app.config['REPORTS_FOLDER'],
                                   f'report_{project.id}.html')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_html)

        report = Report(
            title=f'{project.name} - 自动计量报告',
            format='html',
            file_path=report_path,
            summary_json=json.dumps(summary, ensure_ascii=False),
            project_id=project.id
        )
        db.session.add(report)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': '处理完成',
            'measurement_count': len(measurements),
            'summary': summary,
            'report_url': f'/report/{report.id}',
            'warnings': _check_reasonableness(measurements)
        })

    except Exception as e:
        project.status = 'failed'
        db.session.commit()
        return jsonify({'success': False, 'error': f'处理出错: {str(e)}'}), 500


# ═══════════════════════════════
# BOQ Summary & Reasonableness
# ═══════════════════════════════

def _generate_boq_summary(measurements):
    """Generate 8-division summary with confidence distribution."""
    summary = {
        'divisions': {},
        'total_items': len(measurements),
        'confidence_counts': {'high': 0, 'medium': 0, 'low': 0},
        'pending_confirmation': [],
        'warnings': [],
    }

    for div_id, div_name, unit, _ in DIVISIONS:
        div_items = [m for m in measurements if m.division_id == div_id]
        total_area = sum(m.area or 0 for m in div_items)
        total_length = sum(m.length or 0 for m in div_items)
        total_count = sum(m.count or 0 for m in div_items)
        summary['divisions'][str(div_id)] = {
            'name': div_name,
            'item_count': len(div_items),
            'total_area': round(total_area, 2),
            'total_length': round(total_length, 2),
            'total_count': total_count,
            'items': [{'id': m.id, 'label': m.label or ''} for m in div_items]
        }

    for m in measurements:
        level = m.confidence_level or 'medium'
        if level in summary['confidence_counts']:
            summary['confidence_counts'][level] += 1
        if level == 'low':
            summary['pending_confirmation'].append({
                'label': m.label or '',
                'reason': m.remarks or '信息不足，待确认',
                'position': m.position_desc or ''
            })

    return summary


def _check_reasonableness(measurements):
    """Check reasonableness indicators per v3.1 spec."""
    warnings = []

    # Wall/Floor ratio
    floor_area = sum(m.area or 0 for m in measurements if m.division_id == 1)
    wall_area = sum(m.area or 0 for m in measurements if m.division_id == 2)
    if floor_area > 0 and wall_area > 0:
        ratio = wall_area / floor_area
        if ratio < 1.4 or ratio > 4.5:
            warnings.append(f'⚠️ 墙面/地面面积比={ratio:.2f}，参考范围2.0~3.5，偏差较大请核查')

    # Fixture density
    total_area = floor_area
    fixture_count = sum(m.count or 0 for m in measurements if m.division_id == 8)
    if total_area > 0:
        density = fixture_count / total_area
        if density < 0.3 or density > 2.5:
            warnings.append(f'⚠️ 开关插座密度={density:.2f}个/m²，请核实电气点位配置')

    # Baseboard ratio
    for m in measurements:
        if m.division_id == 5 and m.length and m.perimeter:
            ratio = m.length / m.perimeter if m.perimeter > 0 else 0
            if ratio < 0.6 or ratio > 1.1:
                warnings.append(f'⚠️ {m.label}踢脚线长度/周长比={ratio:.2f}，请核实扣减项')

    return warnings


# ═══════════════════════════════
# BOQ Data API
# ═══════════════════════════════

@api_bp.route('/boq/<int:project_id>')
@login_required
def get_boq(project_id):
    """Get BOQ data organized by 8 divisions."""
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权访问'}), 403

    measurements = Measurement.query.filter_by(project_id=project.id).order_by(
        Measurement.division_id, Measurement.id
    ).all()

    boq_data = []
    for div_id, div_name, unit, _ in DIVISIONS:
        div_items = [m for m in measurements if m.division_id == div_id]
        if not div_items:
            continue
        boq_data.append({
            'division_id': div_id,
            'division_name': div_name,
            'items': [m.to_dict() for m in div_items]
        })

    summary = _generate_boq_summary(measurements)

    return jsonify({
        'success': True,
        'project': {'id': project.id, 'name': project.name, 'type': project.project_type},
        'divisions': boq_data,
        'summary': summary,
        'warnings': _check_reasonableness(measurements)
    })


@api_bp.route('/measurements/<int:project_id>')
@login_required
def get_measurements(project_id):
    """Legacy: get all measurements as flat list."""
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权访问'}), 403

    measurements = project.measurements.order_by(Measurement.division_id).all()
    return jsonify({'success': True, 'measurements': [m.to_dict() for m in measurements]})


@api_bp.route('/project/<int:project_id>/scale', methods=['POST'])
@login_required
def set_scale(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权操作'}), 403

    data = request.get_json()
    scale = data.get('scale')
    reference = data.get('reference', '')

    if not scale or scale <= 0:
        return jsonify({'success': False, 'error': '无效的比例尺'}), 400

    project.scale = scale
    project.scale_reference = reference
    db.session.commit()

    return jsonify({'success': True, 'scale': scale})


@api_bp.route('/project/<int:project_id>/rerun', methods=['POST'])
@login_required
def rerun_processing(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权操作'}), 403

    Measurement.query.filter_by(project_id=project.id).delete()
    Report.query.filter_by(project_id=project.id).delete()
    SelfCheckItem.query.filter_by(project_id=project.id).delete()
    db.session.commit()

    return start_processing(project_id)


# ═══════════════════════════════
# Historical Reference
# ═══════════════════════════════

@api_bp.route('/historical/upload', methods=['POST'])
@login_required
def upload_historical():
    """Upload a historical project BOQ for reference."""
    project_id = request.form.get('project_id', type=int)
    if not project_id:
        return jsonify({'success': False, 'error': '缺少项目ID'}), 400

    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权操作'}), 403

    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '未选择文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': '文件名为空'}), 400

    display_name = os.path.basename(file.filename)
    ext = display_name.rsplit('.', 1)[-1].lower()
    unique_name = f"hist_{uuid.uuid4().hex}.{ext}"
    upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
    file.save(upload_path)

    hist = HistoricalProject(
        name=display_name,
        project_type=project.project_type,
        file_path=unique_name,
        user_id=current_user.id,
        project_id=project.id
    )
    db.session.add(hist)
    db.session.commit()

    # Parse Excel and store structured data
    try:
        from app.comparison import parse_historical_excel
        parsed = parse_historical_excel(upload_path)
        hist.item_count = parsed['item_count']
        hist.summary_json = json.dumps(parsed, ensure_ascii=False)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'历史清单解析完成：{parsed["item_count"]} 项，{len(parsed["divisions"])} 个分部',
            'id': hist.id,
            'parsed': {
                'item_count': parsed['item_count'],
                'divisions': list(parsed['divisions'].keys()),
                'project_name': parsed['project_name'],
            }
        })
    except Exception as e:
        # File saved but parse failed; still usable for reference
        return jsonify({
            'success': True,
            'message': f'文件已上传，但解析失败：{str(e)}。可手动参照。',
            'id': hist.id,
            'parse_error': str(e),
        })


@api_bp.route('/historical/list')
@login_required
def list_historical():
    """List historical projects for the current user."""
    projects = HistoricalProject.query.filter_by(user_id=current_user.id).all()
    return jsonify({
        'success': True,
        'projects': [{'id': p.id, 'name': p.name, 'type': p.project_type,
                       'items': p.item_count, 'created': p.created_at.isoformat()}
                      for p in projects]
    })


# ═══════════════════════════════
# Historical Comparison & Auto-fill
# ═══════════════════════════════

@api_bp.route('/historical/compare/<int:project_id>', methods=['POST'])
@login_required
def compare_with_historical(project_id):
    """Compare current BOQ against a historical reference and find gaps."""
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权操作'}), 403

    historical_id = request.get_json().get('historical_id')
    if not historical_id:
        return jsonify({'success': False, 'error': '请指定历史清单ID'}), 400

    from app.comparison import compare_with_historical
    result = compare_with_historical(project_id, historical_id)

    if 'error' in result:
        return jsonify({'success': False, 'error': result['error']}), 400

    return jsonify({
        'success': True,
        'comparison': result,
    })


@api_bp.route('/historical/auto-fill/<int:project_id>', methods=['POST'])
@login_required
def auto_fill_missing(project_id):
    """Auto-fill missing items from historical comparison into project."""
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权操作'}), 403

    data = request.get_json()
    historical_id = data.get('historical_id')
    items_to_fill = data.get('items', [])

    if not historical_id or not items_to_fill:
        return jsonify({'success': False, 'error': '缺少历史清单ID或待填项目'}), 400

    from app.comparison import auto_fill_missing
    filled = auto_fill_missing(project_id, items_to_fill)

    return jsonify({
        'success': True,
        'message': f'已补入 {len(filled)} 项遗漏项目',
        'filled_count': len(filled),
    })


# ═══════════════════════════════
# Self-Check Checklist
# ═══════════════════════════════

SELF_CHECK_ITEMS = [
    ('平面图中所有墙段均已建立墙段清单，无遗漏', 'v3'),
    ('每段墙已核查对应立面图，无立面者已按优先级规则处理', 'v3'),
    ('无立面部位汇总表已单独列出（部位+处理依据+置信度）', 'kb1.0'),
    ('同一墙面多种材质已按分界线分区列项', 'v3'),
    ('门窗洞口面积已从墙面面积中正确扣减（尺寸以门窗表为准）', 'v3'),
    ('地面面积以平面净尺寸为准，各材质分区列项（不使用轴线尺寸）', 'v3'),
    ('天棚工程已核对天花图，不同材质分区列项', 'v3'),
    ('踢脚线长度已扣减门洞宽度和固定柜体段', 'v3.1'),
    ('固定柜台：有大样做法者已列清单；无大样者已标注待确认', 'v3.1'),
    ('甲供材料已在备注列标注，组价时材料费归零', 'v3'),
    ('所有🔴低置信度项目已汇总至清单末尾【待确认事项】', 'v3'),
    ('每项清单均已填写【部位】（图纸位置+图纸编号）', 'v3'),
    ('每项清单均已填写【计算式】，含必要说明，可还原为标注尺寸乘积', 'v3'),
    ('每项清单均已填写【来源标签】（备注列）', 'v3'),
    ('木饰面/不锈钢/铝板墙面：龙骨+夹板基层做法已写入项目特征', 'v3'),
    ('地面找平层已单独列项或在项目特征中注明厚度和材料', 'v3'),
    ('卫生间防水层已列项，注明材料、厚度及上翻高度', 'v3'),
    ('给排水末端器具（龙头/马桶/地漏）已按经验补全列入第7分部', 'v3'),
    ('电气工程（配电箱/开关插座/灯具点位）已列入第8分部', 'v3'),
    ('历史清单对照已完成，补漏项已标注【历史参照】', 'v3'),
    ('合理性指标已计算（墙地面积比/灯具密度等），偏差>30%已输出警告', 'v3'),
]

@api_bp.route('/self-check/<int:project_id>', methods=['GET', 'POST'])
@login_required
def self_check(project_id):
    """Get or update self-check checklist."""
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权访问'}), 403

    if request.method == 'POST':
        data = request.get_json()
        checks = data.get('checks', {})
        for item_id, checked in checks.items():
            item = SelfCheckItem.query.get(int(item_id))
            if item and item.project_id == project.id:
                item.is_checked = checked
        db.session.commit()
        return jsonify({'success': True})

    # GET: return checklist
    existing = SelfCheckItem.query.filter_by(project_id=project.id).all()
    if not existing:
        for text, version in SELF_CHECK_ITEMS:
            db.session.add(SelfCheckItem(
                check_text=text, version_added=version, project_id=project.id
            ))
        db.session.commit()
        existing = SelfCheckItem.query.filter_by(project_id=project.id).all()

    return jsonify({
        'success': True,
        'checks': [{'id': c.id, 'text': c.check_text, 'checked': c.is_checked,
                     'version': c.version_added} for c in existing]
    })


# ═══════════════════════════════
# Project Config
# ═══════════════════════════════

@api_bp.route('/project/<int:project_id>/config', methods=['POST'])
@login_required
def update_project_config(project_id):
    """Update project configuration (type, floor height, etc.)."""
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权操作'}), 403

    data = request.get_json()
    if 'project_type' in data:
        project.project_type = data['project_type']
    if 'default_floor_height' in data:
        project.default_floor_height = data['default_floor_height']
    if 'historical_ref_prompted' in data:
        project.historical_ref_prompted = bool(data['historical_ref_prompted'])
    db.session.commit()

    return jsonify({'success': True})


# ═══════════════════════════════
# Report Generation (internal)
# ═══════════════════════════════

def _render_report_html(project, measurements, summary):
    from flask import render_template
    with current_app.app_context():
        return render_template('report_export.html',
                               project=project,
                               measurements=measurements,
                               summary=summary,
                               generated_at=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
