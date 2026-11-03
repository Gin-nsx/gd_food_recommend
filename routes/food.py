from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
import pymysql
from utils.db import get_db_connection

food_bp = Blueprint('food', __name__)


# ========== 美食详情页 ==========
@food_bp.route('/food/<int:food_id>')
def food_detail(food_id):
    """美食详情页"""
    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    cursor.execute("""
        SELECT f.*, 
               u.username as merchant_name,
               u.id as merchant_id,
               c.name as category_name
        FROM foods f
        LEFT JOIN users u ON f.merchant_id = u.id
        LEFT JOIN categories c ON f.category_id = c.id
        WHERE f.id = %s AND f.status = 'approved'
    """, (food_id,))
    food = cursor.fetchone()

    if not food:
        cursor.close()
        conn.close()
        flash('❌ 该美食不存在或未通过审核')
        return redirect(url_for('index'))

    cursor.execute("""
        SELECT id, name, avg_rating, price, image_url
        FROM foods
        WHERE merchant_id = %s AND id != %s AND status = 'approved'
        ORDER BY avg_rating DESC
        LIMIT 6
    """, (food['merchant_id'], food_id))
    other_foods = cursor.fetchall()

    cursor.execute("""
        SELECT 
            COUNT(*) as total_ratings,
            AVG(score) as avg_score,
            SUM(CASE WHEN score = 5 THEN 1 ELSE 0 END) as star_5,
            SUM(CASE WHEN score = 4 THEN 1 ELSE 0 END) as star_4,
            SUM(CASE WHEN score = 3 THEN 1 ELSE 0 END) as star_3,
            SUM(CASE WHEN score = 2 THEN 1 ELSE 0 END) as star_2,
            SUM(CASE WHEN score = 1 THEN 1 ELSE 0 END) as star_1
        FROM ratings
        WHERE food_id = %s
    """, (food_id,))
    rating_stats = cursor.fetchone()

    user_rating = None
    user_liked = False
    user_favorited = False
    user_blacklisted = False

    if session.get('user_id'):
        user_id = session['user_id']

        cursor.execute(
            "SELECT score FROM ratings WHERE user_id = %s AND food_id = %s",
            (user_id, food_id)
        )
        rating_result = cursor.fetchone()
        user_rating = rating_result['score'] if rating_result else None

        cursor.execute(
            "SELECT id FROM likes WHERE user_id = %s AND food_id = %s",
            (user_id, food_id)
        )
        user_liked = cursor.fetchone() is not None

        cursor.execute(
            "SELECT id FROM favorites WHERE user_id = %s AND food_id = %s",
            (user_id, food_id)
        )
        user_favorited = cursor.fetchone() is not None

        cursor.execute(
            "SELECT id FROM blacklists WHERE user_id = %s AND food_id = %s",
            (user_id, food_id)
        )
        user_blacklisted = cursor.fetchone() is not None

        cursor.execute("""
            INSERT INTO histories (user_id, food_id, last_view_time)
            VALUES (%s, %s, NOW())
            ON DUPLICATE KEY UPDATE last_view_time = NOW()
        """, (user_id, food_id))
        conn.commit()

    cursor.execute("""
        SELECT c.*, u.username
        FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.food_id = %s
        ORDER BY c.created_at DESC
        LIMIT 20
    """, (food_id,))
    comments = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('food_detail.html',
                           food=food,
                           other_foods=other_foods,
                           rating_stats=rating_stats,
                           user_rating=user_rating,
                           user_liked=user_liked,
                           user_favorited=user_favorited,
                           user_blacklisted=user_blacklisted,
                           comments=comments)


# ========== 处理评分 ==========
@food_bp.route('/food/<int:food_id>/rate', methods=['POST'])
def rate_food(food_id):
    """提交或修改评分"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    score = request.form.get('score')
    if not score or not score.isdigit() or int(score) < 1 or int(score) > 5:
        return jsonify({'success': False, 'message': '评分必须是1-5分'}), 400

    user_id = session['user_id']
    score = int(score)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO ratings (user_id, food_id, score)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE score = %s
        """, (user_id, food_id, score, score))

        cursor.execute("""
            UPDATE foods f
            SET avg_rating = (
                SELECT AVG(score) FROM ratings WHERE food_id = %s
            )
            WHERE f.id = %s
        """, (food_id, food_id))

        conn.commit()
        flash('✅ 评分成功！')
        return jsonify({'success': True, 'message': '评分成功'})

    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ========== 切换点赞 ==========
@food_bp.route('/food/<int:food_id>/like', methods=['POST'])
def toggle_like(food_id):
    """点赞/取消点赞"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT id FROM likes WHERE user_id = %s AND food_id = %s",
            (user_id, food_id)
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                "DELETE FROM likes WHERE user_id = %s AND food_id = %s",
                (user_id, food_id)
            )
            cursor.execute(
                "UPDATE foods SET like_count = like_count - 1 WHERE id = %s",
                (food_id,)
            )
            liked = False
        else:
            cursor.execute(
                "INSERT INTO likes (user_id, food_id) VALUES (%s, %s)",
                (user_id, food_id)
            )
            cursor.execute(
                "UPDATE foods SET like_count = like_count + 1 WHERE id = %s",
                (food_id,)
            )
            liked = True

        conn.commit()
        return jsonify({'success': True, 'liked': liked})

    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ========== 切换收藏 ==========
@food_bp.route('/food/<int:food_id>/favorite', methods=['POST'])
def toggle_favorite(food_id):
    """收藏/取消收藏"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT id FROM favorites WHERE user_id = %s AND food_id = %s",
            (user_id, food_id)
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                "DELETE FROM favorites WHERE user_id = %s AND food_id = %s",
                (user_id, food_id)
            )
            favorited = False
        else:
            cursor.execute(
                "INSERT INTO favorites (user_id, food_id) VALUES (%s, %s)",
                (user_id, food_id)
            )
            favorited = True

        conn.commit()
        return jsonify({'success': True, 'favorited': favorited})

    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ========== 获取评论列表 ==========
