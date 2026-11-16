# 生成演示用的浏览明细数据（近两周 PV），供首页每日热榜统计
# 用法：python seed_food_views.py [--days 14] [--reset]
#
# 说明：
#   菜品本身只有 74 道，真实浏览记录不足以体现「热门」的区分度，
#   这里按长尾分布（点赞多、评分高的菜更常被点开）生成一批模拟 PV。
#   生成的记录 visitor_key 统一标为 'seed'，用 --reset 可以只清掉这批，不影响真实浏览。
import argparse
import random
from datetime import datetime, timedelta

from utils.db import get_db_connection

SEED_KEY = 'seed'


def load_weighted_foods(cursor):
    """按「点赞数 + 平均分」给菜品分配热度权重，模拟真实的长尾分布"""
    cursor.execute("""
        SELECT id, like_count, avg_rating
        FROM foods
        WHERE status = 'approved'
    """)
    rows = cursor.fetchall()
    ids, weights = [], []
    for food_id, likes, rating in rows:
        base = (int(likes or 0) + 10) ** 1.15
        quality = max(float(rating or 3.5), 2.0) / 5.0
        ids.append(food_id)
        weights.append(base * (quality ** 2.2))
    return ids, weights


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=14, help='生成最近多少天的数据')
    parser.add_argument('--reset', action='store_true', help='先清掉之前生成的模拟数据')
    args = parser.parse_args()

    random.seed(20261116)  # 固定种子，重跑结果一致，方便复现

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if args.reset:
            cursor.execute("DELETE FROM food_views WHERE visitor_key = %s", (SEED_KEY,))
            print(f"已清除 {cursor.rowcount} 条模拟浏览记录")

        ids, weights = load_weighted_foods(cursor)
        if not ids:
            print("没有可用的已上架菜品")
            return

        cursor.execute("SELECT id FROM users WHERE username LIKE 'mock_%'")
        user_ids = [r[0] for r in cursor.fetchall()]

        now = datetime.now()
        batch = []
        total = 0
        for day_offset in range(args.days):
            day = now - timedelta(days=day_offset)
            # 越接近今天，访问量略高，模拟热度爬升
            volume = random.randint(170, 300) + max(0, (7 - day_offset)) * 8
            for _ in range(volume):
                food_id = random.choices(ids, weights=weights, k=1)[0]
                if day_offset == 0:
                    # 今天的记录不能超过当前时刻
                    seconds = random.randint(0, max(60, int((now - now.replace(hour=0, minute=0, second=0)).total_seconds())))
                else:
                    seconds = random.randint(0, 86399)
                ts = (day.replace(hour=0, minute=0, second=0) + timedelta(seconds=seconds))
                if ts > now:
                    ts = now - timedelta(minutes=random.randint(1, 120))
                user_id = random.choice(user_ids) if user_ids else None
                batch.append((food_id, user_id, SEED_KEY, ts))
                total += 1

        cursor.executemany("""
            INSERT INTO food_views (food_id, user_id, visitor_key, created_at)
            VALUES (%s, %s, %s, %s)
        """, batch)
        conn.commit()
        print(f"✅ 已生成 {total} 条模拟浏览，覆盖最近 {args.days} 天")
    except Exception as exc:
        conn.rollback()
        print(f"❌ 生成失败: {exc}")
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    main()
