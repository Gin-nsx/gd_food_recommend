# routes/recommend.py
# 推荐系统路由：协同过滤 + 安全过滤 + 冷启动兜底 + 推荐理由
from flask import Blueprint, render_template, session, redirect, url_for, request, abort
from utils.db import get_db_connection
from utils.cf import MIN_RELEVANT, explain_candidates, similarity_label

recommend_bp = Blueprint('recommend', __name__)

# 推荐列表规模：
#   一次最多算 REC_MAX 条（再往后推，菜品之间的差异已经很弱，排序信噪比低，
#   硬凑数量反而会推出「跟你口味没关系」的菜）；
#   页面首屏显示 REC_PAGE_SIZE 条，剩下的点「加载更多」再展开。
REC_PAGE_SIZE = 12
REC_MAX = 48

METHOD_LABELS = {
    'random': '随机推荐',
    'popular': '热门推荐',
    'user_cf': 'User-CF',
    'item_cf': 'Item-CF',
}


# ========== 冷启动参数 ==========
# 新用户还没有评分，只能靠"全站口碑"推荐。不能简单按评分人数排序：
# 那样 88 人打 2.8 分的菜会排在 30 人打 4.8 分的前面，等于把差评菜推给新用户。
#
# 打分公式与评估页「热门」基线（utils/cf.py: score_popular）完全一致：
#     score = (Σ 好评分 + prior × 先验均值) / (票数 + prior)
# 好评 = 4 分及以上（MIN_RELEVANT）；低分不进分子但计入分母，等于被稀释。
COLD_PRIOR = 5.0          # 先验票数：票数越少，分数越接近先验均值，防少数票冲榜
COLD_PRIOR_MEAN = 0.5     # 先验均值
COLD_MIN_AVG = 3.5        # 口碑红线：评分人数够了还低于这个分，不推给新用户
COLD_MIN_VOTES = 5        # 超过这个票数才算"口碑已形成"，红线才生效
COLD_MIN_FOR_LIST = 3     # 进热门榜至少要有 3 条评分，0 评价的新菜不占位


def _bayes_popularity(pos_sum, votes, prior=COLD_PRIOR, prior_mean=COLD_PRIOR_MEAN):
    """贝叶斯加权好评分，与评估页的热门基线同源，保证线上线下口径一致"""
    return (pos_sum + prior * prior_mean) / (votes + prior)


