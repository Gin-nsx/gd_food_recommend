from flask import Flask, render_template, request, session
import pymysql
from config import DB_CONFIG
from routes.auth import auth_bp
from routes.merchant import merchant_bp
from routes.admin import admin_bp
from routes.food import food_bp
from routes.user import user_bp
from routes.recommend import recommend_bp
app = Flask(__name__)
app.secret_key = 'your-secret-key-here-change-it-later'

# 注册蓝图
app.register_blueprint(auth_bp)
app.register_blueprint(merchant_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(food_bp)
app.register_blueprint(user_bp)
app.register_blueprint(recommend_bp)
@app.route('/')
def index():
    """首页：展示美食列表，支持搜索和分类筛选"""
    keyword = request.args.get('q', '').strip()
    category_id = request.args.get('category', '')

    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    sql = """
        SELECT f.*, u.username as merchant_name 
        FROM foods f
        LEFT JOIN users u ON f.merchant_id = u.id
        WHERE f.status = 'approved'
    """
    params = []

    if keyword:
        sql += " AND (f.name LIKE %s OR f.ingredients LIKE %s OR f.description LIKE %s)"
        like_keyword = f"%{keyword}%"
        params.extend([like_keyword, like_keyword, like_keyword])

    if category_id and category_id.isdigit():
        sql += " AND f.category_id = %s"
        params.append(int(category_id))

    sql += " ORDER BY f.avg_rating DESC"

    cursor.execute(sql, params)
    foods = cursor.fetchall()

    # 获取分类列表
    cursor.execute("SELECT id, name FROM categories ORDER BY name")
    categories = cursor.fetchall()

    # 获取当前用户的过敏和忌口（如果已登录）
    user_allergy = ''
    user_taboo = ''
    if session.get('user_id'):
        cursor.execute(
            "SELECT allergy, taboo FROM users WHERE id = %s",
            (session['user_id'],)
        )
        pref = cursor.fetchone()
        if pref:
            user_allergy = pref['allergy'] or ''
            user_taboo = pref['taboo'] or ''

    cursor.close()
    conn.close()

    return render_template('index.html',
                           foods=foods,
                           categories=categories,
                           keyword=keyword,
                           selected_category=category_id,
                           user_allergy=user_allergy,
                           user_taboo=user_taboo)


if __name__ == '__main__':
    app.run(debug=True)


