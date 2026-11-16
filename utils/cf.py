# utils/cf.py
# 协同过滤推荐算法（User-CF / Item-CF）+ 基线模型 + 离线评估
#
# 相比初版的三点改进（也是论文实验要讲清楚的三个点）：
#   1. 相似度改为「共现项去均值后的皮尔逊相关」，而不是直接对原始评分做余弦。
#      原因：有人习惯全打 5 分、有人习惯全打 3 分，原始余弦会把「打分宽」误判为「口味像」。
#   2. 引入偏置项 b_ui = μ + b_u + b_i，先解释「用户宽严」和「菜品本身好坏」，
#      协同过滤只负责解释剩余的口味差异，预测值不再挤在同一档。
#   3. 相似度按共现数量做收缩（n / (n + λ)），避免「只共同评过 1 道菜就判定高度相似」。
import math
import random

import numpy as np

from utils.db import get_db_connection

# 评分 >= 4 视为「喜欢」，用于 Top-N 评估的相关性判定（学术界常用阈值）
MIN_RELEVANT = 4
# 相似度收缩系数：共同评分数越少，相似度被压得越低
SHRINK_LAMBDA = 6.0
# 偏置项 L2 正则强度
BIAS_REG = 8.0
BIAS_ITERS = 15

METHOD_NAMES = {
    'random': '随机推荐',
    'popular': '热门推荐',
    'user_cf': 'User-CF（用户协同）',
    'item_cf': 'Item-CF（物品协同）',
}


# --------------------------------------------------------------------------
# 数据读取
# --------------------------------------------------------------------------