def get_hot_foods(limit=12, exclude_ids=None):
    """冷启动兜底：按「贝叶斯加权好评分」推荐，而不是单纯按评价人数

    1. 打分：好评（≥4 分）总分向先验收缩，票少的高分菜不会虚高，
       票多的差评菜也会被大量低分稀释；
    2. 门槛：评价人数 ≥5 且平均分 <3.5 的菜直接剔除，不推给新用户；
    3. 保底：达标菜不足时从剩余里补，但只补平均分 ≥3.5 的。
    """
    exclude_ids = exclude_ids or []
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        SELECT f.id, f.name, f.price, f.avg_rating, f.like_count, f.image_url,
               COALESCE(u.merchant_name, u.username) AS merchant_name,
               c.name AS category_name,
               COUNT(r.id) AS rating_count,
               COALESCE(SUM(CASE WHEN r.score >= %s THEN r.score ELSE 0 END), 0) AS pos_sum
        FROM foods f
        LEFT JOIN users u ON f.merchant_id = u.id
        LEFT JOIN categories c ON f.category_id = c.id
        LEFT JOIN ratings r ON f.id = r.food_id
        WHERE f.status = 'approved'
    """
    from utils.cf import MIN_RELEVANT
    params = [MIN_RELEVANT]
    if exclude_ids:
        placeholders = ','.join(['%s'] * len(exclude_ids))
        sql += f" AND f.id NOT IN ({placeholders})"
        params.extend(exclude_ids)

    # 先按票数粗筛候选（菜品量级在千以内，前 500 足够），精确排序在 Python 里做
    sql += """
        GROUP BY f.id
        ORDER BY rating_count DESC
        LIMIT 500
    """

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    candidates = []
    for row in rows:
        votes = int(row[8] or 0)
        pos_sum = float(row[9] or 0)
        candidates.append({
            'id': row[0], 'name': row[1], 'price': float(row[2]),
            'avg_rating': float(row[3]) if row[3] else 0.0,
            'like_count': row[4], 'image_url': row[5],
            'merchant_name': row[6], 'category_name': row[7],
            'rating_count': votes,
            'cold_score': round(_bayes_popularity(pos_sum, votes), 4),
        })

    def _is_bad(c):
        """票数够多、分数却明显偏低 —— 说明是真的不受欢迎，不进榜"""
        return (c['rating_count'] >= COLD_MIN_VOTES
                and c['avg_rating'] and c['avg_rating'] < COLD_MIN_AVG)

    sort_key = lambda c: (-c['cold_score'], -c['rating_count'], c['id'])

    good = [c for c in candidates
            if c['rating_count'] >= COLD_MIN_FOR_LIST and not _is_bad(c)]
    good.sort(key=sort_key)
    picked = good[:limit]

    # 达标菜不够时，从剩余里挑平均分及格的补位（不补差评菜）
    if len(picked) < limit:
        picked_ids = {c['id'] for c in picked}
        spare = [c for c in candidates
                 if c['id'] not in picked_ids
                 and not _is_bad(c)
                 and (c['avg_rating'] or 0) >= COLD_MIN_AVG]
        spare.sort(key=sort_key)
        picked.extend(spare[:limit - len(picked)])

    return picked


def get_user_safety_info(user_id):
    """
    获取用户的过敏、忌口、黑名单信息
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT allergy, taboo, hide_risk FROM users WHERE id = %s", (user_id,))
    row = cursor.fetchone()
    allergy = row[0] if row and row[0] else ''
    taboo = row[1] if row and row[1] else ''
    # 用户在个人中心勾了「含过敏/忌口的美食隐藏后不推荐」，忌口也从"只标注"升级为"过滤"
    hide_risk = bool(row[2]) if row else False

    cursor.execute(
        "SELECT food_id, merchant_id FROM blacklists WHERE user_id = %s",
        (user_id,)
    )
    blacklist_rows = cursor.fetchall()
    black_food_ids = set()
    black_merchant_ids = set()
    for food_id, merchant_id in blacklist_rows:
        if food_id:
            black_food_ids.add(food_id)
        if merchant_id:
            black_merchant_ids.add(merchant_id)

    cursor.close()
    conn.close()

    return {
        'allergy': [x.strip() for x in allergy.split(',') if x.strip()],
        'taboo': [x.strip() for x in taboo.split(',') if x.strip()],
        'black_food_ids': black_food_ids,
        'black_merchant_ids': black_merchant_ids,
        'hide_risk': hide_risk,
    }


def apply_safety_filter(food_ids, safety_info):
    """
    对推荐结果做安全过滤：
    1. 过敏食材 → 强制过滤（完全隐藏）
    2. 忌口食材 → 默认保留但标注；用户在个人中心开启「隐藏」后一并过滤
    3. 黑名单美食/商家 → 过滤
    """
    if not food_ids:
        return [], set()

    conn = get_db_connection()
    cursor = conn.cursor()

    placeholders = ','.join(['%s'] * len(food_ids))
    cursor.execute(f"""
        SELECT id, name, ingredients, merchant_id
        FROM foods
        WHERE id IN ({placeholders})
    """, food_ids)

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    allergy_list = safety_info['allergy']
    taboo_list = safety_info['taboo']
    black_food_ids = safety_info['black_food_ids']
    black_merchant_ids = safety_info['black_merchant_ids']
    hide_risk = safety_info.get('hide_risk', False)

    safe_ids = []
    taboo_tagged = set()

    for food_id, name, ingredients, merchant_id in rows:
        ingredients = ingredients or ''
        name = name or ''
        haystack = ingredients + ' ' + name

        # 黑名单过滤（美食 + 商家）
        if food_id in black_food_ids:
            continue
        if merchant_id in black_merchant_ids:
            continue

        # 过敏强制过滤
        if any(a and a in haystack for a in allergy_list):
            continue

        # 忌口：默认打标签保留，开启隐藏后直接过滤
        if any(t and t in haystack for t in taboo_list):
            if hide_risk:
                continue
            taboo_tagged.add(food_id)

        safe_ids.append(food_id)

    return safe_ids, taboo_tagged


