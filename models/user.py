# 用户数据操作
import pymysql
from utils.db import get_db_connection
from werkzeug.security import generate_password_hash, check_password_hash


def find_user_by_username(username):
    """根据用户名查找用户"""
    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    return user


def create_user(username, password, role='user', merchant_address=None):
    """创建新用户（普通用户/商家/管理员）"""
    conn = get_db_connection()
    cursor = conn.cursor()

    hashed_pw = generate_password_hash(password)

    if role == 'merchant':
        # 商家需要审核，状态为 pending
        sql = """
            INSERT INTO users (username, password, role, merchant_address, merchant_status, is_active)
            VALUES (%s, %s, %s, %s, 'pending', TRUE)
        """
        cursor.execute(sql, (username, hashed_pw, role, merchant_address))
    else:
        sql = """
            INSERT INTO users (username, password, role, is_active)
            VALUES (%s, %s, %s, TRUE)
        """
        cursor.execute(sql, (username, hashed_pw, role))

    conn.commit()
    user_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return user_id


def check_password(user, raw_password):
    """验证密码是否正确"""
    return check_password_hash(user['password'], raw_password)