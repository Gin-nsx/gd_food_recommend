# 增量迁移脚本（可重复执行）：为「首页每日热榜」补充浏览量明细
# 用法：python migrate_v3.py
#
# 背景：
#   histories 表是「每个用户对每道菜只留一条」的浏览记录（unique user_id+food_id），
#   只能回答「有多少人看过」，回答不了「今天被看了多少次」。
#   每日热榜需要真正的 PV 口径，所以单开一张明细表 food_views，一次访问一条。
import pymysql

from config import DB_CONFIG


FOOD_VIEWS_SQL = """
CREATE TABLE IF NOT EXISTS food_views (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '浏览记录ID',
    food_id INT NOT NULL COMMENT '美食ID',
    user_id INT COMMENT '用户ID（未登录为空）',
    visitor_key VARCHAR(64) COMMENT '匿名访客标识（session 摘要，仅用于去重）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '浏览时间',
    KEY idx_food_views_time (created_at),
    KEY idx_food_views_food_time (food_id, created_at),
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
) COMMENT='美食浏览明细表（PV 口径，供每日热榜统计）'
"""


def table_exists(cursor, table):
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
    """, (DB_CONFIG['database'], table))
    return cursor.fetchone()[0] > 0


def migrate():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        # 1. food_views 明细表
        if not table_exists(cursor, 'food_views'):
            cursor.execute(FOOD_VIEWS_SQL)
            print("✅ food_views 表已创建")
        else:
            print("• food_views 表已存在，跳过")

        # 2. 用 histories 回填历史浏览，让热榜上线第一天就有数据可算
        #    只在表为空时执行，避免重复回填把 PV 翻倍
        cursor.execute("SELECT COUNT(*) FROM food_views")
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                INSERT INTO food_views (food_id, user_id, created_at)
                SELECT food_id, user_id, last_view_time
                FROM histories
                WHERE last_view_time IS NOT NULL
            """)
            print(f"✅ 已从 histories 回填 {cursor.rowcount} 条历史浏览")
        else:
            print("• food_views 已有数据，跳过回填")

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