def get_foods_by_ids(food_ids, taboo_tagged=None):
    """
    根据ID列表查询美食详情，保持原推荐顺序
    """
    if not food_ids:
        return []

    taboo_tagged = taboo_tagged or set()
    conn = get_db_connection()
    cursor = conn.cursor()

    placeholders = ','.join(['%s'] * len(food_ids))
    cursor.execute(f"""
        SELECT f.id, f.name, f.price, f.avg_rating, f.like_count, f.image_url,
               COALESCE(u.merchant_name, u.username) AS merchant_name,
               c.name AS category_name,
               f.ingredients, f.description
        FROM foods f
        LEFT JOIN users u ON f.merchant_id = u.id
        LEFT JOIN categories c ON f.category_id = c.id
        WHERE f.id IN ({placeholders}) AND f.status = 'approved'
    """, food_ids)

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    food_map = {}
    for row in rows:
        food_map[row[0]] = {
            'id': row[0], 'name': row[1], 'price': float(row[2]),
            'avg_rating': float(row[3]) if row[3] else 0,
            'like_count': row[4], 'image_url': row[5],
            'merchant_name': row[6], 'category_name': row[7],
            'ingredients': row[8], 'taste_desc': row[9],
            'is_taboo': row[0] in taboo_tagged
        }

    return [food_map[fid] for fid in food_ids if fid in food_map]


def get_food_names(food_ids):
    """批量取菜名：推荐理由里要显示「你给『XX』打了几分」"""
    if not food_ids:
        return {}
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholders = ','.join(['%s'] * len(food_ids))
    cursor.execute(f"SELECT id, name FROM foods WHERE id IN ({placeholders})",
                   list(food_ids))
    names = {row[0]: row[1] for row in cursor.fetchall()}
    cursor.close()
    conn.close()
    return names


def get_taste_profile(user_id, limit=3):
    """
    口味画像：用户评了多少道菜、其中多少道算喜欢、高分集中在哪几个分类
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN score >= 4 THEN 1 ELSE 0 END) AS liked,
               ROUND(AVG(score), 2) AS avg_score
        FROM ratings WHERE user_id = %s
    """, (user_id,))
    row = cursor.fetchone()
    total = int(row[0] or 0)
    liked = int(row[1] or 0)
    avg_score = float(row[2]) if row[2] is not None else 0.0

    cursor.execute("""
        SELECT c.name, COUNT(*) AS cnt
        FROM ratings r
        JOIN foods f ON r.food_id = f.id
        JOIN categories c ON f.category_id = c.id
        WHERE r.user_id = %s AND r.score >= 4
        GROUP BY c.name
        ORDER BY cnt DESC
        LIMIT %s
    """, (user_id, limit))
    top_categories = [{'name': r[0], 'count': int(r[1])} for r in cursor.fetchall()]

    # 最爱（唯一一道/几道 5 分菜，用于文案）
    cursor.execute("""
        SELECT f.name FROM ratings r JOIN foods f ON r.food_id = f.id
        WHERE r.user_id = %s AND r.score = 5
        ORDER BY r.updated_at DESC, r.id DESC LIMIT 1
    """, (user_id,))
    top_row = cursor.fetchone()

    cursor.close()
    conn.close()

    return {
        'total': total,
        'liked': liked,
        'avg_score': avg_score,
        'top_categories': top_categories,
        'latest_favorite': top_row[0] if top_row else None,
    }


def attach_reasons(foods, items, names):
    """
    把 explain_candidates() 的结果贴到菜品字典上，理由里的菜名补全后做成
    模板直接可渲染的中文句子；找不到菜名的理由（菜品已下架/删除）直接丢掉。
    """
    by_id = {it['food_id']: it for it in items}

    for food in foods:
        info = by_id.get(food['id'], {})
        food['pred_score'] = info.get('score')
        food['rating_count'] = info.get('rating_count', 0)

        reasons = []
        for r in info.get('reasons', []):
            ref_name = names.get(r['ref_food_id'])
            if not ref_name:
                continue
            if r['type'] == 'liked':
                text = f"你给「{ref_name}」打了 {r['ref_score']:.0f} 分"
            else:
                text = f"跟「{ref_name}」很像"
            level = similarity_label(r['sim'])
            reasons.append({
                'text': text,
                'sim': r['sim'],
                'level': level['label'],
                'short': level['short'],
                'stars': level['stars'],
                'type': r['type'],
            })

        food['reasons'] = reasons[:2]
        # 平台口碑：评分人数够多才拿出来说，避免「3 个人评 5 分」这种噪声
        if info.get('rating_count', 0) >= 5 and info.get('avg'):
            food['support'] = f"🔥 {info['rating_count']} 人平均 {info['avg']:.1f} 分"
        else:
            food['support'] = None

    return foods


