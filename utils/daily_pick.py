# utils/daily_pick.py
# 首页「每日热榜」：非个性化，按全站最近行为算当天最热的几道菜
#
# 与「猜你喜欢」的区别：
#   Item-CF 那条链路是 per-user 的，新用户/刚注册用户没有历史，只能退回冷启动兜底；
#   每日热榜是 per-site 的，所有人看到的都一样，口径是「今天大家都在看什么、最近评得好不好」。
#
# 热度打分（论文里可以直接引这一段）：
#   Hot(f) = 0.48·V̂(f) + 0.30·R̂(f) + 0.13·Ĉ(f) + 0.09·F̂(f)
#     V(f) = Σ 2^(-Δh/36)     浏览热度，Δh 为距今小时数，半衰期 36 小时
#     R(f) = (Σ score + μ0·k) / (n + k) / 5   近 7 天评分的贝叶斯收缩，k = 3
#     Ĉ(f) / F̂(f)              近 7 天评论、收藏的衰减和
#     每个分量除以窗口内最大值做归一化，避免量纲不同互相压制
#
# 窗口自适应：
#   先按「今天」统计；如果今天有行为的菜不够铺满榜单，自动放宽到近 3 天、近 7 天，
#   并把实际窗口透出到页面上（今日 / 近三日 / 本周），不会出现「榜单空着」的情况。
import time

import pymysql

from utils.db import get_db_connection

# 浏览/评论/收藏的衰减半衰期（小时）
HALF_LIFE_HOURS = 36.0
# 行为统计的回溯窗口（天）：太短会因为当天样本太少而排名抖动，太长又不够「当天」
STATS_DAYS = 7
# 今日有浏览的菜给一点加权，体现「今天还在热」而不是「上周热过」
TODAY_BOOST = 1.10
# 各分量权重，和恒为 1
WEIGHTS = {'view': 0.48, 'rating': 0.30, 'comment': 0.13, 'favorite': 0.09}
# 近期评分的贝叶斯先验：票数少的高分菜往全站均值收缩，防止「1 条 5 分」直接登顶
RATING_PRIOR_VOTES = 3.0
RATING_PRIOR_MEAN = 3.8
# 口碑门槛：票数够多但均分明显偏低的菜，再热也不进榜
MIN_VOTES_BAD = 5
MIN_AVG_BAD = 3.5
# 首页是高频入口，热榜本身也不该秒级跳动，60 秒内复用上一次结果。
# 只缓存「无排除项」的通用榜单，带黑名单的请求照常实时算。
_CACHE = {'ts': 0.0, 'data': None}
CACHE_TTL = 60


def _decay_expr(column):
    """MySQL 侧的指数衰减：2^(-Δh / 36)"""
    return (f"SUM(POWER(2, -TIMESTAMPDIFF(MINUTE, {column}, NOW()) "
            f"/ ({HALF_LIFE_HOURS:.0f} * 60)))")


def _load_foods(cursor, exclude_ids):
    """已上架菜品的基础信息，含口碑门槛所需的票数与均分"""
    sql = """
        SELECT f.id, f.name, f.price, f.image_url, f.avg_rating, f.like_count,
               f.category_id, f.merchant_id,
               COALESCE(u.merchant_name, u.username) AS merchant_name,
               c.name AS category_name,
               COUNT(r.id) AS rating_count
        FROM foods f
        LEFT JOIN users u ON f.merchant_id = u.id
        LEFT JOIN categories c ON f.category_id = c.id
        LEFT JOIN ratings r ON f.id = r.food_id
        WHERE f.status = 'approved'
    """
    params = []
    if exclude_ids:
        placeholders = ','.join(['%s'] * len(exclude_ids))
        sql += f" AND f.id NOT IN ({placeholders})"
        params.extend(exclude_ids)

    sql += " GROUP BY f.id"
    cursor.execute(sql, params)

    foods = {}
    for row in cursor.fetchall():
        foods[row['id']] = {
            'id': row['id'],
            'name': row['name'],
            'price': float(row['price']) if row['price'] is not None else 0.0,
            'image_url': row['image_url'],
            'avg_rating': float(row['avg_rating']) if row['avg_rating'] else 0.0,
            'like_count': row['like_count'] or 0,
            'category_id': row['category_id'],
            'merchant_id': row['merchant_id'],
            'merchant_name': row['merchant_name'] or '未知商家',
            'category_name': row['category_name'] or '',
            'rating_count': int(row['rating_count'] or 0),
        }
    return foods


