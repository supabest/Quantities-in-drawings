from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, current_app
from flask_login import login_required, current_user
from app import db
from app.models import Project, Drawing, Measurement, Report
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
        drawing_type = request.form.get('drawing_type', 'floor_plan')

        if not name:
            flash('请输入项目名称。', 'danger')
            return render_template('project_new.html')

        project = Project(
            name=name,
            description=description,
            address=address,
            drawing_type=drawing_type,
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
    measurements = project.measurements.order_by(Measurement.category).all()
    report = project.report

    # Organize measurements by category
    categorized = {'room': [], 'wall': [], 'door': [], 'window': [], 'fixture': []}
    for m in measurements:
        cat = m.category if m.category in categorized else 'fixture'
        categorized[cat].append(m)

    return render_template('project_detail.html', project=project, drawings=drawings,
                           measurements=categorized, report=report)


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
        project.drawing_type = request.form.get('drawing_type', project.drawing_type)
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


@main_bp.route('/project/<int:project_id>/process')
@login_required
def process_project(project_id):
    """Trigger processing of all unprocessed drawings in a project."""
    project = Project.query.get_or_404(project_id)

    if project.user_id != current_user.id and not current_user.is_admin:
        flash('无权操作此项目。', 'danger')
        return redirect(url_for('main.dashboard'))

    drawings = project.drawings.all()
    if not drawings:
        flash('请先上传图纸。', 'warning')
        return redirect(url_for('main.project_detail', project_id=project.id))

    # Trigger async processing via API call (handled by frontend JS)
    flash('开始自动计量处理...', 'info')
    return redirect(url_for('main.project_detail', project_id=project.id))


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
