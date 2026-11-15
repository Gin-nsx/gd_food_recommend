# 注册/登录路由
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models.user import find_user_by_username, create_user, check_password
from utils.security import (
    client_key,
    clear_failures,
    is_locked,
    lock_remaining,
    record_failure,
)

auth_bp = Blueprint('auth', __name__)

# 可自助注册的角色白名单：绝不允许通过表单注册成管理员
ALLOWED_ROLES = ('user', 'merchant')


def _safe_next(target):
    """只允许跳回本站路径，防止被拿去当开放重定向（钓鱼）"""
    if not target:
        return None
    if target.startswith('/') and not target.startswith('//'):
        return target
    return None


# ========== 注册页面 ==========
@auth_bp.route('/register', methods=['GET'])
def register_page():
    """显示注册页面"""
    return render_template('register.html')


# ========== 处理注册提交 ==========
@auth_bp.route('/register', methods=['POST'])
def register():
    """处理注册表单提交"""
    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''
    role = (request.form.get('role') or 'user').strip().lower()
    merchant_address = (request.form.get('merchant_address') or '').strip()

    # 1. 角色白名单：表单可以随便改，这里必须拦住，否则能直接注册成管理员
    if role not in ALLOWED_ROLES:
        role = 'user'

    # 2. 基础校验
    if not (2 <= len(username) <= 20):
        flash('❌ 用户名长度需在 2-20 个字符之间')
        return redirect(url_for('auth.register_page'))
    if not (6 <= len(password) <= 64):
        flash('❌ 密码长度需在 6-64 位之间')
        return redirect(url_for('auth.register_page'))
    if role == 'merchant' and not merchant_address:
        flash('❌ 商家注册需要填写店铺地址')
        return redirect(url_for('auth.register_page'))

    # 3. 检查用户名是否已存在
    if find_user_by_username(username):
        flash('❌ 用户名已存在，请换一个')
        return redirect(url_for('auth.register_page'))

    # 4. 创建用户
    try:
        create_user(username, password, role, merchant_address or None)
        if role == 'merchant':
            flash('✅ 注册成功！商家账号需管理员审核通过后才能上传美食')
        else:
            flash('✅ 注册成功！请登录')
        return redirect(url_for('auth.login_page'))
    except Exception:
        # 不把数据库异常原文抛给用户，避免泄露表结构
        flash('❌ 注册失败，请稍后重试')
        return redirect(url_for('auth.register_page'))


# ========== 登录页面 ==========
@auth_bp.route('/login', methods=['GET'])
def login_page():
    """显示登录页面"""
    return render_template('login.html', next=request.args.get('next', ''))


# ========== 处理登录提交 ==========
@auth_bp.route('/login', methods=['POST'])
def login():
    """处理登录表单提交（含失败次数限制，防暴力破解）"""
    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''
    next_url = _safe_next(request.form.get('next'))

    key = client_key(username)
    if is_locked(key):
        remain = lock_remaining(key)
        flash(f'❌ 密码错误次数过多，请 {remain // 60 + 1} 分钟后再试')
        return redirect(url_for('auth.login_page'))

    user = find_user_by_username(username)
    # 用户不存在与密码错误给同一句提示，避免被用来枚举用户名
    if not user or not check_password(user, password):
        record_failure(key)
        flash('❌ 用户名或密码错误')
        return redirect(url_for('auth.login_page'))

    if not user['is_active']:
        flash('❌ 账号已被禁用，请联系管理员')
        return redirect(url_for('auth.login_page'))

    clear_failures(key)

    # 登录成功后重新生成会话，避免会话固定攻击
    session.clear()
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']

    flash(f'✅ 欢迎回来，{user["username"]}！')

    if next_url:
        return redirect(next_url)

    # 不论什么角色（含管理员、商家），登录后先进首页，
    # 想进后台/商家中心从导航栏点过去即可，避免"一登录就掉进后台"。
    return redirect(url_for('index'))


# ========== 退出登录 ==========
@auth_bp.route('/logout', methods=['POST'])
def logout():
    """退出登录（POST，避免被 <img src> 之类的 GET 请求强制下线）"""
    session.clear()
    flash('✅ 已退出登录')
    return redirect(url_for('auth.login_page'))