def _load_stats(cursor, days=STATS_DAYS):
    """近 N 天的行为聚合：浏览 PV（含今日 PV）、近 7 天评分、评论数、收藏数"""
    stats = {}

    def bucket(food_id):
        return stats.setdefault(food_id, {
            'pv': 0, 'view_w': 0.0, 'today_pv': 0,
            'recent_n': 0, 'recent_sum': 0.0, 'recent_avg': 0.0,
            'comment_n': 0, 'comment_w': 0.0,
            'favorite_n': 0, 'favorite_w': 0.0,
        })

    # 浏览：窗口内总 PV、衰减和、以及「今天」的 PV（用于文案和当天加权）
    cursor.execute(f"""
        SELECT food_id, COUNT(*) AS pv, {_decay_expr('created_at')} AS w,
               SUM(CASE WHEN created_at >= CURDATE() THEN 1 ELSE 0 END) AS today_pv
        FROM food_views
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
        GROUP BY food_id
    """, (days,))
    for row in cursor.fetchall():
        item = bucket(row['food_id'])
        item['pv'] = int(row['pv'])
        item['view_w'] = float(row['w'] or 0)
        item['today_pv'] = int(row['today_pv'] or 0)

    # 近期评分：不衰减，7 天内都算「最近的评价」
    cursor.execute("""
        SELECT food_id, COUNT(*) AS n, SUM(score) AS s, AVG(score) AS a
        FROM ratings
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        GROUP BY food_id
    """)
    for row in cursor.fetchall():
        item = bucket(row['food_id'])
        item['recent_n'] = int(row['n'])
        item['recent_sum'] = float(row['s'] or 0)
        item['recent_avg'] = float(row['a'] or 0)

    # 评论
    cursor.execute(f"""
        SELECT food_id, COUNT(*) AS n, {_decay_expr('created_at')} AS w
        FROM comments
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
        GROUP BY food_id
    """, (days,))
    for row in cursor.fetchall():
        item = bucket(row['food_id'])
        item['comment_n'] = int(row['n'])
        item['comment_w'] = float(row['w'] or 0)

    # 收藏
    cursor.execute(f"""
        SELECT food_id, COUNT(*) AS n, {_decay_expr('created_at')} AS w
        FROM favorites
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
        GROUP BY food_id
    """, (days,))
    for row in cursor.fetchall():
        item = bucket(row['food_id'])
        item['favorite_n'] = int(row['n'])
        item['favorite_w'] = float(row['w'] or 0)

    return stats


def _bayes_recent(recent_sum, recent_n):
    """近 7 天评分的贝叶斯均值（1~5 分）"""
    return (recent_sum + RATING_PRIOR_MEAN * RATING_PRIOR_VOTES) / (recent_n + RATING_PRIOR_VOTES)


def _is_bad(food):
    """票数够、均分却明显偏低 —— 同一个人群反复给低分，是真的不受欢迎"""
    return (food['rating_count'] >= MIN_VOTES_BAD
            and food['avg_rating'] and food['avg_rating'] < MIN_AVG_BAD)


def _build_reason(item):
    """一句话说清「凭什么上今天的榜」：一段热度 + 一段口碑，避免堆砌"""
    parts = []
    if item['today_pv'] > 0:
        parts.append(f"今日 {item['today_pv']} 人看过")
    elif item['pv'] > 0:
        parts.append(f"本周 {item['pv']} 次浏览")

    if item['recent_n'] > 0:
        parts.append(f"近 7 天 {item['recent_n']} 条新评分")
    elif item['rating_count'] >= 3 and item['avg_rating']:
        parts.append(f"{item['rating_count']} 人评出 {item['avg_rating']:.1f} 分")
    elif item['comment_n'] > 0:
        parts.append(f"近 7 天 {item['comment_n']} 条新评论")
    elif item['favorite_n'] > 0:
        parts.append(f"近 7 天 {item['favorite_n']} 次收藏")

    return ' · '.join(parts[:2]) or '口碑稳定的常驻热门'


def _diversify(ranked, limit):
    """同一分类最多 2 道、同一商家最多 1 道，先保证榜单不全是同一家店"""
    picked, picked_ids = [], set()
    cat_count, merchant_count = {}, {}

    def take(strict_merchant):
        for item in ranked:
            if len(picked) >= limit:
                return
            if item['id'] in picked_ids:
                continue
            cat = item['category_id']
            merchant = item['merchant_id']
            if cat_count.get(cat, 0) >= 2:
                continue
            cap = 1 if strict_merchant else 2
            if merchant_count.get(merchant, 0) >= cap:
                continue
            picked.append(item)
            picked_ids.add(item['id'])
            cat_count[cat] = cat_count.get(cat, 0) + 1
            merchant_count[merchant] = merchant_count.get(merchant, 0) + 1

    take(strict_merchant=True)
    if len(picked) < limit:
        take(strict_merchant=False)
    return picked


