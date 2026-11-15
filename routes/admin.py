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
        return redirect(url_for('auth.login_page', next=request.path))

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

    cursor.execute("SELECT COUNT(*) as total FROM reports WHERE status='pending'")
    pending_reports = cursor.fetchone()['total']

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
                           pending_reports=pending_reports,
                           merchants=merchants,
                           foods=foods)


# ========== 审核商家 ==========
@admin_bp.route('/admin/merchant/approve/<int:user_id>', methods=['POST'])
def approve_merchant(user_id):
    """通过商家审核"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 无权操作')
        return redirect(url_for('auth.login_page', next=request.path))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE users SET merchant_status='approved' WHERE id=%s AND role='merchant'
        """, (user_id,))
        conn.commit()
        flash('✅ 商家审核通过')
    except Exception as e:
        flash('❌ 操作失败，请稍后重试')
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin.admin_dashboard'))


# ========== 拒绝商家 ==========
@admin_bp.route('/admin/merchant/reject/<int:user_id>', methods=['POST'])
def reject_merchant(user_id):
    """拒绝商家审核"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 无权操作')
        return redirect(url_for('auth.login_page', next=request.path))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE users SET merchant_status='rejected' WHERE id=%s AND role='merchant'
        """, (user_id,))
        conn.commit()
        flash('✅ 已拒绝该商家申请')
    except Exception as e:
        flash('❌ 操作失败，请稍后重试')
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin.admin_dashboard'))


# ========== 审核美食 ==========
@admin_bp.route('/admin/food/approve/<int:food_id>', methods=['POST'])
def approve_food(food_id):
    """通过美食审核"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 无权操作')
        return redirect(url_for('auth.login_page', next=request.path))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE foods SET status='approved' WHERE id=%s", (food_id,))
        conn.commit()
        flash('✅ 美食审核通过')
    except Exception as e:
        flash('❌ 操作失败，请稍后重试')
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin.admin_dashboard'))


# ========== 拒绝美食 ==========
@admin_bp.route('/admin/food/reject/<int:food_id>', methods=['POST'])
def reject_food(food_id):
    """拒绝美食审核"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 无权操作')
        return redirect(url_for('auth.login_page', next=request.path))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE foods SET status='rejected' WHERE id=%s", (food_id,))
        conn.commit()
        flash('✅ 已拒绝该美食')
    except Exception as e:
        flash('❌ 操作失败，请稍后重试')
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin.admin_dashboard'))


# ========== 用户管理 ==========
@admin_bp.route('/admin/users')
def manage_users():
    """管理所有用户（支持关键词搜索、角色筛选、分页）"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 无权操作')
        return redirect(url_for('auth.login_page', next=request.path))

    page = request.args.get('page', default=1, type=int)
    if page < 1:
        page = 1
    per_page = 20

    keyword = request.args.get('q', '').strip()
    role = request.args.get('role', '').strip()

    where = []
    params = []
    if keyword:
        where.append("username LIKE %s")
        params.append(f'%{keyword}%')
    if role in ('user', 'merchant', 'admin'):
        where.append("role = %s")
        params.append(role)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    cursor.execute(f"SELECT COUNT(*) AS total FROM users{where_sql}", params)
    total = cursor.fetchone()['total']
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    cursor.execute(
        f"""SELECT id, username, role, is_active, merchant_status, created_at
            FROM users{where_sql}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s""",
        params + [per_page, (page - 1) * per_page]
    )
    users = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template(
        'admin_users.html',
        users=users,
        page=page,
        total_pages=total_pages,
        total=total,
        keyword=keyword,
        role=role,
        per_page=per_page,
    )


# ========== 禁用/启用用户 ==========
@admin_bp.route('/admin/user/toggle/<int:user_id>', methods=['POST'])
def toggle_user(user_id):
    """禁用或启用用户"""
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('❌ 无权操作')
        return redirect(url_for('auth.login_page', next=request.path))

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
        flash('❌ 操作失败，请稍后重试')
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
        return redirect(url_for('auth.login_page', next=request.path))

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
    """处理举报：提交处理结果，可顺带下架美食 / 删除评论"""
    if session.get('role') != 'admin':
        flash('❌ 请先登录管理员账号')
        return redirect(url_for('auth.login_page', next=request.path))

    action = request.form.get('action')
    note = (request.form.get('handle_note') or '').strip()
    target_action = request.form.get('target_action', 'none')

    if action not in ('resolved', 'rejected'):
        flash('❌ 无效操作')
        return redirect(url_for('admin.admin_reports'))

    if len(note) > 255:
        flash('❌ 处理说明最多 255 个字')
        return redirect(url_for('admin.admin_reports'))

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        cursor.execute(
            "SELECT id, food_id, comment_id, status FROM reports WHERE id = %s",
            (report_id,)
        )
        report = cursor.fetchone()
        if not report:
            flash('❌ 举报记录不存在')
            return redirect(url_for('admin.admin_reports'))
        if report['status'] != 'pending':
            flash('❌ 该举报已处理过')
            return redirect(url_for('admin.admin_reports'))

        cursor.execute("""
            UPDATE reports
            SET status = %s, handle_note = %s, resolved_at = NOW()
            WHERE id = %s
        """, (action, note, report_id))

        # 只有「已处理」才执行内容处置；驳回不动内容
        handled_text = ''
        if action == 'resolved':
            if target_action == 'offline_food' and report['food_id']:
                cursor.execute("UPDATE foods SET status='offline' WHERE id=%s", (report['food_id'],))
                handled_text = '，该美食已下架'
            elif target_action == 'delete_comment' and report['comment_id']:
                cursor.execute("DELETE FROM comments WHERE id=%s", (report['comment_id'],))
                handled_text = '，该评论已删除'

        conn.commit()
        action_text = '已处理' if action == 'resolved' else '已驳回'
        flash(f'✅ 举报{action_text}{handled_text}')
    except Exception:
        conn.rollback()
        flash('❌ 操作失败，请稍后重试')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('admin.admin_reports'))