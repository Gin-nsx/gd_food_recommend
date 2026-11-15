# 增量迁移脚本（可重复执行）：为「饮食偏好隐藏」与「站内消息」补充结构
# 用法：python migrate_v2.py
import pymysql

from config import DB_CONFIG


def column_exists(cursor, table, column):
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
    """, (DB_CONFIG['database'], table, column))
    return cursor.fetchone()[0] > 0


def table_exists(cursor, table):
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
    """, (DB_CONFIG['database'], table))
    return cursor.fetchone()[0] > 0


MESSAGES_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '消息ID',
    sender_id INT NOT NULL COMMENT '发送者ID',
    receiver_id INT NOT NULL COMMENT '接收者ID',
    content VARCHAR(1000) NOT NULL COMMENT '消息正文',
    related_report_id INT COMMENT '关联举报ID（管理员从举报页发起时记录）',
    is_read TINYINT(1) NOT NULL DEFAULT 0 COMMENT '接收方是否已读',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '发送时间',
    KEY idx_messages_receiver (receiver_id, is_read),
    KEY idx_messages_sender (sender_id),
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (receiver_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (related_report_id) REFERENCES reports(id) ON DELETE SET NULL
) COMMENT='站内消息表'
"""


def migrate():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        # 1. users.hide_risk：个人中心「隐藏含过敏/忌口的美食」开关
        if not column_exists(cursor, 'users', 'hide_risk'):
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN hide_risk TINYINT(1) NOT NULL DEFAULT 0
                COMMENT '是否在首页/推荐隐藏含过敏或忌口的美食'
                AFTER allergy
            """)
            print("✅ users.hide_risk 已添加")
        else:
            print("• users.hide_risk 已存在，跳过")

        # 2. messages 站内消息表
        if not table_exists(cursor, 'messages'):
            cursor.execute(MESSAGES_SQL)
            print("✅ messages 表已创建")
        else:
            print("• messages 表已存在，跳过")

        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"❌ 迁移失败: {exc}")
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    migrate()
