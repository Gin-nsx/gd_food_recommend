# seed_behavior.py
# 让模拟用户的点赞 / 收藏 / 浏览记录都从「评分」派生，形成自洽的行为链：
#
#   浏览详情 → 评分 → （按分数概率）点赞 / 收藏 → 评论
#
# 背景：
#   之前 likes 表是空的，页面上的「X 人点赞」其实是评分条数（recompute_avg 把
#   like_count 写成了 COUNT(ratings)），而 favorites / histories 是早期手工造的，
#   和评分毫无关联——会出现「给某道菜打了 5 分却从没浏览、没收藏、没点赞」。
#
# 规则：
#   1. 每条评分 → 评分前 0.5~3 小时产生一次浏览（写 histories + food_views）；
#   2. 点赞概率按分数：5★ 75% / 4★ 55% / 3★ 22% / 2★ 6% / 1★ 3%；
#   3. 收藏概率按分数：5★ 42% / 4★ 16% / 3★ 5% / 2★ 1% / 1★ 0%；
#   4. 每人额外浏览 4~8 道没评过的菜（按热度加权，近 60 天），模拟「看了没打分」；
#   5. 最后按 likes 表重算 foods.like_count，不再用评分数顶替点赞数。
#
# 用法:
#   python seed_behavior.py            # 重建全部模拟行为数据
#   python seed_behavior.py --seed 7   # 换一批
import argparse
import random
import sys
from datetime import datetime, timedelta

from utils.db import get_db_connection

MOCK_PREFIX = 'mock_'

LIKE_P = {5: 0.75, 4: 0.55, 3: 0.22, 2: 0.06, 1: 0.03}
FAV_P = {5: 0.42, 4: 0.16, 3: 0.05, 2: 0.01, 1: 0.0}


def load_mock_users(cur):
    cur.execute("SELECT id FROM users WHERE username LIKE %s", (MOCK_PREFIX + '%',))
    return [r[0] for r in cur.fetchall()]


def purge(cur, mock_ids):
    """清掉 mock 用户的行为数据（真实用户与 visitor_key='seed' 的 PV 保留）"""
    ph = ','.join(['%s'] * len(mock_ids))
    counts = {}
    for table in ('likes', 'favorites', 'histories'):
        cur.execute(f"DELETE FROM {table} WHERE user_id IN ({ph})", mock_ids)
        counts[table] = cur.rowcount
    # 只删「有登录用户」的个人浏览明细，保留匿名访客与批量演示 PV
    cur.execute(
        f"DELETE FROM food_views WHERE user_id IN ({ph}) AND visitor_key IS NULL",
        mock_ids,
    )
    counts['food_views'] = cur.rowcount
    return counts


def load_ratings(cur):
    cur.execute("""
        SELECT r.user_id, r.food_id, r.score, r.created_at
        FROM ratings r JOIN users u ON r.user_id = u.id
        WHERE u.username LIKE %s
        ORDER BY r.created_at
    """, (MOCK_PREFIX + '%',))
    return cur.fetchall()