def _score_window(foods, stats):
    """给每道菜打分并排序，返回 ranked 列表"""
    rows = []
    for food_id, food in foods.items():
        st = stats.get(food_id)
        if not st:
            continue
        # 窗口内完全没有任何行为的菜不进榜
        if st['pv'] == 0 and st['recent_n'] == 0 and st['comment_n'] == 0 and st['favorite_n'] == 0:
            continue
        if _is_bad(food):
            continue
        rows.append((food, st))

    if not rows:
        return []

    max_view = max((st['view_w'] for _, st in rows), default=0) or 1.0
    max_comment = max((st['comment_w'] for _, st in rows), default=0) or 1.0
    max_favorite = max((st['favorite_w'] for _, st in rows), default=0) or 1.0

    ranked = []
    for food, st in rows:
        view_n = st['view_w'] / max_view
        rating_n = _bayes_recent(st['recent_sum'], st['recent_n']) / 5.0
        comment_n = st['comment_w'] / max_comment if st['comment_w'] else 0.0
        favorite_n = st['favorite_w'] / max_favorite if st['favorite_w'] else 0.0

        hot = (WEIGHTS['view'] * view_n
               + WEIGHTS['rating'] * rating_n
               + WEIGHTS['comment'] * comment_n
               + WEIGHTS['favorite'] * favorite_n)
        # 今天有人看过的菜，排名上给一点倾斜：慢火菜不会靠老数据一直霸榜
        if st['today_pv'] > 0:
            hot *= TODAY_BOOST

        ranked.append({
            **food,
            'hot_score': round(hot, 4),
            'pv': st['pv'],
            'today_pv': st['today_pv'],
            'recent_rating': round(_bayes_recent(st['recent_sum'], st['recent_n']), 2),
            'recent_avg': round(st['recent_avg'], 2),
            'recent_n': st['recent_n'],
            'comment_n': st['comment_n'],
            'favorite_n': st['favorite_n'],
        })

    # 分数相同用浏览量、均分、id 兜底，保证同一天内刷新顺序稳定
    ranked.sort(key=lambda x: (-x['hot_score'], -x['pv'], -(x['avg_rating'] or 0), x['id']))
    return ranked


def get_daily_picks(limit=5, exclude_ids=None):
    """返回首页每日热榜

    :param limit: 条数，首页轮播通常 5 条
    :param exclude_ids: 需要排除的菜品（用户拉黑的），不算个性化，只是尊重黑名单
    :return: {'items': [...], 'stats_days': int}
    """
    exclude_ids = exclude_ids or []

    # 通用榜单命中缓存就直接返回（带黑名单的请求不走缓存）
    if not exclude_ids and _CACHE['data'] is not None:
        if time.time() - _CACHE['ts'] < CACHE_TTL:
            return _CACHE['data']

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        foods = _load_foods(cursor, exclude_ids)
        if not foods:
            return {'items': [], 'stats_days': STATS_DAYS}

        ranked = _score_window(foods, _load_stats(cursor, STATS_DAYS))
        chosen = _diversify(ranked, limit)

        for idx, item in enumerate(chosen, start=1):
            item['rank'] = idx
            item['reason'] = _build_reason(item)

        result = {'items': chosen, 'stats_days': STATS_DAYS}
        if not exclude_ids:
            _CACHE['data'] = result
            _CACHE['ts'] = time.time()
        return result
    finally:
        cursor.close()
        conn.close()


def record_view(food_id, user_id=None, visitor_key=None, dedupe_minutes=30):
    """记录一次美食浏览（供每日热榜统计 PV）

    同一访客短时间内反复刷新同一道菜只记一次，防止 F5 把热榜刷歪。
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if user_id:
            cursor.execute("""
                SELECT 1 FROM food_views
                WHERE food_id = %s AND user_id = %s
                  AND created_at >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
                LIMIT 1
            """, (food_id, user_id, dedupe_minutes))
        elif visitor_key:
            cursor.execute("""
                SELECT 1 FROM food_views
                WHERE food_id = %s AND visitor_key = %s
                  AND created_at >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
                LIMIT 1
            """, (food_id, visitor_key, dedupe_minutes))
        else:
            cursor.execute("""
                SELECT 1 FROM food_views
                WHERE food_id = %s AND created_at >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
                LIMIT 1
            """, (food_id, dedupe_minutes))

        if cursor.fetchone():
            return False

        cursor.execute("""
            INSERT INTO food_views (food_id, user_id, visitor_key)
            VALUES (%s, %s, %s)
        """, (food_id, user_id, visitor_key))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()
