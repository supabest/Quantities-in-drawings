from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.models import User
from datetime import datetime

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            if not user.is_approved:
                flash('您的账户尚未通过管理员审核，请耐心等待。', 'warning')
                return render_template('auth/login.html')

            if not user.is_active:
                flash('您的账户已被禁用，请联系管理员。', 'danger')
                return render_template('auth/login.html')

            login_user(user, remember=request.form.get('remember'))
            user.last_login = datetime.utcnow()
            db.session.commit()

            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('main.dashboard'))

        flash('邮箱或密码错误，请重试。', 'danger')

    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        company = request.form.get('company', '').strip()
        phone = request.form.get('phone', '').strip()

        # Validation
        if not username or not email or not password:
            flash('请填写所有必填字段。', 'danger')
            return render_template('auth/register.html')

        if password != confirm_password:
            flash('两次输入的密码不一致。', 'danger')
            return render_template('auth/register.html')

        if len(password) < 6:
            flash('密码长度至少6位。', 'danger')
            return render_template('auth/register.html')

        if User.query.filter_by(email=email).first():
            flash('该邮箱已被注册。', 'danger')
            return render_template('auth/register.html')

        if User.query.filter_by(username=username).first():
            flash('该用户名已被使用。', 'danger')
            return render_template('auth/register.html')

        user = User(
            username=username,
            email=email,
            company=company,
            phone=phone,
            is_approved=False
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash('注册成功！请等待管理员审核您的账户。', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('您已成功退出登录。', 'info')
    return redirect(url_for('auth.login'))


# --- Admin user management ---

@auth_bp.route('/admin/users')
@login_required
def admin_users():
    if not current_user.is_admin:
        flash('无权访问此页面。', 'danger')
        return redirect(url_for('main.dashboard'))

    page = request.args.get('page', 1, type=int)
    users = User.query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template('auth/admin_users.html', users=users)


@auth_bp.route('/admin/approve/<int:user_id>')
@login_required
def approve_user(user_id):
    if not current_user.is_admin:
        flash('无权执行此操作。', 'danger')
        return redirect(url_for('main.dashboard'))

    user = User.query.get_or_404(user_id)
    user.is_approved = True
    db.session.commit()
    flash(f'已批准用户 "{user.username}" 的账户。', 'success')
    return redirect(url_for('auth.admin_users'))


@auth_bp.route('/admin/toggle-active/<int:user_id>')
@login_required
def toggle_user_active(user_id):
    if not current_user.is_admin:
        flash('无权执行此操作。', 'danger')
        return redirect(url_for('main.dashboard'))

    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('不能禁用自己。', 'warning')
        return redirect(url_for('auth.admin_users'))

    user.is_active = not user.is_active
    db.session.commit()
    status = '启用' if user.is_active else '禁用'
    flash(f'已{status}用户 "{user.username}"。', 'success')
    return redirect(url_for('auth.admin_users'))


@auth_bp.route('/admin/delete-user/<int:user_id>')
@login_required
def delete_user(user_id):
    if not current_user.is_admin:
        flash('无权执行此操作。', 'danger')
        return redirect(url_for('main.dashboard'))

    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('不能删除自己。', 'warning')
        return redirect(url_for('auth.admin_users'))

    db.session.delete(user)
    db.session.commit()
    flash(f'已删除用户 "{user.username}"。', 'success')
    return redirect(url_for('auth.admin_users'))
