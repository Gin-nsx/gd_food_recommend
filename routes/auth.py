# 注册/登录路由
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models.user import find_user_by_username, create_user, check_password

auth_bp = Blueprint('auth', __name__)


# ========== 注册页面 ==========
@auth_bp.route('/register', methods=['GET'])
def register_page():
    """显示注册页面"""
    return render_template('register.html')


# ========== 处理注册提交 ==========
@auth_bp.route('/register', methods=['POST'])
def register():
    """处理注册表单提交"""
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role', 'user')
    merchant_address = request.form.get('merchant_address')

    # 1. 检查用户名是否已存在
    existing = find_user_by_username(username)
    if existing:
        flash('❌ 用户名已存在，请换一个')
        return redirect(url_for('auth.register_page'))

    # 2. 检查密码长度（简单校验）
    if len(password) < 6:
        flash('❌ 密码至少6位')
        return redirect(url_for('auth.register_page'))

    # 3. 创建用户
    try:
        user_id = create_user(username, password, role, merchant_address)
        flash('✅ 注册成功！请登录')
        return redirect(url_for('auth.login_page'))
    except Exception as e:
        flash(f'❌ 注册失败：{e}')
        return redirect(url_for('auth.register_page'))


# ========== 登录页面 ==========
@auth_bp.route('/login', methods=['GET'])
def login_page():
    """显示登录页面"""
    return render_template('login.html')


# ========== 处理登录提交 ==========
@auth_bp.route('/login', methods=['POST'])
def login():
    """处理登录表单提交"""
    username = request.form.get('username')
    password = request.form.get('password')

    user = find_user_by_username(username)
    if not user:
        flash('❌ 用户名不存在')
        return redirect(url_for('auth.login_page'))

    if not check_password(user, password):
        flash('❌ 密码错误')
        return redirect(url_for('auth.login_page'))

    # 检查账号是否被禁用
    if not user['is_active']:
        flash('❌ 账号已被禁用，请联系管理员')
        return redirect(url_for('auth.login_page'))

    # 登录成功，写入 session
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']

    flash(f'✅ 欢迎回来，{username}！')

    # 根据角色跳转不同页面
    if user['role'] == 'admin':
        return redirect(url_for('index'))  # ← 先统一跳首页
    elif user['role'] == 'merchant':
        return redirect(url_for('index'))  # ← 先统一跳首页
    else:
        return redirect(url_for('index'))  # ← 改成 index


# ========== 退出登录 ==========
@auth_bp.route('/logout')
def logout():
    """退出登录，清空session"""
    session.clear()
    flash('✅ 已退出登录')
    return redirect(url_for('auth.login_page'))