def load_ratings():
    """从数据库读取全部评分，返回 [(user_id, food_id, score), ...]"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, food_id, score FROM ratings")
    rows = [(int(u), int(f), float(s)) for u, f, s in cursor.fetchall()]
    cursor.close()
    conn.close()
    return rows


def build_rating_matrix():
    """兼容旧接口：返回 {user_id: {food_id: score}}"""
    matrix = {}
    for user_id, food_id, score in load_ratings():
        matrix.setdefault(user_id, {})[food_id] = score
    return matrix


# --------------------------------------------------------------------------
# 数据集：用稠密矩阵存一份，200 用户 × 74 菜品完全放得下
# --------------------------------------------------------------------------

class RatingDataset:
    """
    评分矩阵容器。
        R: n_users × n_items 的评分矩阵，未评分为 0
        M: 同形状的布尔矩阵，True 表示有评分
        B: 偏置模型预测 μ + b_u + b_i
    """

    def __init__(self, user_ids, item_ids, R, M):
        self.user_ids = list(user_ids)
        self.item_ids = list(item_ids)
        self.user_index = {u: i for i, u in enumerate(self.user_ids)}
        self.item_index = {f: i for i, f in enumerate(self.item_ids)}
        self.R = R.astype(float)
        self.M = M.astype(bool)
        self.mu = float(self.R[self.M].mean()) if self.M.any() else 3.0
        self.bu = np.zeros(len(self.user_ids))
        self.bi = np.zeros(len(self.item_ids))
        self._user_sim = None
        self._item_sim = None
        self._fit_bias()

    # ---- 构造 ----
    @classmethod
    def from_triples(cls, triples):
        user_ids = sorted({u for u, _, _ in triples})
        item_ids = sorted({f for _, f, _ in triples})
        ui = {u: i for i, u in enumerate(user_ids)}
        fi = {f: i for i, f in enumerate(item_ids)}
        R = np.zeros((len(user_ids), len(item_ids)))
        M = np.zeros((len(user_ids), len(item_ids)), dtype=bool)
        for u, f, s in triples:
            R[ui[u], fi[f]] = s
            M[ui[u], fi[f]] = True
        return cls(user_ids, item_ids, R, M)

    def subset(self, mask):
        """按掩码取训练子集（保持用户/菜品维度不变）"""
        return RatingDataset(self.user_ids, self.item_ids,
                             np.where(mask, self.R, 0.0), mask)

    # ---- 偏置项 b_u、b_i：交替最小二乘，交替更新到收敛 ----
    def _fit_bias(self):
        n_u = self.M.sum(axis=1)
        n_i = self.M.sum(axis=0)
        for _ in range(BIAS_ITERS):
            err = (self.R - self.mu - self.bu[:, None] - self.bi[None, :]) * self.M
            self.bu = err.sum(axis=1) / (BIAS_REG + n_u)
            err = (self.R - self.mu - self.bu[:, None] - self.bi[None, :]) * self.M
            self.bi = err.sum(axis=0) / (BIAS_REG + n_i)

    @property
    def B(self):
        """偏置模型的预测矩阵 μ + b_u + b_i"""
        return self.mu + self.bu[:, None] + self.bi[None, :]

    # ---- 相似度矩阵 ----
    def _centered(self):
        """(R - 用户均值) * M：只在共同评分项上参与相关计算"""
        n_u = self.M.sum(axis=1, keepdims=True).astype(float)
        user_mean = np.divide(self.R.sum(axis=1, keepdims=True), n_u,
                              out=np.zeros_like(n_u), where=n_u > 0)
        return (self.R - user_mean) * self.M

    def user_similarity(self):
        """用户-用户皮尔逊相关，按共现数收缩"""
        if self._user_sim is not None:
            return self._user_sim

        C = self._centered()
        dot = C @ C.T
        norm = np.sqrt((C ** 2).sum(axis=1))
        denom = np.outer(norm, norm)
        common = self.M.astype(float) @ self.M.astype(float).T

        sim = np.divide(dot, denom, out=np.zeros_like(dot), where=denom > 0)
        sim *= common / (common + SHRINK_LAMBDA)      # 共现收缩
        np.fill_diagonal(sim, 0.0)
        self._user_sim = sim
        return sim

    def item_similarity(self):
        """物品-物品皮尔逊相关（列方向），按共现数收缩"""
        if self._item_sim is not None:
            return self._item_sim

        # 物品向量的中心化：减掉该物品的平均分
        n_i = self.M.sum(axis=0, keepdims=True).astype(float)
        item_mean = np.divide(self.R.sum(axis=0, keepdims=True), n_i,
                              out=np.zeros_like(n_i), where=n_i > 0)
        C = (self.R - item_mean) * self.M

        dot = C.T @ C
        norm = np.sqrt((C ** 2).sum(axis=0))
        denom = np.outer(norm, norm)
        common = self.M.astype(float).T @ self.M.astype(float)

        sim = np.divide(dot, denom, out=np.zeros_like(dot), where=denom > 0)
        sim *= common / (common + SHRINK_LAMBDA)
        np.fill_diagonal(sim, 0.0)
        self._item_sim = sim
        return sim

    @staticmethod
    def _keep_top_k(sim, k):
        """只保留每行相似度最高的 k 个，其余置 0"""
        if k is None or k >= sim.shape[1]:
            return sim
        thresh = np.partition(sim, -k, axis=1)[:, -k][:, None]
        return np.where(sim >= thresh, sim, 0.0)

    # ---- 各算法的打分矩阵（返回 n_users × n_items）----
    def score_popular(self):
        """热门推荐：贝叶斯平均，避免只有 1 条 5 分就冲上第一"""
        n = self.M.sum(axis=0)
        positive = (self.R * (self.R >= MIN_RELEVANT)).sum(axis=0)
        prior = 5.0
        score = (positive + prior * 0.5) / (n + prior)
        return np.tile(score, (len(self.user_ids), 1))

    def score_random(self, rng_seed=42):
        rng = np.random.default_rng(rng_seed)
        return rng.random((len(self.user_ids), len(self.item_ids)))

    def score_user_cf(self, k_neighbors=20):
        """
        预测 b_ui + 加权偏差：邻居对该菜的「超出自身偏置的部分」按相似度加权
        """
        sim = self._keep_top_k(self.user_similarity(), k_neighbors)
        D = (self.R - self.B) * self.M            # 残差：评分减去偏置预测
        num = sim @ D
        den = np.abs(sim) @ self.M.astype(float)
        return self.B + np.divide(num, den, out=np.zeros_like(num), where=den > 0)

    def score_item_cf(self, k_neighbors=20):
        """
        预测 b_ui + 该用户已评分菜品的偏差，按物品相似度加权
        """
        sim = self._keep_top_k(self.item_similarity(), k_neighbors)
        D = (self.R - self.B) * self.M
        num = D @ sim
        den = self.M.astype(float) @ np.abs(sim)
        return self.B + np.divide(num, den, out=np.zeros_like(num), where=den > 0)

    def score_matrix(self, method, k_neighbors=20):
        if method == 'random':
            return self.score_random()
        if method == 'popular':
            return self.score_popular()
        if method == 'user_cf':
            return self.score_user_cf(k_neighbors)
        if method == 'item_cf':
            return self.score_item_cf(k_neighbors)
        raise ValueError(f'未知算法: {method}')


# --------------------------------------------------------------------------
# 训练/测试集划分：按用户分层留出法
# --------------------------------------------------------------------------

def split_per_user(R, M, test_ratio=0.2, seed=42, min_ratings=5):
    """
    每个用户留出 test_ratio 比例的评分作为测试集。
    好处：每个用户都有测试样本，不会出现「某个用户全在测试集里」的情况。
    """
    rng = np.random.default_rng(seed)
    train_mask = M.copy()
    test_pairs = []

    for u in range(R.shape[0]):
        idx = np.flatnonzero(M[u])
        if len(idx) < min_ratings:
            continue
        n_test = max(1, int(round(len(idx) * test_ratio)))
        chosen = rng.choice(idx, size=n_test, replace=False)
        train_mask[u, chosen] = False
        for i in chosen:
            test_pairs.append((u, int(i), float(R[u, i])))

    return train_mask, test_pairs


def split_train_test(matrix, test_ratio=0.2, seed=42):
    """兼容旧接口：全局随机留出法（返回 dict 形式）"""
    random.seed(seed)
    all_ratings = [(u, f, s) for u, fs in matrix.items() for f, s in fs.items()]
    random.shuffle(all_ratings)

    test_size = int(len(all_ratings) * test_ratio)
    test_set = all_ratings[:test_size]
    train_matrix = {}
    for user_id, food_id, score in all_ratings[test_size:]:
        train_matrix.setdefault(user_id, {})[food_id] = score
    return train_matrix, test_set


# --------------------------------------------------------------------------
# 在线推荐
# --------------------------------------------------------------------------

def recommend_by_cf(target_user_id, top_n=10, k_neighbors=5, method='user_cf'):
    """
    推荐主入口，返回 [(food_id, predicted_score), ...] 按预测分降序。
    target_user_id 不在矩阵中或评分太少时返回空列表，由调用方走热门兜底。
    """
    triples = load_ratings()
    if not triples:
        return []

    ds = RatingDataset.from_triples(triples)
    if target_user_id not in ds.user_index:
        return []

    u = ds.user_index[target_user_id]
    if ds.M[u].sum() < 2:
        return []

    scores = ds.score_matrix(method, k_neighbors)[u]
    scores = np.where(ds.M[u], -np.inf, scores)          # 已评过的不再推荐

    order = np.argsort(-scores)[:top_n]
    return [(ds.item_ids[i], round(float(np.clip(scores[i], 1.0, 5.0)), 2))
            for i in order if np.isfinite(scores[i])]


# --------------------------------------------------------------------------
# 推荐解释：告诉用户「为什么给你推这道菜」
# --------------------------------------------------------------------------
#
# 思路：排名的分数没法直接给用户看，但 Item-CF 的分数本来就是
#   b_ui + Σ(相似菜品偏差 × 相似度) / Σ|相似度|
# 也就是说「哪几道你评过分的菜把它顶上来」是可以逐项追溯的。
# 这里把每个候选菜最相似的 2 道已评分菜品拎出来，作为推荐理由；
# 同时附上平台口碑（评分人数 / 平均分）作为补充理由。

# 相似度低于这个值就不作为理由展示，避免「因为一道毫不相干的菜」这种废话
MIN_REASON_SIM = 0.05

# 展示层翻译：相似度是算法内部指标（收缩后的皮尔逊相关系数，0.3 已经算相当接近），
# 直接把 0.42 摆给用户，用户只会觉得「怎么才零点几」，所以统一换成口头描述 + 星级。
SIM_LEVELS = [
    (0.30, '口味非常接近', 5, '非常接近'),
    (0.20, '口味很接近', 4, '很接近'),
    (0.12, '口味比较接近', 3, '比较接近'),
    (0.06, '口味有点接近', 2, '有点接近'),
]
_SIM_DEFAULT = ('口味略有相似', 1, '略有相似')


def similarity_label(sim):
    """把 0~1 的相似度翻译成用户看得懂的说法

    返回 {'label': 完整说法（详情页用）, 'short': 短标签（卡片角标用）, 'stars': 1~5}
    """
    if sim is None:
        return {'label': _SIM_DEFAULT[0], 'short': _SIM_DEFAULT[2], 'stars': _SIM_DEFAULT[1]}
    for threshold, label, stars, short in SIM_LEVELS:
        if sim >= threshold:
            return {'label': label, 'short': short, 'stars': stars}
    return {'label': _SIM_DEFAULT[0], 'short': _SIM_DEFAULT[2], 'stars': _SIM_DEFAULT[1]}


def _item_stats(ds):
    """每道菜的评分人数与平均分"""
    n = ds.M.sum(axis=0).astype(float)
    avg = np.divide(ds.R.sum(axis=0), n, out=np.zeros_like(n), where=n > 0)
    return n, avg


def explain_candidates(target_user_id, top_n=20, k_neighbors=5,
                       method='item_cf', like_threshold=4.0, max_reasons=2):
    """
    带解释的推荐列表。

    返回 (items, profile)：
        items: [{
            'food_id', 'score', 'rating_count', 'avg',
            'reasons': [{'type': 'liked'|'neutral', 'ref_food_id',
                         'ref_score', 'sim'}, ...]
        }, ...]
        profile: 用户口味画像 {'rated', 'liked', 'avg', 'top_refs'}，冷启动时为空 dict

    菜名由调用方（路由）补上，算法层不依赖数据库里的菜名。
    """
    triples = load_ratings()
    if not triples:
        return [], {}

    ds = RatingDataset.from_triples(triples)
    if target_user_id not in ds.user_index:
        return [], {}

    u = ds.user_index[target_user_id]
    n_rated = int(ds.M[u].sum())
    if n_rated < 2:
        return [], {'rated': n_rated}

    scores = ds.score_matrix(method, k_neighbors)[u]
    scores = np.where(ds.M[u], -np.inf, scores)      # 已评过的不再推荐
    order = np.argsort(-scores)[:top_n]

    sim = ds.item_similarity()
    n_item, avg_item = _item_stats(ds)
    rated_idx = np.flatnonzero(ds.M[u])
    rated_scores = ds.R[u, rated_idx]

    items = []
    for i in order:
        if not np.isfinite(scores[i]):
            continue
        # 打分为 0 说明这道菜和用户评过的菜没有任何相似度支撑（典型是刚上架、
        # 还没人评分的新菜），硬推上去只会显示"预测 1 分"，不如不推。
        if scores[i] <= 0:
            continue

        # 该候选菜与本用户已评分菜品的相似度，只保留正相关；
        # 排序时优先「喜欢过的菜」（4 分以上），同类里再按相似度排
        pairs = [(float(sim[i, j]), int(rated_idx[j]), float(rated_scores[j]))
                 for j in range(len(rated_idx)) if sim[i, j] > MIN_REASON_SIM]
        pairs.sort(key=lambda x: (x[2] >= like_threshold, x[0]), reverse=True)

        reasons = [{
            'type': 'liked' if fs >= like_threshold else 'neutral',
            'ref_food_id': fid,
            'ref_score': round(fs, 1),
            'sim': round(sim_val, 2),
        } for sim_val, fid, fs in pairs[:max_reasons]]

        items.append({
            'food_id': ds.item_ids[i],
            'score': round(float(np.clip(scores[i], 1.0, 5.0)), 2),
            'rating_count': int(n_item[i]),
            'avg': round(float(avg_item[i]), 2) if n_item[i] else 0.0,
            'reasons': reasons,
        })

    profile = {
        'rated': n_rated,
        'liked': int((rated_scores >= like_threshold).sum()),
        'avg': round(float(rated_scores.mean()), 2),
        'method': method,
        'k': k_neighbors,
    }
    return items, profile


def similar_foods(food_id, top_n=4, min_sim=0.01):
    """
    相似菜品（Item-CF 视角）：和这道菜被同一批人一起打高分的菜。
    详情页「相似口味推荐」用它，返回 [{'food_id', 'sim'}, ...]
    """
    triples = load_ratings()
    if not triples:
        return []

    ds = RatingDataset.from_triples(triples)
    if food_id not in ds.item_index:
        return []

    i = ds.item_index[food_id]
    sim = ds.item_similarity()[i]
    order = np.argsort(-sim)[:top_n + 1]

    out = []
    for j in order:
        if j == i or sim[j] < min_sim:
            continue
        out.append({'food_id': ds.item_ids[j], 'sim': round(float(sim[j]), 2)})
        if len(out) >= top_n:
            break
    return out


# --------------------------------------------------------------------------
# 离线评估
# --------------------------------------------------------------------------

def _ndcg_at_n(recommended, relevant, n):
    """二值相关性的 NDCG@N"""
    dcg = 0.0
    for rank, item in enumerate(recommended[:n], start=1):
        if item in relevant:
            dcg += 1.0 / math.log2(rank + 1)
    idcg = sum(1.0 / math.log2(r + 1) for r in range(1, min(len(relevant), n) + 1))
    return dcg / idcg if idcg > 0 else 0.0


def evaluate_topn(train_ds, test_pairs, method, k_neighbors=20,
                  top_n=10, all_item_count=None):
    """
    Top-N 推荐效果评估。
    指标：Precision@N、Recall@N、NDCG@N、覆盖率
    对训练集中有评分、测试集中有「喜欢」样本的用户逐一出评。
    """
    scores_all = train_ds.score_matrix(method, k_neighbors)
    fallback = train_ds.score_popular()

    precisions, recalls, ndcgs = [], [], []
    recommended_items = set()

    # 把测试集按用户聚合
    by_user = {}
    for u, i, score in test_pairs:
        by_user.setdefault(u, []).append((i, score))

    for u, pairs in by_user.items():
        relevant = {i for i, s in pairs if s >= MIN_RELEVANT}
        if not relevant:
            continue

        row = scores_all[u]
        # 训练集里已评分的菜品不参与推荐
        row = np.where(train_ds.M[u], -np.inf, row)

        if np.all(~np.isfinite(row)):
            row = fallback[u]

        top_idx = np.argsort(-row)[:top_n]
        top_idx = [i for i in top_idx if np.isfinite(row[i])]

        hits = sum(1 for i in top_idx if i in relevant)
        precisions.append(hits / top_n)
        recalls.append(hits / len(relevant))
        ndcgs.append(_ndcg_at_n(top_idx, relevant, top_n))
        recommended_items.update(top_idx)

    total_items = all_item_count or train_ds.R.shape[1]
    results = {
        'method': method,
        'method_name': METHOD_NAMES.get(method, method),
        'k': k_neighbors,
        'top_n': top_n,
        'precision': round(float(np.mean(precisions)), 4) if precisions else 0.0,
        'recall': round(float(np.mean(recalls)), 4) if recalls else 0.0,
        'ndcg': round(float(np.mean(ndcgs)), 4) if ndcgs else 0.0,
        'coverage': round(len(recommended_items) / total_items, 4) if total_items else 0.0,
        'user_count': len(precisions),
    }
    return results


def safe_div(a, b):
    return a / b if b else 0.0


def evaluate_all(test_ratio=0.2, top_n=10, seed=42,
                 k_list=(5, 10, 20, 40),
                 methods=('random', 'popular', 'user_cf', 'item_cf')):
    """
    一次性跑完全部算法 × 全部 K 值，只读一次数据库、相似度矩阵只算一次。
    评估页调用这个函数，避免逐个 K 值重复加载数据导致页面卡死。
    """
    triples = load_ratings()
    if not triples:
        return {'rows': [], 'msg': '暂无评分数据'}

    full = RatingDataset.from_triples(triples)
    train_mask, test_pairs = split_per_user(full.R, full.M, test_ratio, seed)
    train_ds = full.subset(train_mask)

    if not test_pairs:
        return {'rows': [], 'msg': '评分数据太少，无法划分测试集'}

    rows = []
    for method in methods:
        if method in ('popular', 'random'):
            rows.append(evaluate_topn(train_ds, test_pairs, method, k_neighbors=None,
                                      top_n=top_n, all_item_count=len(full.item_ids)))
            continue
        for k in k_list:
            rows.append(evaluate_topn(train_ds, test_pairs, method, k_neighbors=k,
                                      top_n=top_n, all_item_count=len(full.item_ids)))

    return {
        'rows': rows,
        'msg': None,
        'user_total': len(full.user_ids),
        'item_total': len(full.item_ids),
        'rating_total': len(triples),
        'test_total': len(test_pairs),
        'train_total': len(triples) - len(test_pairs),
        'density': round(len(triples) / (len(full.user_ids) * len(full.item_ids)) * 100, 1),
        'k_list': list(k_list),
    }


def evaluate_mae_rmse(test_ratio=0.2, k_neighbors=5, seed=42, method='user_cf'):
    """
    评分预测准确度：MAE / RMSE（兼容旧接口，供论文的「评分回归」实验使用）
    """
    triples = load_ratings()
    if len(triples) < 10:
        return {'mae': None, 'rmse': None, 'count': 0,
                'test_ratio': test_ratio, 'k': k_neighbors,
                'msg': '评分数据太少，无法评估'}

    full = RatingDataset.from_triples(triples)
    train_mask, test_pairs = split_per_user(full.R, full.M, test_ratio, seed)
    train_ds = full.subset(train_mask)

    if not test_pairs:
        return {'mae': None, 'rmse': None, 'count': 0,
                'test_ratio': test_ratio, 'k': k_neighbors, 'msg': '测试集为空'}

    scores = train_ds.score_matrix(method, k_neighbors)

    errors = []
    for u, i, real in test_pairs:
        pred = scores[u, i]
        if not np.isfinite(pred):
            continue
        errors.append(float(np.clip(pred, 1.0, 5.0)) - real)

    if not errors:
        return {'mae': None, 'rmse': None, 'count': 0,
                'test_ratio': test_ratio, 'k': k_neighbors, 'msg': '无有效预测'}

    err = np.array(errors)
    return {
        'mae': round(float(np.abs(err).mean()), 4),
        'rmse': round(float(np.sqrt((err ** 2).mean())), 4),
        'count': len(errors),
        'test_ratio': test_ratio,
        'k': k_neighbors,
        'method': method,
    }
