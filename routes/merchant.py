from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
import pymysql
from utils.db import get_db_connection
from utils.images import ImageError, delete_food_image, save_food_image

merchant_bp = Blueprint('merchant', __name__)

MAX_NAME_LEN = 100
MAX_PRICE = 9999.99


# ========== 公共校验 ==========
def _merchant_status(user_id):
    """查商家审核状态（pending / approved / rejected）"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT merchant_status FROM users WHERE id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else None


def _guard(require_approved=False):
    """未登录 / 非商家 / 未通过审核时返回重定向响应，否则返回 None"""
    if 'user_id' not in session or session.get('role') != 'merchant':
        flash('❌ 请先登录商家账号')
        return redirect(url_for('auth.login_page', next=request.path))

    if require_approved and _merchant_status(session['user_id']) != 'approved':
        flash('⏳ 商家资料还在审核中，审核通过后才能管理美食')
        return redirect(url_for('merchant.merchant_dashboard'))
    return None


def _valid_category(category_id, categories):
    """分类 id 必须真实存在，否则写 NULL，避免外键报错"""
    valid = {str(c['id']) for c in categories}
    return category_id if category_id and str(category_id) in valid else None


def _parse_price(raw):
    """价格校验：必须是 0-9999.99 之间的数字，返回 float 或 None"""
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None
    if price <= 0 or price > MAX_PRICE:
        return None
    return round(price, 2)


# ========== 商家首页（数据看板） ==========
@merchant_bp.route('/merchant/dashboard')
def merchant_dashboard():
    """商家数据看板"""
    blocked = _guard()
    if blocked:
        return blocked

    user_id = session['user_id']
    merchant_status = _merchant_status(user_id)

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

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

    # 商家自己的美食列表（带分类名，避免前端只显示分类 id）
    cursor.execute("""
        SELECT f.*, c.name AS category_name
        FROM foods f
        LEFT JOIN categories c ON f.category_id = c.id
        WHERE f.merchant_id = %s
        ORDER BY f.created_at DESC
    """, (user_id,))
    foods = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('merchant_dashboard.html',
                           total_foods=total_foods,
                           total_ratings=total_ratings,
                           merchant_status=merchant_status,
                           foods=foods)


# ========== 商家上传美食 ==========
@merchant_bp.route('/merchant/food/add', methods=['GET', 'POST'])
def add_food():
    """商家上传美食（支持上传图片）"""
    blocked = _guard(require_approved=True)
    if blocked:
        return blocked

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("SELECT * FROM categories ORDER BY name")
    categories = cursor.fetchall()
    cursor.close()
    conn.close()

    if request.method == 'GET':
        return render_template('merchant_add_food.html', categories=categories)

    # POST：处理提交
    name = (request.form.get('name') or '').strip()
    category_id = _valid_category(request.form.get('category_id'), categories)
    price = _parse_price(request.form.get('price'))
    ingredients = (request.form.get('ingredients') or '').strip()
    description = (request.form.get('description') or '').strip()
    image_file = request.files.get('image')

    # 校验
    if not name or len(name) > MAX_NAME_LEN:
        flash(f'❌ 美食名称必填，且不超过 {MAX_NAME_LEN} 个字')
        return render_template('merchant_add_food.html', categories=categories)
    if price is None:
        flash('❌ 价格需为 0.01 - 9999.99 之间的数字')
        return render_template('merchant_add_food.html', categories=categories)

    # 图片可选；传了就压缩落盘，格式/大小不对直接驳回，不静默忽略
    image_url = None
    if image_file and image_file.filename:
        try:
            image_url = save_food_image(image_file)
        except ImageError as e:
            flash(f'❌ {e}')
            return render_template('merchant_add_food.html', categories=categories)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO foods (name, merchant_id, category_id, price, ingredients,
                               description, image_url, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
        """, (name, session['user_id'], category_id, price,
              ingredients, description, image_url))
        conn.commit()
        flash('✅ 美食已提交，等待管理员审核')
    except Exception:
        conn.rollback()
        if image_url:
            delete_food_image(image_url)
        flash('❌ 上传失败，请稍后重试')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('merchant.merchant_dashboard'))


