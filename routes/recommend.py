# routes/recommend.py
# 推荐系统路由：协同过滤 + 安全过滤 + 冷启动兜底
from flask import Blueprint, render_template, session, redirect, url_for
from utils.db import get_db_connection
from utils.cf import recommend_by_cf

recommend_bp = Blueprint('recommend', __name__)


def get_hot_foods(limit=10, exclude_ids=None):
    """
    冷启动兜底：按评分数量 + 平均评分推荐热门美食
    """
    exclude_ids = exclude_ids or []
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        SELECT f.id, f.name, f.price, f.avg_rating, f.like_count, f.image_url,
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

    sql += """
        GROUP BY f.id
        ORDER BY rating_count DESC, f.avg_rating DESC
        LIMIT %s
    """
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return [
        {
            'id': row[0], 'name': row[1], 'price': float(row[2]),
            'avg_rating': float(row[3]) if row[3] else 0,
            'like_count': row[4], 'image_url': row[5],
            'merchant_name': row[6], 'category_name': row[7],
            'rating_count': row[8]
        }
        for row in rows
    ]


def get_user_safety_info(user_id):
    """
    获取用户的过敏、忌口、黑名单信息
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT allergy, taboo FROM users WHERE id = %s", (user_id,))
    row = cursor.fetchone()
    allergy = row[0] if row and row[0] else ''
    taboo = row[1] if row and row[1] else ''

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
        'black_merchant_ids': black_merchant_ids
    }


def apply_safety_filter(food_ids, safety_info):
    """
    对推荐结果做安全过滤：
    1. 过敏食材 → 强制过滤（完全隐藏）
    2. 忌口食材 → 保留但标注
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

    safe_ids = []
    taboo_tagged = set()

    for food_id, name, ingredients, merchant_id in rows:
        ingredients = ingredients or ''

        # 黑名单过滤（美食 + 商家）
        if food_id in black_food_ids:
            continue
        if merchant_id in black_merchant_ids:
            continue

        # 过敏强制过滤
        if any(a and a in ingredients for a in allergy_list):
            continue

        # 忌口标注（保留，但打标签）
        if any(t and t in ingredients for t in taboo_list):
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


@recommend_bp.route('/recommend')
def recommend_page():
    """
    猜你喜欢页面
    """
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth.login'))

    safety_info = get_user_safety_info(user_id)

    cf_results = recommend_by_cf(user_id, top_n=20, k_neighbors=5)

    is_cold_start = False
    if cf_results:
        food_ids = [food_id for food_id, _ in cf_results]
        safe_ids, taboo_tagged = apply_safety_filter(food_ids, safety_info)
        foods = get_foods_by_ids(safe_ids, taboo_tagged)
    else:
        is_cold_start = True
        foods = get_hot_foods(limit=12)
        hot_ids = [f['id'] for f in foods]
        safe_ids, taboo_tagged = apply_safety_filter(hot_ids, safety_info)
        foods = [f for f in foods if f['id'] in safe_ids]
        for f in foods:
            f['is_taboo'] = f['id'] in taboo_tagged

    return render_template(
        'recommend.html',
        foods=foods,
        is_cold_start=is_cold_start
    )

@recommend_bp.route('/recommend/evaluate')
def evaluate_page():
    """
    推荐效果评估页面（管理员可访问）
    """
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth.login'))

    # 只有管理员能看评估报告
    if session.get('role') != 'admin':
        return "无权访问，仅管理员可查看推荐效果评估", 403

    from utils.cf import evaluate_mae_rmse

    # 评估不同 K 值下的效果
    results = []
    for k in [3, 5, 7, 10]:
        result = evaluate_mae_rmse(test_ratio=0.2, k_neighbors=k)
        result['k'] = k
        results.append(result)

    return render_template('recommend_eval.html', results=results)