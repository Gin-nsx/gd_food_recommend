# generate_mock_data.py
# 生成可复现的模拟数据集：批量用户 + 用户-美食评分
#
# 为什么要造数据：
#   真实评分只有十几条，留出法切完几乎没有可预测样本，
#   评估指标（MAE / Precision 等）都会退化成 0，无法支撑论文实验。
#
# 评分模型（隐因子 + 偏置，接近真实评分分布）：
#   score = 全局均值 + 用户偏置 + 菜品质量 + 口味偏好 + 噪声
#   其中口味偏好按分类生效，模拟「某人偏爱糖水、某人偏爱烧腊」的行为。
#
# 用法:
#   python generate_mock_data.py                 # 默认 200 用户 / 5000 条评分
#   python generate_mock_data.py --users 300 --ratings 8000 --seed 7
#   python generate_mock_data.py --purge         # 清除所有模拟用户及其评分
import argparse
import random
import sys
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from utils.db import get_db_connection

MOCK_PREFIX = 'mock_'
DEFAULT_PASSWORD = '123456'

ALLERGY_POOL = ['花生', '虾', '蟹', '牛奶', '鸡蛋', '大豆']
CITY_POOL = ['广州', '深圳', '佛山', '东莞', '珠海', '中山', '汕头', '潮州',
             '梅州', '惠州', '江门', '肇庆', '清远', '湛江', '韶关']


def purge(cur):
    """删除所有模拟用户，连带其评分、点赞、收藏、历史、黑名单、评论"""
    cur.execute("SELECT id FROM users WHERE username LIKE %s", (MOCK_PREFIX + '%',))
    ids = [r[0] for r in cur.fetchall()]
    if not ids:
        return 0

    placeholders = ','.join(['%s'] * len(ids))
    for table in ('ratings', 'likes', 'favorites', 'histories', 'blacklists', 'comments'):
        cur.execute(f"DELETE FROM {table} WHERE user_id IN ({placeholders})", ids)
    cur.execute(f"DELETE FROM users WHERE id IN ({placeholders})", ids)
    return len(ids)


def load_items(cur):
    """读取上架菜品及其分类"""
    cur.execute("""
        SELECT f.id, f.category_id, c.name
        FROM foods f LEFT JOIN categories c ON f.category_id = c.id
        WHERE f.status = 'approved'
    """)
    return [{'id': fid, 'cat': cat_name or '未分类'} for fid, cid, cat_name in cur.fetchall()]


def create_users(cur, count, rng):
    pwd_hash = generate_password_hash(DEFAULT_PASSWORD)  # 只算一次，200 个用户复用
    users = []

    for i in range(1, count + 1):
        username = f'{MOCK_PREFIX}{i:03d}'
        age_city = rng.choice(CITY_POOL)
        allergy = rng.choice(ALLERGY_POOL) if rng.random() < 0.25 else None
        taboo = rng.choice(['香菜', '内脏', '辣']) if rng.random() < 0.15 else None

        cur.execute(
            """INSERT INTO users (username, password, role, email, phone,
                                  merchant_status, is_active, allergy, taboo, created_at)
               VALUES (%s, %s, 'user', %s, %s, 'pending', 1, %s, %s, %s)""",
            (username, pwd_hash,
             f'{username}@example.com',
             '13%09d' % rng.randint(0, 999999999),
             allergy, taboo,
             datetime.now() - timedelta(days=rng.randint(1, 400)))
        )
        users.append(cur.lastrowid)

    return users


def build_item_quality(items, rng):
    """菜品质量先验：热门菜普遍分高，个别踩雷"""
    quality = {}
    for it in items:
        quality[it['id']] = rng.gauss(0, 0.55)
    return quality


