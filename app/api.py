import os
import json
import uuid
from datetime import datetime
from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app import db
from app.models import Project, Drawing, Measurement, Report
from app.processing import process_drawing

api_bp = Blueprint('api', __name__)


def allowed_file(filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in current_app.config.get('ALLOWED_EXTENSIONS', {'png', 'jpg', 'jpeg', 'bmp', 'tiff'})


@api_bp.route('/upload', methods=['POST'])
@login_required
def upload_drawing():
    """Upload one or more drawing files to a project."""
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

        original_name = secure_filename(file.filename)
        ext = original_name.rsplit('.', 1)[-1].lower()
        unique_name = f"{uuid.uuid4().hex}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.{ext}"
        upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
        file.save(upload_path)

        size = os.path.getsize(upload_path)

        drawing = Drawing(
            filename=unique_name,
            original_name=original_name,
            file_type=ext,
            file_size=size,
            project_id=project.id
        )
        db.session.add(drawing)
        uploaded.append({
            'id': drawing.id,
            'name': original_name,
            'size': size,
            'type': ext,
            'url': f'/static/uploads/{unique_name}'
        })

    if uploaded:
        project.status = 'pending'
        db.session.commit()

    return jsonify({
        'success': True,
        'count': len(uploaded),
        'files': uploaded
    })


@api_bp.route('/process/<int:project_id>', methods=['POST'])
@login_required
def start_processing(project_id):
    """Start processing all unprocessed drawings for a project."""
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权操作此项目'}), 403

    drawings = project.drawings.all()
    if not drawings:
        return jsonify({'success': False, 'error': '没有可处理的图纸'}), 400

    project.status = 'processing'
    db.session.commit()

    try:
        results = process_drawing(project, drawings)
        project.status = 'completed'

        # Generate report
        measurements = project.measurements.all()
        summary = _generate_summary(measurements)
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
            'report_url': f'/report/{report.id}'
        })

    except Exception as e:
        project.status = 'failed'
        db.session.commit()
        return jsonify({
            'success': False,
            'error': f'处理出错: {str(e)}'
        }), 500


@api_bp.route('/measurements/<int:project_id>')
@login_required
def get_measurements(project_id):
    """Get all measurements for a project."""
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权访问'}), 403

    measurements = project.measurements.all()
    data = []
    for m in measurements:
        data.append({
            'id': m.id,
            'label': m.label,
            'category': m.category,
            'area': m.area,
            'perimeter': m.perimeter,
            'length': m.length,
            'width': m.width,
            'height': m.height,
            'count': m.count,
            'unit': m.unit,
            'confidence': m.confidence
        })

    return jsonify({'success': True, 'measurements': data})


@api_bp.route('/project/<int:project_id>/scale', methods=['POST'])
@login_required
def set_scale(project_id):
    """Set the scale calibration for a project."""
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
    """Delete old measurements and reprocess."""
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        return jsonify({'success': False, 'error': '无权操作'}), 403

    # Delete old measurements and report
    Measurement.query.filter_by(project_id=project.id).delete()
    Report.query.filter_by(project_id=project.id).delete()
    db.session.commit()

    return start_processing(project_id)


def _generate_summary(measurements):
    """Generate summary statistics from measurements."""
    summary = {
        'total_area': 0,
        'total_perimeter': 0,
        'room_count': 0,
        'door_count': 0,
        'window_count': 0,
        'fixture_count': 0,
        'rooms': []
    }

    for m in measurements:
        if m.category == 'room':
            summary['room_count'] += 1
            summary['total_area'] += m.area or 0
            summary['total_perimeter'] += m.perimeter or 0
            summary['rooms'].append({
                'label': m.label or f'房间{summary["room_count"]}',
                'area': m.area,
                'perimeter': m.perimeter
            })
        elif m.category == 'door':
            summary['door_count'] += m.count or 1
        elif m.category == 'window':
            summary['window_count'] += m.count or 1
        elif m.category == 'fixture':
            summary['fixture_count'] += m.count or 1

    return summary


def _render_report_html(project, measurements, summary):
    """Render an HTML report as string."""
    from flask import render_template
    from flask import current_app

    with current_app.app_context():
        return render_template('report_export.html',
                               project=project,
                               measurements=measurements,
                               summary=summary,
                               generated_at=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
