from flask import Blueprint, render_template, session, flash, redirect, url_for, request, jsonify
import pymysql
from werkzeug.security import check_password_hash, generate_password_hash
from utils.db import get_db_connection

user_bp = Blueprint('user', __name__)


# ========== 个人中心首页 ==========
@user_bp.route('/user/dashboard')
def user_dashboard():
    """个人中心首页"""
    if 'user_id' not in session:
        flash('❌ 请先登录')
        return redirect(url_for('auth.login_page', next=request.path))

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 查询用户信息
    cursor.execute("""
        SELECT id, username, role, is_active, merchant_status, merchant_address,
               merchant_name, allergy, taboo, hide_risk, created_at
        FROM users WHERE id = %s
    """, (user_id,))
    user = cursor.fetchone()

    # 统计数据
    cursor.execute("SELECT COUNT(*) as count FROM favorites WHERE user_id = %s", (user_id,))
    favorite_count = cursor.fetchone()['count']

    cursor.execute("SELECT COUNT(*) as count FROM histories WHERE user_id = %s", (user_id,))
    history_count = cursor.fetchone()['count']

    cursor.execute("SELECT COUNT(*) as count FROM blacklists WHERE user_id = %s", (user_id,))
    blacklist_count = cursor.fetchone()['count']

    cursor.execute("SELECT COUNT(*) as count FROM ratings WHERE user_id = %s", (user_id,))
    rating_count = cursor.fetchone()['count']

    cursor.execute("SELECT COUNT(*) as count FROM comments WHERE user_id = %s", (user_id,))
    comment_count = cursor.fetchone()['count']

    cursor.execute("SELECT COUNT(*) as count FROM reports WHERE reporter_id = %s", (user_id,))
    report_count = cursor.fetchone()['count']

    cursor.close()
    conn.close()

    return render_template('user_dashboard.html',
                           user=user,
                           favorite_count=favorite_count,
                           history_count=history_count,
                           blacklist_count=blacklist_count,
                           rating_count=rating_count,
                           comment_count=comment_count,
                           report_count=report_count)


# ========== 更新过敏/忌口 ==========
@user_bp.route('/user/update_preferences', methods=['POST'])
def update_preferences():
    """更新用户的过敏和忌口信息"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    user_id = session['user_id']
    allergy = request.form.get('allergy', '').strip()
    taboo = request.form.get('taboo', '').strip()
    hide_risk = 1 if request.form.get('hide_risk') in ('1', 'on', 'true') else 0

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE users SET allergy = %s, taboo = %s, hide_risk = %s WHERE id = %s
        """, (allergy, taboo, hide_risk, user_id))
        conn.commit()
        return jsonify({'success': True, 'message': '已保存'})
    except Exception:
        conn.rollback()
        return jsonify({'success': False, 'message': '操作失败，请稍后重试'}), 500
    finally:
        cursor.close()
        conn.close()


# ========== 修改密码 ==========
@user_bp.route('/user/password', methods=['POST'])
def change_password():
    """修改登录密码：校验原密码 → 检查新密码强度 → 重新哈希"""
    if 'user_id' not in session:
        flash('❌ 请先登录')
        return redirect(url_for('auth.login_page', next=request.path))

    user_id = session['user_id']
    old_password = request.form.get('old_password') or ''
    new_password = request.form.get('new_password') or ''
    confirm_password = request.form.get('confirm_password') or ''

    if not (6 <= len(new_password) <= 64):
        flash('❌ 新密码长度需在 6-64 位之间')
        return redirect(url_for('user.user_dashboard'))
    if new_password != confirm_password:
        flash('❌ 两次输入的新密码不一致')
        return redirect(url_for('user.user_dashboard'))
    if new_password == old_password:
        flash('❌ 新密码不能与原密码相同')
        return redirect(url_for('user.user_dashboard'))

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("SELECT password FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        if not row or not check_password_hash(row['password'], old_password):
            flash('❌ 原密码不正确')
            return redirect(url_for('user.user_dashboard'))

        cursor.execute(
            "UPDATE users SET password = %s WHERE id = %s",
            (generate_password_hash(new_password), user_id)
        )
        conn.commit()
        flash('✅ 密码已修改，下次登录请使用新密码')
    except Exception:
        conn.rollback()
        flash('❌ 修改失败，请稍后重试')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('user.user_dashboard'))