@recommend_bp.route('/recommend')
def recommend_page():
    """
    猜你喜欢页面
    """
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth.login_page', next=request.path))

    safety_info = get_user_safety_info(user_id)
    taste = get_taste_profile(user_id)

    # 带推荐理由的候选列表（内部仍是 Item-CF 打分，只是把「谁把它顶上来」一并返回）
    items, cf_profile = explain_candidates(user_id, top_n=REC_MAX, k_neighbors=5)

    is_cold_start = not items
    if items:
        food_ids = [it['food_id'] for it in items]
        safe_ids, taboo_tagged = apply_safety_filter(food_ids, safety_info)
        foods = get_foods_by_ids(safe_ids, taboo_tagged)

        ref_ids = {r['ref_food_id'] for it in items for r in it['reasons']}
        names = get_food_names(set(food_ids) | ref_ids)
        foods = attach_reasons(foods, items, names)
        foods = foods[:REC_MAX]
    else:
        # 多取一些再过滤：过敏/黑名单可能剔掉几条，避免过滤后只剩一小撮
        foods = get_hot_foods(limit=REC_MAX + 12)
        hot_ids = [f['id'] for f in foods]
        safe_ids, taboo_tagged = apply_safety_filter(hot_ids, safety_info)
        foods = [f for f in foods if f['id'] in safe_ids][:REC_MAX]
        for f in foods:
            f['is_taboo'] = f['id'] in taboo_tagged
            f['reasons'] = []
            f['pred_score'] = None
            if f.get('rating_count'):
                f['support'] = f"🔥 {f['avg_rating']:.1f} 分 · {f['rating_count']} 人评价"
            else:
                f['support'] = '🌱 新上架，暂无评价'

    return render_template(
        'recommend.html',
        foods=foods,
        is_cold_start=is_cold_start,
        taste=taste,
        cf_profile=cf_profile,
        page_size=REC_PAGE_SIZE,
    )

@recommend_bp.route('/recommend/evaluate')
def evaluate_page():
    """
    推荐效果评估页面（管理员可访问）
    展示 4 种算法（随机 / 热门 / User-CF / Item-CF）在 Top-N 推荐下的对比，
    以及 User-CF 评分预测的 MAE / RMSE。
    """
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth.login_page', next=request.path))

    # 只有管理员能看评估报告
    if session.get('role') != 'admin':
        abort(403)

    from utils.cf import evaluate_all, evaluate_mae_rmse

    top_n = request.args.get('top_n', default=10, type=int)
    if top_n not in (5, 10, 20):
        top_n = 10

    k_list = [5, 10, 20, 40]
    report = evaluate_all(top_n=top_n, k_list=k_list)

    # 评分预测（回归）指标，用于和 Top-N 指标互相印证
    mae_rows = []
    for k in k_list:
        row = evaluate_mae_rmse(test_ratio=0.2, k_neighbors=k)
        row['k'] = k
        mae_rows.append(row)

    rows = report.get('rows', [])
    best = max(rows, key=lambda r: r['precision']) if rows else None

    # 给模板用的图表数据：按算法聚合
    chart = {'k_list': k_list, 'user_cf': {}, 'item_cf': {}}
    for m in ('user_cf', 'item_cf'):
        for key in ('precision', 'ndcg'):
            chart[m][key] = [
                next((r[key] for r in rows if r['method'] == m and r['k'] == k), 0)
                for k in k_list
            ]
    chart['bar'] = {
        'names': [],
        'precision': [],
        'ndcg': [],
    }
    for r in rows:
        if r['method'] in ('user_cf', 'item_cf') and r['k'] != k_list[0]:
            continue
        chart['bar']['names'].append(METHOD_LABELS.get(r['method'], r['method']))
        chart['bar']['precision'].append(r['precision'])
        chart['bar']['ndcg'].append(r['ndcg'])

    return render_template(
        'recommend_eval.html',
        report=report,
        rows=rows,
        best=best,
        mae_rows=mae_rows,
        chart=chart,
        top_n=top_n,
    )