def main():
    parser = argparse.ArgumentParser(description='从评分派生点赞/收藏/浏览，使行为数据自洽')
    parser.add_argument('--seed', type=int, default=2026, help='随机种子（可复现）')
    parser.add_argument('--extra-views', type=int, default=6, help='每人额外浏览未评分菜品的条数基准')
    args = parser.parse_args()

    conn = get_db_connection()
    cur = conn.cursor()
    rng = random.Random(args.seed)

    try:
        mock_ids = load_mock_users(cur)
        if not mock_ids:
            print('没有找到 mock 用户，请先运行 generate_mock_data.py')
            sys.exit(1)

        removed = purge(cur, mock_ids)
        ratings = load_ratings(cur)
        if not ratings:
            print('没有评分数据，请先运行 generate_mock_data.py')
            sys.exit(1)

        now = datetime.now()
        likes, favs, hist_rows, view_rows = [], [], [], []

        for uid, fid, score, rated_at in ratings:
            # 1. 评分前的浏览（半小时到三小时前）
            gap = timedelta(minutes=rng.randint(30, 180))
            viewed_at = rated_at - gap
            view_rows.append((fid, uid, None, viewed_at))
            hist_rows.append((uid, fid, viewed_at))

            # 2. 点赞 / 收藏，动作时间紧跟在浏览之后
            act_at = viewed_at + timedelta(minutes=rng.randint(1, 40))
            if act_at > now:
                act_at = rated_at
            if rng.random() < LIKE_P.get(score, 0.0):
                likes.append((uid, fid, act_at))
            if rng.random() < FAV_P.get(score, 0.0):
                favs.append((uid, fid, act_at))

        # 3. 没评分但有浏览：按菜品热度加权抽，时间落在近 60 天
        rated_pairs = {(uid, fid) for uid, fid, _s, _t in ratings}
        cur.execute("""
            SELECT f.id, COUNT(r.id) AS n
            FROM foods f LEFT JOIN ratings r ON r.food_id = f.id
            WHERE f.status = 'approved'
            GROUP BY f.id
        """)
        pop = cur.fetchall()
        food_ids = [r[0] for r in pop]
        weights = [(r[1] or 0) ** 0.8 + 1 for r in pop]

        per_user = {}
        for uid, fid, _s, _t in ratings:
            per_user.setdefault(uid, set()).add(fid)

        for uid in mock_ids:
            n = max(1, int(rng.gauss(args.extra_views, 2)))
            seen = per_user.get(uid, set())
            for _ in range(n):
                fid = rng.choices(food_ids, weights=weights, k=1)[0]
                if fid in seen:
                    continue
                seen.add(fid)
                v_at = now - timedelta(days=rng.randint(0, 60),
                                       hours=rng.randint(0, 23),
                                       minutes=rng.randint(0, 59))
                view_rows.append((fid, uid, None, v_at))
                hist_rows.append((uid, fid, v_at))

        # 4. 落库
        cur.executemany(
            "INSERT IGNORE INTO likes (user_id, food_id, created_at) VALUES (%s, %s, %s)", likes)
        cur.executemany(
            "INSERT IGNORE INTO favorites (user_id, food_id, created_at) VALUES (%s, %s, %s)", favs)
        cur.executemany("""
            INSERT INTO histories (user_id, food_id, last_view_time) VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE last_view_time = VALUES(last_view_time)
        """, hist_rows)
        cur.executemany(
            "INSERT INTO food_views (food_id, user_id, visitor_key, created_at) VALUES (%s, %s, %s, %s)",
            view_rows)

        # 5. 点赞数按 likes 表重算（不再用评分数顶替）
        cur.execute("""
            UPDATE foods f
            LEFT JOIN (SELECT food_id, COUNT(*) c FROM likes GROUP BY food_id) l
                   ON f.id = l.food_id
            SET f.like_count = COALESCE(l.c, 0)
        """)

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM likes")
        total_likes = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM favorites")
        total_favs = cur.fetchone()[0]
        cur.execute("SELECT MIN(like_count), MAX(like_count), ROUND(AVG(like_count),1) FROM foods WHERE status='approved'")
        lmin, lmax, lavg = cur.fetchone()

        print(f'清理旧数据：点赞 {removed["likes"]} / 收藏 {removed["favorites"]} / '
              f'浏览记录 {removed["histories"]} / 浏览明细 {removed["food_views"]}')
        print(f'生成：点赞 {len(likes)} 条 / 收藏 {len(favs)} 条 / '
              f'浏览记录 {len(hist_rows)} 条 / 浏览明细 {len(view_rows)} 条')
        print(f'表内合计：点赞 {total_likes} / 收藏 {total_favs}')
        print(f'菜品点赞数：最少 {lmin}，最多 {lmax}，平均 {float(lavg):.1f}')

    except Exception as e:
        conn.rollback()
        print('失败，已回滚:', type(e).__name__, e)
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