def generate_ratings(cur, user_ids, items, total_ratings, rng):
    """按用户口味偏好采样菜品并打分"""
    categories = sorted({it['cat'] for it in items})
    items_by_cat = {c: [it['id'] for it in items if it['cat'] == c] for c in categories}
    cat_of_item = {it['id']: it['cat'] for it in items}

    quality = build_item_quality(items, rng)
    global_mean = 3.9

    per_user = max(3, round(total_ratings / len(user_ids)))
    rows = []
    seen = set()

    for uid in user_ids:
        # 每个人挑 1~3 个偏好分类，权重明显高于其他分类
        liked = rng.sample(categories, k=rng.randint(1, 3))
        affinity = {c: rng.gauss(0.5, 0.25) for c in liked}
        for c in categories:
            affinity.setdefault(c, rng.gauss(-0.35, 0.3))

        user_bias = rng.gauss(0, 0.45)
        # 打分宽严：有人几乎不打 5 分
        user_scale = rng.uniform(0.75, 1.15)

        n_target = min(len(items), max(3, int(rng.gauss(per_user, per_user * 0.25))))
        picked = []

        # 60% 从偏好分类里抽，40% 全目录随机（保证冷门菜也有曝光）
        while len(picked) < n_target:
            if rng.random() < 0.6 and liked:
                c = rng.choice(liked)
                cand = items_by_cat[c]
            else:
                c = rng.choice(categories)
                cand = items_by_cat[c]
            if not cand:
                break
            fid = rng.choice(cand)
            if fid not in picked:
                picked.append(fid)

        for fid in picked:
            cat = cat_of_item[fid]
            raw = global_mean + user_bias + quality[fid] + affinity[cat] + rng.gauss(0, 0.45)
            score = round(global_mean + (raw - global_mean) * user_scale)
            score = max(1, min(5, int(score)))

            if (uid, fid) in seen:
                continue
            seen.add((uid, fid))

            rows.append((uid, fid, score,
                         datetime.now() - timedelta(days=rng.randint(0, 300),
                                                    minutes=rng.randint(0, 1440))))

    cur.executemany(
        "INSERT INTO ratings (user_id, food_id, score, created_at) VALUES (%s, %s, %s, %s)",
        rows
    )
    return len(rows)


def recompute_avg(cur):
    cur.execute("""
        UPDATE foods f
        LEFT JOIN (SELECT food_id, ROUND(AVG(score), 1) AS s FROM ratings GROUP BY food_id) r
               ON f.id = r.food_id
        SET f.avg_rating = COALESCE(r.s, 0)
    """)
    # 点赞数只认 likes 表：这里不能拿评分条数顶替，
    # 否则「X 人点赞」会等于评分数，而用户的点赞状态又是未点赞，前后矛盾。
    cur.execute("""
        UPDATE foods f
        LEFT JOIN (SELECT food_id, COUNT(*) AS c FROM likes GROUP BY food_id) l
               ON f.id = l.food_id
        SET f.like_count = COALESCE(l.c, 0)
    """)


def main():
    parser = argparse.ArgumentParser(description='生成模拟用户与评分数据')
    parser.add_argument('--users', type=int, default=200, help='模拟用户数量')
    parser.add_argument('--ratings', type=int, default=5000, help='评分总条数')
    parser.add_argument('--seed', type=int, default=42, help='随机种子（保证可复现）')
    parser.add_argument('--purge', action='store_true', help='清除全部模拟数据后退出')
    args = parser.parse_args()

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if args.purge:
            n = purge(cur)
            recompute_avg(cur)
            conn.commit()
            print(f'已清除 {n} 个模拟用户及其评分')
            return

        deleted = purge(cur)  # 重复执行时先清空，保证结果可复现
        if deleted:
            print(f'清理旧模拟数据 {deleted} 个用户')

        rng = random.Random(args.seed)

        cur.execute("SELECT COUNT(*) FROM foods WHERE status = 'approved'")
        if cur.fetchone()[0] < 20:
            print('菜品不足 20 道，先运行 seed_catalog.py 扩充目录')
            sys.exit(1)

        items = load_items(cur)
        user_ids = create_users(cur, args.users, rng)
        n_ratings = generate_ratings(cur, user_ids, items, args.ratings, rng)

        recompute_avg(cur)
        conn.commit()

        cur.execute("SELECT AVG(score) FROM ratings")
        avg = cur.fetchone()[0]
        density = n_ratings / (len(user_ids) * len(items)) * 100

        print(f'模拟用户: {len(user_ids)} 个')
        print(f'评分条数: {n_ratings} 条（矩阵密度 {density:.1f}%）')
        print(f'评分均值: {float(avg):.2f}')
        print(f'菜品数量: {len(items)} 道')
        print('提示：再执行 seed_behavior.py（点赞/收藏/浏览）与 seed_comments.py（评论），'
              '行为数据才会与评分自洽')

    except Exception as e:
        conn.rollback()
        print('失败，已回滚:', type(e).__name__, e)
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
