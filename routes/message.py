# 站内消息：管理员与商家 / 用户之间的沟通渠道
# 场景：管理员处理举报时，需要向商家核实情况、向举报人说明处理结果；
#      对方也能在本页回复，形成可追溯的对话记录。
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, jsonify,
)

import pymysql

from utils.db import get_db_connection

message_bp = Blueprint('message', __name__)

MAX_CONTENT_LEN = 500


def _need_login():
    if 'user_id' not in session:
        flash('❌ 请先登录')
        return redirect(url_for('auth.login_page', next=request.path))
    return None


def _safe_next(target, fallback):
    """只允许跳回站内路径，避免被当成开放重定向"""
    if target and target.startswith('/') and not target.startswith('//'):
        return target
    return fallback


def _has_conversation(cursor, a, b):
    """两人之间是否已有消息往来（任一方向）"""
    cursor.execute("""
        SELECT 1 FROM messages
        WHERE (sender_id = %s AND receiver_id = %s)
           OR (sender_id = %s AND receiver_id = %s)
        LIMIT 1
    """, (a, b, b, a))
    return cursor.fetchone() is not None


# ========== 消息中心 ==========
@message_bp.route('/messages')
def inbox():
    """收件箱 / 已发出"""
    resp = _need_login()
    if resp:
        return resp

    user_id = session['user_id']
    tab = request.args.get('tab', 'in')
    if tab not in ('in', 'out'):
        tab = 'in'

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    if tab == 'in':
        cursor.execute("""
            SELECT m.*, u.username AS peer_name, u.role AS peer_role
            FROM messages m
            JOIN users u ON m.sender_id = u.id
            WHERE m.receiver_id = %s
            ORDER BY m.created_at DESC
            LIMIT 100
        """, (user_id,))
    else:
        cursor.execute("""
            SELECT m.*, u.username AS peer_name, u.role AS peer_role
            FROM messages m
            JOIN users u ON m.receiver_id = u.id
            WHERE m.sender_id = %s
            ORDER BY m.created_at DESC
            LIMIT 100
        """, (user_id,))

    messages = cursor.fetchall()

    cursor.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE receiver_id = %s AND is_read = 0",
        (user_id,)
    )
    unread = cursor.fetchone()['c']

    # 打开收件箱即视为已读（和邮件客户端一致）
    if tab == 'in' and unread:
        cursor.execute(
            "UPDATE messages SET is_read = 1 WHERE receiver_id = %s AND is_read = 0",
            (user_id,)
        )
        conn.commit()

    cursor.close()
    conn.close()

    return render_template('messages.html',
                           messages=messages,
                           tab=tab,
                           unread=unread)


# ========== 发送消息 ==========
@message_bp.route('/messages/send', methods=['POST'])
def send():
    """发送站内消息；管理员可发给任何人，普通用户只能回复有过往来的人"""
    resp = _need_login()
    if resp:
        return resp

    sender_id = session['user_id']
    role = session.get('role')

    try:
        receiver_id = int(request.form.get('receiver_id') or 0)
    except ValueError:
        receiver_id = 0

    content = (request.form.get('content') or '').strip()
    report_id = request.form.get('related_report_id') or None
    next_url = _safe_next(request.form.get('next'), url_for('message.inbox'))
    if not receiver_id or receiver_id == sender_id:
        flash('❌ 收件人不正确')
        return redirect(next_url)
    if not content:
        flash('❌ 消息内容不能为空')
        return redirect(next_url)
    if len(content) > MAX_CONTENT_LEN:
        flash(f'❌ 消息最多 {MAX_CONTENT_LEN} 个字')
        return redirect(next_url)

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("SELECT id, role, is_active FROM users WHERE id = %s",
                       (receiver_id,))
        receiver = cursor.fetchone()
        if not receiver or not receiver['is_active']:
            flash('❌ 收件人不存在或已被禁用')
            return redirect(next_url)

        # 关联举报编号无效时降级为"不关联"，避免整条消息因为外键失败而发不出去
        if report_id:
            try:
                report_id = int(report_id)
            except (TypeError, ValueError):
                report_id = None
            if report_id:
                cursor.execute("SELECT id FROM reports WHERE id = %s", (report_id,))
                if not cursor.fetchone():
                    report_id = None

        # 非管理员：只能回复与自己有过消息往来的人，避免变成骚扰通道
        if role != 'admin' and not _has_conversation(cursor, sender_id, receiver_id):
            flash('❌ 只能回复与你有过消息往来的对象')
            return redirect(next_url)

        cursor.execute("""
            INSERT INTO messages (sender_id, receiver_id, content, related_report_id)
            VALUES (%s, %s, %s, %s)
        """, (sender_id, receiver_id, content, report_id))
        conn.commit()
        flash('✅ 消息已发送')
    except Exception:
        conn.rollback()
        flash('❌ 发送失败，请稍后重试')
    finally:
        cursor.close()
        conn.close()

    return redirect(next_url)


# ========== 未读数（导航栏红点用） ==========
def unread_count(user_id):
    """给模板上下文用：当前用户未读消息数"""
    if not user_id:
        return 0
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM messages WHERE receiver_id = %s AND is_read = 0",
            (user_id,)
        )
        return int(cursor.fetchone()[0])
    except Exception:
        return 0
    finally:
        cursor.close()
        conn.close()