# ========== 商家编辑美食 ==========
@merchant_bp.route('/merchant/food/edit/<int:food_id>', methods=['GET', 'POST'])
def edit_food(food_id):
    """商家编辑自己上传的美食"""
    blocked = _guard(require_approved=True)
    if blocked:
        return blocked

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 检查该美食是否属于当前商家（越权直接当作不存在）
    cursor.execute(
        "SELECT * FROM foods WHERE id = %s AND merchant_id = %s",
        (food_id, session['user_id'])
    )
    food = cursor.fetchone()
    if not food:
        flash('❌ 该美食不存在或不属于您')
        cursor.close()
        conn.close()
        return redirect(url_for('merchant.merchant_dashboard'))

    cursor.execute("SELECT * FROM categories ORDER BY name")
    categories = cursor.fetchall()

    if request.method == 'GET':
        cursor.close()
        conn.close()
        return render_template('merchant_edit_food.html', food=food, categories=categories)

    # POST：更新
    name = (request.form.get('name') or '').strip()
    category_id = _valid_category(request.form.get('category_id'), categories)
    price = _parse_price(request.form.get('price'))
    ingredients = (request.form.get('ingredients') or '').strip()
    description = (request.form.get('description') or '').strip()
    image_file = request.files.get('image')

    if not name or len(name) > MAX_NAME_LEN:
        flash(f'❌ 美食名称必填，且不超过 {MAX_NAME_LEN} 个字')
        cursor.close()
        conn.close()
        return render_template('merchant_edit_food.html', food=food, categories=categories)
    if price is None:
        flash('❌ 价格需为 0.01 - 9999.99 之间的数字')
        cursor.close()
        conn.close()
        return render_template('merchant_edit_food.html', food=food, categories=categories)

    new_image = None
    if image_file and image_file.filename:
        try:
            new_image = save_food_image(image_file)
        except ImageError as e:
            flash(f'❌ {e}')
            cursor.close()
            conn.close()
            return render_template('merchant_edit_food.html', food=food, categories=categories)

    try:
        cursor.execute("""
            UPDATE foods
            SET name=%s, category_id=%s, price=%s, ingredients=%s, description=%s,
                image_url=COALESCE(%s, image_url)
            WHERE id=%s AND merchant_id=%s
        """, (name, category_id, price, ingredients, description,
              new_image, food_id, session['user_id']))
        conn.commit()
        # 换了新图，把旧的上传图删掉（内置配图不会被删）
        if new_image and food['image_url']:
            delete_food_image(food['image_url'])
        flash('✅ 美食信息已更新')
    except Exception:
        conn.rollback()
        if new_image:
            delete_food_image(new_image)
        flash('❌ 更新失败，请稍后重试')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('merchant.merchant_dashboard'))


# ========== 商家下架美食 ==========
@merchant_bp.route('/merchant/food/offline/<int:food_id>', methods=['POST'])
def offline_food(food_id):
    """商家下架美食"""
    blocked = _guard()
    if blocked:
        return blocked

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE foods SET status='offline'
            WHERE id=%s AND merchant_id=%s
        """, (food_id, session['user_id']))
        conn.commit()
        flash('✅ 美食已下架' if cursor.rowcount else '❌ 未找到该美食')
    except Exception:
        conn.rollback()
        flash('❌ 操作失败，请稍后重试')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('merchant.merchant_dashboard'))


# ========== 商家重新上架 ==========
@merchant_bp.route('/merchant/food/relist/<int:food_id>', methods=['POST'])
def relist_food(food_id):
    """
    重新上架：下架/被拒的美食改回 pending，重新走一遍管理员审核，
    避免商家自己上架绕过审核。
    """
    blocked = _guard(require_approved=True)
    if blocked:
        return blocked

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE foods SET status='pending'
            WHERE id=%s AND merchant_id=%s AND status IN ('offline', 'rejected')
        """, (food_id, session['user_id']))
        conn.commit()
        flash('✅ 已提交重新上架申请，等待管理员审核'
              if cursor.rowcount else '❌ 该美食当前状态无法重新上架')
    except Exception:
        conn.rollback()
        flash('❌ 操作失败，请稍后重试')
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
