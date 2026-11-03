# utils/cf.py
# 基于用户的协同过滤算法（User-based CF）
import math
import random
from utils.db import get_db_connection


def build_rating_matrix():
    """
    从 ratings 表读取数据，构建用户-美食评分矩阵
    返回: {user_id: {food_id: rating}}
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, food_id, score FROM ratings")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    matrix = {}
    for user_id, food_id, score in rows:
        matrix.setdefault(user_id, {})[food_id] = float(score)
    return matrix


def cosine_similarity(user_a, user_b):
    """
    计算两个用户之间的余弦相似度
    """
    common_items = set(user_a.keys()) & set(user_b.keys())
    if not common_items:
        return 0.0

    dot_product = sum(user_a[item] * user_b[item] for item in common_items)
    norm_a = math.sqrt(sum(user_a[item] ** 2 for item in user_a))
    norm_b = math.sqrt(sum(user_b[item] ** 2 for item in user_b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


def find_neighbors(target_user_id, matrix, k=5):
    """
    找到目标用户的 K 个最近邻
    返回: [(neighbor_user_id, similarity), ...] 按相似度降序
    """
    if target_user_id not in matrix:
        return []

    target_ratings = matrix[target_user_id]
    similarities = []

    for other_user_id, other_ratings in matrix.items():
        if other_user_id == target_user_id:
            continue
        sim = cosine_similarity(target_ratings, other_ratings)
        if sim > 0:
            similarities.append((other_user_id, sim))

    similarities.sort(key=lambda x: x[1], reverse=True)
    return similarities[:k]


def predict_ratings(target_user_id, neighbors, matrix, rated_food_ids=None):
    """
    基于近邻用户的评分，预测目标用户对未评分美食的评分
    """
    if not neighbors:
        return {}

    target_ratings = matrix.get(target_user_id, {})
    rated_food_ids = set(target_ratings.keys()) if rated_food_ids is None else rated_food_ids

    food_scores = {}
    for neighbor_id, similarity in neighbors:
        neighbor_ratings = matrix.get(neighbor_id, {})
        for food_id, score in neighbor_ratings.items():
            if food_id in rated_food_ids:
                continue
            food_scores.setdefault(food_id, []).append((similarity, score))

    predictions = {}
    for food_id, score_list in food_scores.items():
        total_sim = sum(sim for sim, _ in score_list)
        if total_sim == 0:
            continue
        weighted_sum = sum(sim * score for sim, score in score_list)
        predictions[food_id] = weighted_sum / total_sim

    return predictions


def recommend_by_cf(target_user_id, top_n=10, k_neighbors=5):
    """
    协同过滤推荐主入口
    返回: [(food_id, predicted_score), ...] 按预测评分降序
    """
    matrix = build_rating_matrix()

    if target_user_id not in matrix or len(matrix[target_user_id]) < 2:
        return []

    neighbors = find_neighbors(target_user_id, matrix, k=k_neighbors)
    if not neighbors:
        return []

    predictions = predict_ratings(target_user_id, neighbors, matrix)
    sorted_predictions = sorted(predictions.items(), key=lambda x: x[1], reverse=True)
    return sorted_predictions[:top_n]

# ===== 以下为推荐效果评估模块 =====

def split_train_test(matrix, test_ratio=0.2, seed=42):
    """
    留出法：将评分矩阵切分为训练集和测试集
    返回: (train_matrix, test_set)
    test_set: [(user_id, food_id, real_score), ...]
    """
    random.seed(seed)

    all_ratings = []
    for user_id, food_ratings in matrix.items():
        for food_id, score in food_ratings.items():
            all_ratings.append((user_id, food_id, score))

    random.shuffle(all_ratings)

    test_size = int(len(all_ratings) * test_ratio)
    test_set = all_ratings[:test_size]
    train_set = all_ratings[test_size:]

    train_matrix = {}
    for user_id, food_id, score in train_set:
        train_matrix.setdefault(user_id, {})[food_id] = score

    return train_matrix, test_set


def evaluate_mae_rmse(test_ratio=0.2, k_neighbors=5, seed=42):
    """
    评估协同过滤算法的 MAE 和 RMSE
    返回: {'mae': ..., 'rmse': ..., 'count': ..., 'test_ratio': ..., 'k': ...}
    """
    full_matrix = build_rating_matrix()

    if len(full_matrix) < 2:
        return {'mae': None, 'rmse': None, 'count': 0,
                'test_ratio': test_ratio, 'k': k_neighbors,
                'msg': '用户数量不足，无法评估'}

    train_matrix, test_set = split_train_test(full_matrix, test_ratio, seed)

    if not test_set:
        return {'mae': None, 'rmse': None, 'count': 0,
                'test_ratio': test_ratio, 'k': k_neighbors,
                'msg': '测试集为空'}

    abs_error_sum = 0.0
    sq_error_sum = 0.0
    count = 0

    # 对每个测试样本进行预测
    for user_id, food_id, real_score in test_set:
        if user_id not in train_matrix:
            continue

        neighbors = find_neighbors(user_id, train_matrix, k=k_neighbors)
        if not neighbors:
            continue

        predictions = predict_ratings(user_id, neighbors, train_matrix)

        if food_id not in predictions:
            continue

        pred_score = predictions[food_id]
        abs_error_sum += abs(pred_score - real_score)
        sq_error_sum += (pred_score - real_score) ** 2
        count += 1

    if count == 0:
        return {'mae': None, 'rmse': None, 'count': 0,
                'test_ratio': test_ratio, 'k': k_neighbors,
                'msg': '无法计算出有效预测（评分数据太少）'}

    mae = abs_error_sum / count
    rmse = math.sqrt(sq_error_sum / count)

    return {
        'mae': round(mae, 4),
        'rmse': round(rmse, 4),
        'count': count,
        'test_ratio': test_ratio,
        'k': k_neighbors,
        'total_ratings': len(test_set),
    }