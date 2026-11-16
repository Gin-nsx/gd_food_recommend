from flask import Flask, render_template, request, session, flash, redirect, url_for, jsonify, g
from datetime import datetime
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
from utils.daily_pick import get_daily_picks
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


# ========== 统一错误页 ==========
# 不注册这些处理器的话，出错会看到 Flask 默认的英文页（生产环境只有一句
# "Internal Server Error"），对普通用户既看不懂也不知道下一步该干什么。
ERROR_PAGES = {
    403: ('没有访问权限',
          '这个页面需要更高的权限才能打开。如果你是商家或管理员，请确认登录的账号是否正确。',
          ''),
    404: ('这个页面不存在',
          '链接可能已经失效，或者美食已经被下架删除了。',
          ''),
    405: ('请求方式不被允许',
          '这个地址不支持当前操作，通常是从收藏夹或旧链接直接打开导致的。',
          ''),
    500: ('服务器出了点小状况',
          '已经记录下这次异常，稍后再试一次通常就好了。',
          '如果反复出现，可以把当前时间和操作步骤反馈给管理员。'),
}


def _render_error(code):
    title, message, hint = ERROR_PAGES.get(
        code, ('出错了', '请求没有正常完成，请稍后重试。', ''))
    return render_template('error.html', code=code, title=title,
                           message=message, hint=hint), code


@app.errorhandler(403)
def handle_403(e):
    return _render_error(403)


@app.errorhandler(404)
def handle_404(e):
    return _render_error(404)


@app.errorhandler(405)
def handle_405(e):
    return _render_error(405)


@app.errorhandler(500)
def handle_500(e):
    # 异常信息进日志，不回显给用户（避免泄露表名、路径等内部信息）
    app.logger.error('未捕获的服务器异常: %s', getattr(e, 'original_exception', e))
    return _render_error(500)


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
    black_food_ids = []
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

        # 拉黑的菜不出现在热榜里（只是尊重黑名单，不改变榜单的全局口径）
        cursor.execute(
            "SELECT food_id FROM blacklists WHERE user_id = %s AND food_id IS NOT NULL",
            (session['user_id'],)
        )
        black_food_ids = [row['food_id'] for row in cursor.fetchall()]

    cursor.close()
    conn.close()

    # 每日热榜：全站同一份，与「猜你喜欢」的个性化推荐是两条独立链路。
    # 只有在首页默认视图（没有搜索词、没有选分类）时才需要，避免干扰筛选结果。
    daily_picks = []
    if not keyword and not category_id:
        daily_picks = get_daily_picks(limit=5, exclude_ids=black_food_ids)['items']

    return render_template('index.html',
                           foods=foods,
                           categories=categories,
                           keyword=keyword,
                           synonyms=expand_note(keyword),
                           selected_category=category_id,
                           user_allergy=user_allergy,
                           user_taboo=user_taboo,
                           hide_risk=hide_risk,
                           daily_picks=daily_picks,
                           today_str=f"{datetime.now().month} 月 {datetime.now().day} 日")


if __name__ == '__main__':
    # debug 由环境变量 FLASK_DEBUG 控制，默认关闭（开着等于把调试器暴露给外部）
    app.run(host=HOST, port=PORT, debug=DEBUG)