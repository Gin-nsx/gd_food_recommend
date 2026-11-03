from flask import Blueprint, render_template, request, redirect, url_for, session, flash,jsonify
import pymysql
from utils.db import get_db_connection

merchant_bp = Blueprint('merchant', __name__)


# ========== 商家首页（数据看板） ==========
@merchant_bp.route('/merchant/dashboard')
def merchant_dashboard():
    """商家数据看板"""
    if 'user_id' not in session or session.get('role') != 'merchant':
        flash('❌ 请先登录商家账号')
        return redirect(url_for('auth.login_page'))

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 获取商家的所有美食
    cursor.execute("""
        SELECT COUNT(*) as total_foods FROM foods WHERE merchant_id = %s
    """, (user_id,))
    total_foods = cursor.fetchone()['total_foods']

    cursor.execute("""
        SELECT COUNT(*) as total_ratings FROM ratings r
        JOIN foods f ON r.food_id = f.id
        WHERE f.merchant_id = %s
    """, (user_id,))
    total_ratings = cursor.fetchone()['total_ratings']

    # 商家自己的美食列表
    cursor.execute("""
        SELECT * FROM foods WHERE merchant_id = %s ORDER BY created_at DESC
    """, (user_id,))
    foods = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('merchant_dashboard.html',
                           total_foods=total_foods,
                           total_ratings=total_ratings,
                           foods=foods)


# ========== 商家上传美食 ==========
@merchant_bp.route('/merchant/food/add', methods=['GET', 'POST'])
def add_food():
    """商家上传美食"""
    if 'user_id' not in session or session.get('role') != 'merchant':
        flash('❌ 请先登录商家账号')
        return redirect(url_for('auth.login_page'))

    # 获取分类列表（供下拉菜单使用）
    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("SELECT * FROM categories ORDER BY name")
    categories = cursor.fetchall()
    cursor.close()
    conn.close()

    if request.method == 'GET':
        return render_template('merchant_add_food.html', categories=categories)

    # POST：处理提交
    name = request.form.get('name')
    category_id = request.form.get('category_id')
    price = request.form.get('price')
    ingredients = request.form.get('ingredients')
    description = request.form.get('description')

    # 简单校验
    if not name or not price:
        flash('❌ 美食名称和价格为必填项')
        return render_template('merchant_add_food.html', categories=categories)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO foods (name, merchant_id, category_id, price, ingredients, description, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending')
        """, (name, session['user_id'], category_id, price, ingredients, description))
        conn.commit()
        flash('✅ 美食上传成功，等待管理员审核')
    except Exception as e:
        flash(f'❌ 上传失败：{e}')
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('merchant.merchant_dashboard'))


# ========== 商家编辑美食 ==========
@merchant_bp.route('/merchant/food/edit/<int:food_id>', methods=['GET', 'POST'])
def edit_food(food_id):
    """商家编辑自己上传的美食"""
    if 'user_id' not in session or session.get('role') != 'merchant':
        flash('❌ 请先登录商家账号')
        return redirect(url_for('auth.login_page'))

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 检查该美食是否属于当前商家
    cursor.execute("SELECT * FROM foods WHERE id = %s AND merchant_id = %s", (food_id, session['user_id']))
    food = cursor.fetchone()
    if not food:
        flash('❌ 该美食不存在或不属于您')
        cursor.close()
        conn.close()
        return redirect(url_for('merchant.merchant_dashboard'))

    if request.method == 'GET':
        cursor.execute("SELECT * FROM categories ORDER BY name")
        categories = cursor.fetchall()
        cursor.close()
        conn.close()
        return render_template('merchant_edit_food.html', food=food, categories=categories)

    # POST：更新
    name = request.form.get('name')
    category_id = request.form.get('category_id')
    price = request.form.get('price')
    ingredients = request.form.get('ingredients')
    description = request.form.get('description')

    try:
        cursor.execute("""
            UPDATE foods SET name=%s, category_id=%s, price=%s, ingredients=%s, description=%s
            WHERE id=%s AND merchant_id=%s
        """, (name, category_id, price, ingredients, description, food_id, session['user_id']))
        conn.commit()
        flash('✅ 美食信息已更新')
    except Exception as e:
        flash(f'❌ 更新失败：{e}')
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('merchant.merchant_dashboard'))


# ========== 商家下架美食 ==========
@merchant_bp.route('/merchant/food/offline/<int:food_id>')
def offline_food(food_id):
    """商家下架美食"""
    if 'user_id' not in session or session.get('role') != 'merchant':
        flash('❌ 请先登录商家账号')
        return redirect(url_for('auth.login_page'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE foods SET status='offline' 
            WHERE id=%s AND merchant_id=%s
        """, (food_id, session['user_id']))
        conn.commit()
        flash('✅ 美食已下架')
    except Exception as e:
        flash(f'❌ 操作失败：{e}')
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('merchant.merchant_dashboard'))

# ========== 商家数据统计接口（图表用） ==========
@merchant_bp.route('/merchant/stats/rating')
def merchant_stats_rating():
    """本店美食评分（柱状图用）"""
    if 'user_id' not in session or session.get('role') != 'merchant':
        return jsonify({'error': '无权访问'}), 403

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT f.name, f.avg_rating
        FROM foods f
        WHERE f.merchant_id = %s AND f.status = 'approved'
        ORDER BY f.avg_rating DESC
        LIMIT 10
    """, (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify({
        'labels': [r[0] for r in rows],
        'values': [float(r[1]) if r[1] else 0 for r in rows]
    })


@merchant_bp.route('/merchant/stats/likes')
def merchant_stats_likes():
    """本店美食点赞排行（横向柱状图用）"""
    if 'user_id' not in session or session.get('role') != 'merchant':
        return jsonify({'error': '无权访问'}), 403

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT f.name, f.like_count
        FROM foods f
        WHERE f.merchant_id = %s AND f.status = 'approved'
        ORDER BY f.like_count DESC
        LIMIT 10
    """, (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    data = [{'name': r[0], 'value': r[1] or 0} for r in rows]
    data.reverse()  # 横向柱状图从下往上显示

    return jsonify(data)