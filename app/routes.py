from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, current_app
from flask_login import login_required, current_user
from app import db
from app.models import (
    Project, Drawing, Measurement, Report, SelfCheckItem,
    DIVISIONS, CONFIDENCE_LEVELS, PROJECT_TYPES
)
from datetime import datetime
import os
import json

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('index.html')


@main_bp.route('/dashboard')
@login_required
def dashboard():
    page = request.args.get('page', 1, type=int)
    projects = Project.query.filter_by(user_id=current_user.id) \
        .order_by(Project.updated_at.desc()) \
        .paginate(page=page, per_page=current_app.config.get('ITEMS_PER_PAGE', 12), error_out=False)
    return render_template('dashboard.html', projects=projects)


@main_bp.route('/project/new', methods=['GET', 'POST'])
@login_required
def new_project():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        address = request.form.get('address', '').strip()
        project_type = request.form.get('project_type', 'residential')

        if not name:
            flash('请输入项目名称。', 'danger')
            return render_template('project_new.html')

        # Get default floor height for project type
        floor_height = 2.80
        for pt_id, pt_name, fh in PROJECT_TYPES:
            if pt_id == project_type:
                floor_height = fh
                break

        project = Project(
            name=name,
            description=description,
            address=address,
            project_type=project_type,
            default_floor_height=floor_height,
            user_id=current_user.id
        )
        db.session.add(project)
        db.session.commit()

        flash('项目创建成功！请上传图纸开始计量。', 'success')
        return redirect(url_for('main.project_detail', project_id=project.id))

    return render_template('project_new.html')


@main_bp.route('/project/<int:project_id>')
@login_required
def project_detail(project_id):
    project = Project.query.get_or_404(project_id)

    if project.user_id != current_user.id and not current_user.is_admin:
        flash('无权访问此项目。', 'danger')
        return redirect(url_for('main.dashboard'))

    drawings = project.drawings.order_by(Drawing.uploaded_at.desc()).all()
    measurements = project.measurements.order_by(Measurement.division_id).all()
    report = project.report

    # Organize by 8 divisions
    divisions_data = []
    for div_id, div_name, unit, _ in DIVISIONS:
        div_items = [m for m in measurements if m.division_id == div_id]
        if div_items:
            total_area = sum(m.area or 0 for m in div_items)
            total_length = sum(m.length or 0 for m in div_items)
            total_count = sum(m.count or 0 for m in div_items)
            divisions_data.append({
                'id': div_id,
                'name': div_name,
                'unit': unit,
                'item_list': div_items,
                'total_area': round(total_area, 2),
                'total_length': round(total_length, 2),
                'total_count': total_count,
            })

    # Confidence summary
    conf_counts = {'high': 0, 'medium': 0, 'low': 0}
    for m in measurements:
        level = m.confidence_level or 'medium'
        if level in conf_counts:
            conf_counts[level] += 1

    low_conf_items = [m for m in measurements if m.confidence_level == 'low']

    return render_template('project_detail.html',
                           project=project, drawings=drawings,
                           divisions_data=divisions_data,
                           measurements=measurements,
                           conf_counts=conf_counts,
                           low_conf_items=low_conf_items,
                           report=report,
                           DIVISIONS=DIVISIONS,
                           CONFIDENCE_LEVELS=CONFIDENCE_LEVELS,
                           PROJECT_TYPES=PROJECT_TYPES)


@main_bp.route('/project/<int:project_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_project(project_id):
    project = Project.query.get_or_404(project_id)

    if project.user_id != current_user.id and not current_user.is_admin:
        flash('无权修改此项目。', 'danger')
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        project.name = request.form.get('name', project.name).strip()
        project.description = request.form.get('description', '').strip()
        project.address = request.form.get('address', '').strip()
        project.project_type = request.form.get('project_type', project.project_type)
        fh = request.form.get('default_floor_height', type=float)
        if fh and fh > 0:
            project.default_floor_height = fh
        db.session.commit()
        flash('项目信息已更新。', 'success')
        return redirect(url_for('main.project_detail', project_id=project.id))

    return render_template('project_edit.html', project=project)


@main_bp.route('/project/<int:project_id>/delete', methods=['POST'])
@login_required
def delete_project(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        flash('无权删除此项目。', 'danger')
        return redirect(url_for('main.dashboard'))
    db.session.delete(project)
    db.session.commit()
    flash('项目已删除。', 'success')
    return redirect(url_for('main.dashboard'))


@main_bp.route('/project/<int:project_id>/boq')
@login_required
def view_boq(project_id):
    """View the full BOQ (Bill of Quantities) in 8-division format."""
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        flash('无权访问此项目。', 'danger')
        return redirect(url_for('main.dashboard'))

    measurements = Measurement.query.filter_by(project_id=project.id).order_by(
        Measurement.division_id, Measurement.id
    ).all()

    # Build 8-division structure
    divisions = []
    for div_id, div_name, unit, _ in DIVISIONS:
        div_items = [m for m in measurements if m.division_id == div_id]
        if not div_items:
            divisions.append({'id': div_id, 'name': div_name, 'unit': unit,
                              'item_list': [], 'total_area': 0, 'total_length': 0, 'total_count': 0})
            continue
        total_area = sum(m.area or 0 for m in div_items)
        total_length = sum(m.length or 0 for m in div_items)
        total_count = sum(m.count or 0 for m in div_items)
        divisions.append({
            'id': div_id, 'name': div_name, 'unit': unit,
            'item_list': div_items,
            'total_area': round(total_area, 2),
            'total_length': round(total_length, 2),
            'total_count': total_count,
        })

    # Confidence distribution
    conf_dist = {'high': [], 'medium': [], 'low': []}
    for m in measurements:
        level = m.confidence_level or 'medium'
        conf_dist[level].append(m)

    # Low confidence items summary
    pending = [
        {'label': m.label, 'reason': m.remarks or '信息不足',
         'position': m.position_desc or ''}
        for m in conf_dist['low']
    ]

    return render_template('boq.html', project=project, divisions=divisions,
                           measurements=measurements, pending=pending,
                           conf_dist=conf_dist, DIVISIONS=DIVISIONS,
                           CONFIDENCE_LEVELS=CONFIDENCE_LEVELS)


@main_bp.route('/report/<int:report_id>')
@login_required
def view_report(report_id):
    report = Report.query.get_or_404(report_id)
    project = report.project
    if project.user_id != current_user.id and not current_user.is_admin:
        flash('无权访问此报告。', 'danger')
        return redirect(url_for('main.dashboard'))

    measurements = project.measurements.all()
    summary = {}
    if report.summary_json:
        summary = json.loads(report.summary_json)

    return render_template('report.html', report=report, project=project,
                           measurements=measurements, summary=summary)


@main_bp.route('/report/<int:report_id>/download')
@login_required
def download_report(report_id):
    report = Report.query.get_or_404(report_id)
    project = report.project
    if project.user_id != current_user.id and not current_user.is_admin:
        flash('无权下载此报告。', 'danger')
        return redirect(url_for('main.dashboard'))

    if report.file_path and os.path.exists(report.file_path):
        return send_file(report.file_path, as_attachment=True,
                         download_name=f'计量报告_{project.name}.html')

    flash('报告文件不存在。', 'warning')
    return redirect(url_for('main.view_report', report_id=report.id))


@main_bp.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=current_user)
