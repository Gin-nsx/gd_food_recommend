from flask import Flask, render_template, request, session, flash, redirect, url_for, jsonify, g
import pymysql

from config import (
    DEBUG,
    HOST,
    PORT,
    SECRET_KEY,
    SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE,
    PERMANENT_SESSION_LIFETIME,
)
from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.food import food_bp
from routes.merchant import merchant_bp
from routes.message import message_bp, unread_count
from routes.recommend import recommend_bp
from routes.user import user_bp
from utils.db import get_db_connection
from utils.search import expand_terms, expand_note
from utils.security import get_csrf_token, validate_csrf

app = Flask(__name__)
# 密钥不写死在源码里，见 config.load_secret_key()
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE=SESSION_COOKIE_SAMESITE,
    PERMANENT_SESSION_LIFETIME=PERMANENT_SESSION_LIFETIME,
)

# 注册蓝图
app.register_blueprint(auth_bp)
app.register_blueprint(merchant_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(food_bp)
app.register_blueprint(user_bp)
app.register_blueprint(recommend_bp)
app.register_blueprint(message_bp)


# ========== 全局：CSRF 防护 ==========
# 所有写操作（POST/PUT/PATCH/DELETE）必须带上会话内的 CSRF 令牌。
# 令牌来源：请求头 X-CSRFToken（AJAX）或表单隐藏域 _csrf_token（普通表单）。
# 模板里用 {{ csrf_token() }} 取；base.html 的脚本会自动给没写的表单补上。
@app.before_request
def csrf_protect():
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return None
    if validate_csrf():
        return None

    # AJAX 请求返回 JSON，普通表单请求回到来源页并提示
    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.is_json
        or request.path.startswith('/api/')
    )
    if is_ajax:
        return jsonify({'success': False, 'message': '页面已过期，请刷新后重试'}), 400

    flash('❌ 页面已过期，请重新提交')
    referrer = request.referrer
    if referrer and referrer.startswith(request.host_url):
        return redirect(referrer)
    return redirect(url_for('index'))


@app.context_processor
def inject_csrf_token():
    """让所有模板都能直接调用 csrf_token()，并拿到导航栏的未读消息数"""
    return {
        'csrf_token': get_csrf_token,
        'unread_messages': _unread_messages(),
    }


def _unread_messages():
    """每次请求只查一次库，供导航栏红点使用"""
    user_id = session.get('user_id')
    if not user_id:
        return 0
    if not hasattr(g, 'unread_messages'):
        g.unread_messages = unread_count(user_id)
    return g.unread_messages


@app.route('/')
def index():
    """首页：展示美食列表，支持搜索和分类筛选"""
    keyword = request.args.get('q', '').strip()
    category_id = request.args.get('category', '')

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    sql = """
        SELECT f.*, COALESCE(u.merchant_name, u.username) AS merchant_name
        FROM foods f
        LEFT JOIN users u ON f.merchant_id = u.id
        WHERE f.status = 'approved'
    """
    params = []

    # 关键词：先做同义词扩展（白斩鸡 → 白切鸡、皮皮虾 → 濑尿虾…），
    # 再把每个词在「菜名 / 食材 / 描述」三个字段上做 LIKE，彼此 OR
    terms = expand_terms(keyword)
    if terms:
        or_parts = []
        for term in terms:
            or_parts.append(
                "(f.name LIKE %s OR f.ingredients LIKE %s OR f.description LIKE %s)"
            )
            like = f"%{term}%"
            params.extend([like, like, like])
        sql += " AND (" + " OR ".join(or_parts) + ")"

    if category_id and category_id.isdigit():
        sql += " AND f.category_id = %s"
        params.append(int(category_id))

    # 评分相同时用点赞数、再按 id 兜底，保证每次刷新的顺序稳定
    sql += " ORDER BY f.avg_rating DESC, f.like_count DESC, f.id ASC"

    cursor.execute(sql, params)
    foods = cursor.fetchall()

    # 获取分类列表
    cursor.execute("SELECT id, name FROM categories ORDER BY name")
    categories = cursor.fetchall()

    # 获取当前用户的过敏和忌口（如果已登录）
    user_allergy = ''
    user_taboo = ''
    hide_risk = False
    if session.get('user_id'):
        cursor.execute(
            "SELECT allergy, taboo, hide_risk FROM users WHERE id = %s",
            (session['user_id'],)
        )
        pref = cursor.fetchone()
        if pref:
            user_allergy = pref['allergy'] or ''
            user_taboo = pref['taboo'] or ''
            hide_risk = bool(pref['hide_risk'])

    cursor.close()
    conn.close()

    return render_template('index.html',
                           foods=foods,
                           categories=categories,
                           keyword=keyword,
                           synonyms=expand_note(keyword),
                           selected_category=category_id,
                           user_allergy=user_allergy,
                           user_taboo=user_taboo,
                           hide_risk=hide_risk)


if __name__ == '__main__':
    # debug 由环境变量 FLASK_DEBUG 控制，默认关闭（开着等于把调试器暴露给外部）
    app.run(host=HOST, port=PORT, debug=DEBUG)