@food_bp.route('/food/<int:food_id>/comments')
def get_comments(food_id):
    """获取某美食的评论列表（AJAX）"""
    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    cursor.execute("""
        SELECT c.*, u.username
        FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.food_id = %s
        ORDER BY c.created_at DESC
        LIMIT 20
    """, (food_id,))
    comments = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify({'success': True, 'comments': comments})


# ========== 发表评论 ==========
@food_bp.route('/food/<int:food_id>/comment', methods=['POST'])
def add_comment(food_id):
    """发表评论"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    content = request.form.get('content', '').strip()
    if not content:
        return jsonify({'success': False, 'message': '评论内容不能为空'}), 400

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO comments (user_id, food_id, content)
            VALUES (%s, %s, %s)
        """, (user_id, food_id, content))
        conn.commit()
        return jsonify({'success': True, 'message': '评论成功'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ========== 浏览记录页面 ==========
@food_bp.route('/history')
def history():
    """查看当前用户的浏览记录"""
    if 'user_id' not in session:
        flash('❌ 请先登录')
        return redirect(url_for('auth.login_page'))

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    cursor.execute("""
        SELECT h.*, f.name, f.price, f.avg_rating, f.image_url, 
               u.username as merchant_name
        FROM histories h
        JOIN foods f ON h.food_id = f.id
        LEFT JOIN users u ON f.merchant_id = u.id
        WHERE h.user_id = %s
        ORDER BY h.last_view_time DESC
    """, (user_id,))

    histories = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('history.html', histories=histories)


# ========== 清空所有浏览记录 ==========
@food_bp.route('/history/clear', methods=['POST'])
def clear_history():
    """清空当前用户的所有浏览记录"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("DELETE FROM histories WHERE user_id = %s", (user_id,))
        conn.commit()
        return jsonify({'success': True, 'message': '已清空所有浏览记录'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ========== 删除单条浏览记录 ==========
@food_bp.route('/history/delete/<int:history_id>', methods=['POST'])
def delete_history(history_id):
    """删除单条浏览记录"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "DELETE FROM histories WHERE id = %s AND user_id = %s",
            (history_id, user_id)
        )
        conn.commit()

        if cursor.rowcount == 0:
            return jsonify({'success': False, 'message': '记录不存在'}), 404

        return jsonify({'success': True, 'message': '删除成功'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ========== 批量删除浏览记录 ==========
@food_bp.route('/history/batch_delete', methods=['POST'])
def batch_delete_history():
    """批量删除浏览记录"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    data = request.get_json()
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'success': False, 'message': '请选择要删除的记录'}), 400

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        placeholders = ','.join(['%s'] * len(ids))
        sql = f"DELETE FROM histories WHERE id IN ({placeholders}) AND user_id = %s"
        params = ids + [user_id]
        cursor.execute(sql, params)
        conn.commit()

        return jsonify({'success': True, 'message': f'已删除 {cursor.rowcount} 条记录'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ========== 举报美食 ==========
@food_bp.route('/food/<int:food_id>/report', methods=['POST'])
def report_food(food_id):
    """举报美食"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    reason = request.form.get('reason', '').strip()
    if not reason:
        return jsonify({'success': False, 'message': '举报原因不能为空'}), 400

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT id FROM reports WHERE reporter_id = %s AND food_id = %s AND status = 'pending'",
            (user_id, food_id)
        )
        if cursor.fetchone():
            return jsonify({'success': False, 'message': '您已举报过该美食，正在处理中'})

        cursor.execute("""
            INSERT INTO reports (reporter_id, food_id, reason, status)
            VALUES (%s, %s, %s, 'pending')
        """, (user_id, food_id, reason))
        conn.commit()
        return jsonify({'success': True, 'message': '举报已提交'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ========== 举报评论 ==========
@food_bp.route('/food/comment/<int:comment_id>/report', methods=['POST'])
def report_comment(comment_id):
    """举报评论"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    reason = request.form.get('reason', '').strip()
    if not reason:
        return jsonify({'success': False, 'message': '举报原因不能为空'}), 400

    user_id = session['user_id']

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT id FROM reports WHERE reporter_id = %s AND comment_id = %s AND status = 'pending'",
            (user_id, comment_id)
        )
        if cursor.fetchone():
            return jsonify({'success': False, 'message': '您已举报过该评论，正在处理中'})

        cursor.execute("""
            INSERT INTO reports (reporter_id, comment_id, reason, status)
            VALUES (%s, %s, %s, 'pending')
        """, (user_id, comment_id, reason))
        conn.commit()
        return jsonify({'success': True, 'message': '举报已提交'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ========== 我的举报 ==========
@food_bp.route('/my_reports')
def my_reports():
    """查看当前用户提交的举报及处理进度"""
    if 'user_id' not in session:
        flash('❌ 请先登录')
        return redirect(url_for('auth.login_page'))

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    cursor.execute("""
        SELECT r.id, r.reason, r.status, r.created_at, r.resolved_at, r.handle_note,
               f.id AS food_id,
               f.name AS food_name,
               c.content AS comment_content,
               cf.id AS comment_food_id,
               cf.name AS comment_food_name
        FROM reports r
        LEFT JOIN foods f ON r.food_id = f.id
        LEFT JOIN comments c ON r.comment_id = c.id
        LEFT JOIN foods cf ON c.food_id = cf.id
        WHERE r.reporter_id = %s
        ORDER BY r.created_at DESC
    """, (user_id,))
    reports = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('my_reports.html', reports=reports)