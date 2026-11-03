from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
import pymysql
from utils.db import get_db_connection

admin_bp = Blueprint('admin', __name__)


# ========== 管理员首页 ==========
@admin_bp.route('/admin/dashboard')
def admin_dashboard():
    """管理员数据看板"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 请先登录管理员账号')
        return redirect(url_for('auth.login_page'))

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    cursor.execute("SELECT COUNT(*) as total FROM users")
    total_users = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) as total FROM foods")
    total_foods = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) as total FROM users WHERE role='merchant' AND merchant_status='pending'")
    pending_merchants = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) as total FROM foods WHERE status='pending'")
    pending_foods = cursor.fetchone()['total']

    cursor.execute("""
        SELECT * FROM users WHERE role='merchant' AND merchant_status='pending'
    """)
    merchants = cursor.fetchall()

    cursor.execute("""
        SELECT f.*, u.username as merchant_name FROM foods f
        JOIN users u ON f.merchant_id = u.id
        WHERE f.status = 'pending'
    """)
    foods = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('admin_dashboard.html',
                           total_users=total_users,
                           total_foods=total_foods,
                           pending_merchants=pending_merchants,
                           pending_foods=pending_foods,
                           merchants=merchants,
                           foods=foods)


# ========== 审核商家 ==========
@admin_bp.route('/admin/merchant/approve/<int:user_id>')
def approve_merchant(user_id):
    """通过商家审核"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 无权操作')
        return redirect(url_for('auth.login_page'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE users SET merchant_status='approved' WHERE id=%s AND role='merchant'
        """, (user_id,))
        conn.commit()
        flash('✅ 商家审核通过')
    except Exception as e:
        flash(f'❌ 操作失败：{e}')
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin.admin_dashboard'))


# ========== 拒绝商家 ==========
@admin_bp.route('/admin/merchant/reject/<int:user_id>')
def reject_merchant(user_id):
    """拒绝商家审核"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 无权操作')
        return redirect(url_for('auth.login_page'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE users SET merchant_status='rejected' WHERE id=%s AND role='merchant'
        """, (user_id,))
        conn.commit()
        flash('✅ 已拒绝该商家申请')
    except Exception as e:
        flash(f'❌ 操作失败：{e}')
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin.admin_dashboard'))


# ========== 审核美食 ==========
@admin_bp.route('/admin/food/approve/<int:food_id>')
def approve_food(food_id):
    """通过美食审核"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 无权操作')
        return redirect(url_for('auth.login_page'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE foods SET status='approved' WHERE id=%s", (food_id,))
        conn.commit()
        flash('✅ 美食审核通过')
    except Exception as e:
        flash(f'❌ 操作失败：{e}')
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin.admin_dashboard'))


# ========== 拒绝美食 ==========
@admin_bp.route('/admin/food/reject/<int:food_id>')
def reject_food(food_id):
    """拒绝美食审核"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 无权操作')
        return redirect(url_for('auth.login_page'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE foods SET status='rejected' WHERE id=%s", (food_id,))
        conn.commit()
        flash('✅ 已拒绝该美食')
    except Exception as e:
        flash(f'❌ 操作失败：{e}')
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin.admin_dashboard'))


# ========== 用户管理 ==========
@admin_bp.route('/admin/users')
def manage_users():
    """管理所有用户"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 无权操作')
        return redirect(url_for('auth.login_page'))

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("""
        SELECT id, username, role, is_active, merchant_status, created_at FROM users
        ORDER BY created_at DESC
    """)
    users = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('admin_users.html', users=users)


# ========== 禁用/启用用户 ==========
@admin_bp.route('/admin/user/toggle/<int:user_id>')
def toggle_user(user_id):
    """禁用或启用用户"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 无权操作')
        return redirect(url_for('auth.login_page'))

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("SELECT is_active FROM users WHERE id=%s", (user_id,))
    user = cursor.fetchone()
    if not user:
        flash('❌ 用户不存在')
        cursor.close()
        conn.close()
        return redirect(url_for('admin.manage_users'))

    new_status = not user['is_active']
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE users SET is_active=%s WHERE id=%s", (new_status, user_id))
        conn.commit()
        flash(f'✅ 用户状态已{"启用" if new_status else "禁用"}')
    except Exception as e:
        flash(f'❌ 操作失败：{e}')
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin.manage_users'))


# ========== 图表统计接口 ==========
@admin_bp.route('/admin/stats/category')
def stats_by_category():
    """按分类统计美食数量（饼图用）"""
    if session.get('role') != 'admin':
        return jsonify({'error': '无权访问'}), 403

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.name, COUNT(f.id) AS cnt
        FROM categories c
        LEFT JOIN foods f ON f.category_id = c.id AND f.status = 'approved'
        GROUP BY c.id, c.name
        HAVING cnt > 0
        ORDER BY cnt DESC
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify([{'name': r[0], 'value': r[1]} for r in rows])


@admin_bp.route('/admin/stats/rating_dist')
def stats_rating_dist():
    """评分分布统计（柱状图用）"""
    if session.get('role') != 'admin':
        return jsonify({'error': '无权访问'}), 403

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT score, COUNT(*) AS cnt
        FROM ratings
        GROUP BY score
        ORDER BY score
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    data = {i: 0 for i in range(1, 6)}
    for score, cnt in rows:
        data[score] = cnt

    return jsonify({
        'labels': ['1星', '2星', '3星', '4星', '5星'],
        'values': [data[i] for i in range(1, 6)]
    })


@admin_bp.route('/admin/stats/user_growth')
def stats_user_growth():
    """用户注册趋势（折线图用）"""
    if session.get('role') != 'admin':
        return jsonify({'error': '无权访问'}), 403

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DATE(created_at) AS d, COUNT(*) AS cnt
        FROM users
        WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
        GROUP BY DATE(created_at)
        ORDER BY d
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify({
        'labels': [str(r[0]) for r in rows],
        'values': [r[1] for r in rows]
    })


# ========== 举报列表页面 ==========
@admin_bp.route('/admin/reports')
def admin_reports():
    """管理员查看所有举报"""
    if session.get('role') != 'admin':
        flash('❌ 请先登录管理员账号')
        return redirect(url_for('auth.login_page'))

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 美食举报：带上 food_id、商家名、商家ID
    cursor.execute("""
        SELECT r.id, r.reason, r.status, r.created_at, r.handle_note, r.resolved_at,
               u.username AS reporter_name,
               f.id AS food_id,
               f.name AS food_name,
               m.id AS merchant_id,
               COALESCE(m.merchant_name, m.username) AS merchant_name,
               NULL AS comment_content,
               NULL AS comment_food_id,
               NULL AS comment_food_name
        FROM reports r
        JOIN users u ON r.reporter_id = u.id
        LEFT JOIN foods f ON r.food_id = f.id
        LEFT JOIN users m ON f.merchant_id = m.id
        WHERE r.food_id IS NOT NULL
        ORDER BY r.created_at DESC
    """)
    food_reports = cursor.fetchall()

    # 评论举报：带上评论内容 + 所属美食 ID/名称
    cursor.execute("""
        SELECT r.id, r.reason, r.status, r.created_at, r.handle_note, r.resolved_at,
               u.username AS reporter_name,
               NULL AS food_id,
               NULL AS food_name,
               NULL AS merchant_id,
               NULL AS merchant_name,
               c.content AS comment_content,
               cf.id AS comment_food_id,
               cf.name AS comment_food_name
        FROM reports r
        JOIN users u ON r.reporter_id = u.id
        LEFT JOIN comments c ON r.comment_id = c.id
        LEFT JOIN foods cf ON c.food_id = cf.id
        WHERE r.comment_id IS NOT NULL
        ORDER BY r.created_at DESC
    """)
    comment_reports = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('admin_reports.html',
                           food_reports=food_reports,
                           comment_reports=comment_reports)


# ========== 处理举报（POST） ==========
@admin_bp.route('/admin/report/<int:report_id>/handle', methods=['POST'])
def handle_report(report_id):
    """处理举报：提交处理结果"""
    if session.get('role') != 'admin':
        flash('❌ 请先登录管理员账号')
        return redirect(url_for('auth.login_page'))

    action = request.form.get('action')
    note = request.form.get('handle_note', '').strip()

    if action not in ('resolved', 'rejected'):
        flash('❌ 无效操作')
        return redirect(url_for('admin.admin_reports'))

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE reports
            SET status = %s, handle_note = %s, resolved_at = NOW()
            WHERE id = %s
        """, (action, note, report_id))
        conn.commit()

        action_text = '已处理' if action == 'resolved' else '已驳回'
        flash(f'✅ 举报{action_text}')
    except Exception as e:
        conn.rollback()
        flash(f'❌ 操作失败：{e}')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin.admin_reports'))