# ========== 我的收藏 ==========
@user_bp.route('/user/favorites')
def user_favorites():
    """查看当前用户的收藏列表"""
    if 'user_id' not in session:
        flash('❌ 请先登录')
        return redirect(url_for('auth.login_page', next=request.path))

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    cursor.execute("""
        SELECT f.*, u.username as merchant_name
        FROM favorites fav
        JOIN foods f ON fav.food_id = f.id
        LEFT JOIN users u ON f.merchant_id = u.id
        WHERE fav.user_id = %s
        ORDER BY fav.created_at DESC
    """, (user_id,))

    favorites = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('user_favorites.html', favorites=favorites)


# ========== 取消收藏 ==========
@user_bp.route('/user/favorite/remove/<int:food_id>', methods=['POST'])
def remove_favorite(food_id):
    """取消收藏"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "DELETE FROM favorites WHERE user_id = %s AND food_id = %s",
            (user_id, food_id)
        )
        conn.commit()
        return jsonify({'success': True, 'message': '已取消收藏'})
    except Exception:
        conn.rollback()
        return jsonify({'success': False, 'message': '操作失败，请稍后重试'}), 500
    finally:
        cursor.close()
        conn.close()


# ========== 黑名单管理 ==========
@user_bp.route('/user/blacklist')
def user_blacklist():
    """查看当前用户的黑名单"""
    if 'user_id' not in session:
        flash('❌ 请先登录')
        return redirect(url_for('auth.login_page', next=request.path))

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 拉黑的美食
    cursor.execute("""
        SELECT b.id as blacklist_id, b.created_at, b.reason,
               f.id as food_id, f.name as food_name, f.price, f.avg_rating,
               u.username as merchant_name
        FROM blacklists b
        LEFT JOIN foods f ON b.food_id = f.id
        LEFT JOIN users u ON f.merchant_id = u.id
        WHERE b.user_id = %s AND b.food_id IS NOT NULL
        ORDER BY b.created_at DESC
    """, (user_id,))
    food_blacklist = cursor.fetchall()

    # 拉黑的商家
    cursor.execute("""
        SELECT b.id as blacklist_id, b.created_at, b.reason,
               u.id as merchant_id, u.username as merchant_name, u.merchant_address
        FROM blacklists b
        JOIN users u ON b.merchant_id = u.id
        WHERE b.user_id = %s AND b.merchant_id IS NOT NULL
        ORDER BY b.created_at DESC
    """, (user_id,))
    merchant_blacklist = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('user_blacklist.html',
                           food_blacklist=food_blacklist,
                           merchant_blacklist=merchant_blacklist)


# ========== 拉黑美食 ==========
@user_bp.route('/user/blacklist/food/<int:food_id>', methods=['POST'])
def blacklist_food(food_id):
    """拉黑美食"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    user_id = session['user_id']
    reason = request.form.get('reason', '不感兴趣')

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT id FROM blacklists WHERE user_id = %s AND food_id = %s",
            (user_id, food_id)
        )
        if cursor.fetchone():
            return jsonify({'success': False, 'message': '已拉黑该美食'})

        cursor.execute("""
            INSERT INTO blacklists (user_id, food_id, reason)
            VALUES (%s, %s, %s)
        """, (user_id, food_id, reason))
        conn.commit()
        return jsonify({'success': True, 'message': '已拉黑该美食'})
    except Exception:
        conn.rollback()
        return jsonify({'success': False, 'message': '操作失败，请稍后重试'}), 500
    finally:
        cursor.close()
        conn.close()


# ========== 取消拉黑 ==========
@user_bp.route('/user/blacklist/remove/<int:blacklist_id>', methods=['POST'])
def remove_blacklist(blacklist_id):
    """取消拉黑"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "DELETE FROM blacklists WHERE id = %s AND user_id = %s",
            (blacklist_id, user_id)
        )
        conn.commit()
        return jsonify({'success': True, 'message': '已取消拉黑'})
    except Exception:
        conn.rollback()
        return jsonify({'success': False, 'message': '操作失败，请稍后重试'}), 500
    finally:
        cursor.close()
        conn.close()