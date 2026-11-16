# seed_comments.py
# 重新生成模拟评论：按「菜品分类 + 菜名关键词」匹配文本，并让评论作者、情绪与评分一致。
#
# 背景：
#   最早的模拟评论来自一个约 10 句的通用模板池，随机分配给所有菜品，
#   导致「鲜虾腐皮卷」下面出现「粥底绵密」「糖水甜度适中」这种张冠李戴的评论；
#   后来虽然按菜名匹配了文本，但作者仍是随机挑的，会出现「打了 2 分却在评论里夸」。
#
# 生成规则：
#   1. 作者只能从「给这道菜打过分」的用户里挑，权重随分数升高（好评更常见）；
#   2. 文本按作者分数分层：4~5★ 用推荐语，3★ 用中评，1~2★ 用吐槽；
#   3. 文本来源优先级：菜名关键词专属池 > 分类池 > 通用池，同菜不重复；
#   4. 评论时间 = 该用户的评分时间 + 2~72 小时，保证「先评分后评论」；
#   5. 只删 mock 用户的旧评论，真实用户评论保留。
#
# 用法:
#   python seed_comments.py             # 重新生成
#   python seed_comments.py --seed 7    # 换一批
import argparse
import random
import sys
from datetime import datetime, timedelta

from utils.db import get_db_connection

MOCK_PREFIX = 'mock_'

# ========== 1. 菜名关键词专属池（命中菜名即优先使用，均为好评） ==========
DISH_LINES = {
    '烧鹅':   ['烧鹅皮脆肉嫩，配酸梅酱刚刚好。', '斩件利落，皮下的油不多，烧得透。', '例牌烧鹅够热气，蘸酱不抢味。'],
    '叉烧':   ['叉烧半肥瘦，蜜香渗进肉里。', '边缘带一点焦香，甜咸平衡。', '蜜汁叉烧够入味，配白饭一绝。'],
    '烧肉':   ['脆皮烧肉表皮爆得酥脆，肥肉层薄。', '趁热吃咔咔响，蘸芥末酱油更提味。'],
    '烧排骨': ['蜜汁烧排骨焦香带甜，啃到停不下来。', '排骨腌得透，炭火味很足。'],
    '豉油鸡': ['豉油鸡滑嫩入味，皮色红亮。', '酱油配方老广味，鸡肉不柴。'],
    '卤水鹅': ['卤水香而不咸，鹅掌焖得软糯胶质足。', '卤水的八角味正，越嚼越香。'],
    '卤鹅肝': ['卤鹅肝粉糯香浓，一点不腥。', '潮汕卤鹅肝名不虚传，配酒一绝。'],
    '虾饺':   ['虾饺皮薄透亮，一只虾饺一只虾。', '笋干和虾仁的比例刚好，鲜甜爽口。'],
    '烧卖':   ['干蒸烧卖肉紧实，蟹籽在嘴里爆开。', '猪肉鲜甜，猪肉味够浓。'],
    '凤爪':   ['豉汁凤爪蒸得脱骨，吸满豉汁。', '花生垫底也很入味，一盅不够吃。'],
    '糯米鸡': ['糯米鸡荷叶香扑鼻，糯米软糯不粘牙。', '拆开荷叶热气腾腾，鸡肉给得实在。'],
    '流沙包': ['流沙包趁热掰开爆浆，咸蛋黄香浓。', '流沙顺滑不齁甜，趁热吃最惊艳。'],
    '叉烧包': ['叉烧包皮松软，馅料甜咸适中。', '老式发面，包子雪白松软。'],
    '蛋挞':   ['挞皮起酥层次分明，蛋心嫩得会晃。', '葡挞焦糖斑点漂亮，不甜腻。'],
    '萝卜糕': ['萝卜糕煎到底面金黄，萝卜味很足。', '糕体松化，腊味粒给得大方。'],
    '马蹄糕': ['马蹄糕弹牙清甜，马蹄粒爽脆。', '煎过之后外脆内弹，茶楼水平在线。'],
    '腐皮卷': ['腐皮炸得酥脆，里面虾肉弹嫩多汁。', '外酥里嫩，蘸番茄酱很开胃。', '腐皮卷层次分明，一口下去咔嚓响。'],
    '肠粉':   ['粉皮薄得透光，酱油咸鲜带甜，很广州。', '现拉现蒸，上桌还冒着热气。', '米香很足，滑而不腻，早餐首选。', '一碟不够吃，酱油可以再多淋一点。'],
    '粥':     ['粥底绵密，生滚的火候刚好。', '料给得足，一碗下肚很暖。', '米粒化开但不糊，粥水清甜。'],
    '煲仔饭': ['锅巴金黄脆香，就是等得有点久。', '豉油捞饭一绝，镬气十足。', '冬天吃一煲热腾腾的煲仔饭最幸福。'],
    '云吞面': ['云吞里藏着一整只虾，细面碱水香。', '汤底大地鱼味够浓，面弹牙。'],
    '捞面':   ['捞面酱汁均匀，面条根根分明。', '牛腩焖得软烂，捞面镬气足。'],
    '牛河':   ['干炒牛河根根分明，不油不腻镬气爆表。', '河粉完整不断，牛肉嫩滑。'],
    '炒面':   ['豉油皇炒面镬气够，面条焦香。', '简单但到位，出品稳定。'],
    '姜撞奶': ['姜味够劲，奶冻凝固得恰到好处。', '暖胃，冬天来一碗从喉咙暖到胃。'],
    '双皮奶': ['双皮奶奶皮厚实，勺子舀起来会颤。', '奶味醇正不齁，水牛奶做的才够香。'],
    '芝麻糊': ['芝麻糊磨得细腻，香而不涩。', '黑芝麻香浓，甜度刚好。'],
    '红豆沙': ['红豆沙起沙绵密，陈皮味点到即止。', '煲得够久，豆都化了。'],
    '西米露': ['西米弹牙，芒果够甜。', '椰浆香浓，冰凉解腻。'],
    '甘露':   ['杨枝甘露料足，芒果西柚比例正好。', '酸甜平衡，一勺下去满口果香。'],
    '白切鸡': ['鸡皮爽肉滑，蘸姜葱蓉正好。', '骨头带血丝才算够嫩，火候准。'],
    '清蒸':   ['清蒸火候刚好，鱼肉嫩滑不腥。', '豉油和葱丝配得对，鲜味全锁住。'],
    '咕噜肉': ['咕噜肉外脆里嫩，糖醋汁开胃。', '酸甜度经典，菠萝解腻。'],
    '老火汤': ['老火汤够料，喝了两碗还想喝。', '霸王花炖得化，汤水清润。'],
    '蒸排骨': ['豉汁蒸排骨豉香浓郁，排骨嫩滑。', '垫底的花生比排骨还抢手。'],
    '蒜香骨': ['蒜香骨外酥里嫩，蒜香霸道。', '炸得透，骨头都想嗦一下。'],
    '牛肉丸': ['牛肉丸弹牙到能打乒乓球，牛味浓。', '手打的真的不一样，紧实爆汁。'],
    '蚝烙':   ['蚝烙外脆内嫩，蘸鱼露绝配。', '生蚝肥美，鸭蛋香够足。'],
    '牛肉火锅': ['牛肉现切现涮，吊龙嫩、匙柄弹。', '沙茶酱是灵魂，配牛肉绝了。'],
    '豆干':   ['普宁豆干外脆内嫩，蘸韭菜盐水正。', '炸得金黄，豆香味足。'],
    '砂锅粥': ['砂锅粥米水交融，虾蟹的鲜都熬进粥里。', '粥底香浓，一锅两人分正好。'],
    '梅菜扣肉': ['梅菜吸饱肉汁，扣肉肥而不腻。', '咸香下饭，配米饭能吃三碗。'],
    '酿豆腐': ['酿豆腐煎得金黄，肉馅紧实多汁。', '客家做法正宗，豆味浓。'],
    '盐焗鸡': ['盐焗鸡咸香入骨，手撕着吃更有味。', '鸡皮爽脆，盐焗粉用得地道。'],
    '猪肚鸡': ['猪肚鸡胡椒味够劲，汤喝到冒汗。', '猪肚脆嫩，汤底浓白。'],
    '黄酒鸡': ['黄酒香渗进鸡肉，微醺的甜。', '客家娘酒煮得正宗，暖身。'],
    '基围虾': ['白灼最能吃出鲜味，虾肉弹牙。', '虾很鲜活，蘸酱油和白灼水正。'],
    '生蚝':   ['蒜蓉蒸生蚝肥美多汁，蒜香到位。', '蚝够大只，粉丝垫底都吸满蒜香。'],
    '濑尿虾': ['椒盐濑尿虾壳脆肉甜，停不下来。', '椒盐味够香，剥壳也值得。'],
    '炒蟹':   ['姜葱炒蟹镬气足，蟹肉鲜甜。', '蟹膏不少，姜葱味渗进蟹壳里。'],
    '花甲':   ['豉椒炒花甲开口率高，沙吐得干净。', '酱香浓郁，花甲鲜嫩。'],
    '石斑鱼': ['石斑鱼清蒸得嫩，鱼鳞胶满满胶质。', '鱼够新鲜，一筷子下去都是蒜瓣肉。'],
    '凉茶':   ['地道凉茶味，苦中回甘。', '上火喝一杯，第二天喉咙舒服多了。'],
    '柠茶':   ['冻柠茶茶味够浓，柠檬腌得入味。', '港式风味，甜度可调。'],
    '蔗汁':   ['鲜榨蔗汁清甜无渣，冰过更爽。', '不加水的甜，解腻神器。'],
}

# ========== 2. 分类好评池 ==========
CATEGORY_LINES = {
    '肠粉':     ['粉皮滑嫩，酱油是灵魂。', '茶楼水平，米香足不粘腻。'],
    '粥品':     ['粥够绵，暖胃舒服。', '一碗热粥落肚，整个人都舒展了。'],
    '煲仔饭':   ['煲仔饭豉油正，锅巴是精华。', '明火煲出来的饭有饭焦香。'],
    '烧腊':     ['斩料加钱都值，出品对得起招牌。', '烧腊档的香气隔着玻璃都闻到。'],
    '早茶点心': ['一盅两件的快乐，点心出品在线。', '点心够热够新鲜，茶位费都值了。'],
    '粉面':     ['汤粉面出品稳定，出品快不等位。', '面条弹牙汤底清，一碗很满足。'],
    '糖水甜品': ['甜度适中，不会腻。', '饭后一碗糖水，这顿饭才算完整。'],
    '广府菜':   ['广府菜讲究镬气和火候，这家到位。', '粤菜做得清而不淡，鲜而不俗。'],
    '潮汕菜':   ['食材新鲜，做法很潮汕。', '蘸料是灵魂，口味很正。'],
    '客家菜':   ['咸香入味，非常下饭。', '分量实在，家常味道，实惠。'],
    '海鲜':     ['现捞现做，鲜甜弹牙。', '海鲜够鲜，白灼清蒸最考验功夫。'],
    '饮品':     ['解腻解辣，饭后来一杯正好。', '老广的味道，从小喝到大。'],
}

# ========== 3. 分类中评池（3★ 用） ==========
CATEGORY_NEUTRAL = {
    '肠粉':     ['味道中规中矩，当早餐还行。', '粉皮略厚，酱油偏咸。'],
    '粥品':     ['粥底还行，就是料比想象中少。', '味道偏淡，需要自己加胡椒。'],
    '煲仔饭':   ['饭焦不错，就是出餐慢了点。', '味道可以，饭有点偏硬。'],
    '烧腊':     ['味道过得去，就是斩件有点碎。', '烧得还行，皮不够脆。'],
    '早茶点心': ['点心一般，胜在够热。', '出品稳定，谈不上惊喜。'],
    '粉面':     ['汤头淡了点，面还行。', '分量一般，价格不算便宜。'],
    '糖水甜品': ['甜度偏甜，胜在够冰。', '分量不大，味道还行。'],
    '广府菜':   ['中规中矩的粤菜，环境加分。', '火候还行，调味偏淡。'],
    '潮汕菜':   ['食材还行，调味偏咸。', '做法地道，分量略少。'],
    '客家菜':   ['口味偏咸，胜在下饭。', '家常水平，没有特别惊喜。'],
    '海鲜':     ['新鲜度一般，价格还行。', '处理得干净，个头偏小。'],
    '饮品':     ['味道普通，解渴够用。', '偏甜，冰放得有点多。'],
}

# ========== 4. 通用池 ==========
GENERIC_POSITIVE = [
    '出品稳定，价格对得起品质。',
    '带外地朋友来吃，都说比想象中好吃。',
    '会回购，已经推荐给同事了。',
    '价格实惠，出品稳定，隔几天就想吃。',
    '比想象中好，下次还会来。',
    '老字号的味道，吃了很多年没变过。',
    '发到家庭群，爸妈都问是哪家。',
]
GENERIC_NEUTRAL = [
    '环境一般，但胜在味道正宗。',
    '份量有一点少，但味道确实地道。',
    '排队有点久，味道算对得起这个等待。',
    '整体中规中矩，适合路过随便吃。',
]
NEGATIVE_LINES = [
    '偏咸了一点，下次让师傅少放盐。',
    '等了挺久的，希望出餐能再快一点。',
    '今天出品比之前差一点，可能是高峰期。',
    '希望份量能再大一点，性价比一般。',
    '不太新鲜，吃了一半就不想吃了。',
    '和图片差得有点多，有点失望。',
    '口味太重，盖住了食材本身的鲜味。',
]


def load_mock_users(cur):
    cur.execute("SELECT id FROM users WHERE username LIKE %s", (MOCK_PREFIX + '%',))
    return [r[0] for r in cur.fetchall()]


def load_foods(cur):
    cur.execute("""
        SELECT f.id, f.name, c.name
        FROM foods f LEFT JOIN categories c ON f.category_id = c.id
        WHERE f.status = 'approved'
        ORDER BY f.id
    """)
    return cur.fetchall()


def load_ratings_by_food(cur):
    """{food_id: [(user_id, score, rated_at), ...]}，用于挑评论作者"""
    cur.execute("""
        SELECT r.user_id, r.food_id, r.score, r.created_at
        FROM ratings r JOIN users u ON r.user_id = u.id
        WHERE u.username LIKE %s
    """, (MOCK_PREFIX + '%',))
    out = {}
    for uid, fid, score, ts in cur.fetchall():
        out.setdefault(fid, []).append((uid, score, ts))
    return out


def dish_pool(name):
    pool = []
    for key, lines in DISH_LINES.items():
        if key in name:
            pool.extend(lines)
    return pool


def pick_text(name, cat, score, used, rng):
    """按分数选文本：4~5★ 好评，3★ 中评，1~2★ 吐槽；同菜内不重复"""
    if score <= 2:
        cand = [l for l in NEGATIVE_LINES if l not in used]
    elif score == 3:
        cand = [l for l in (CATEGORY_NEUTRAL.get(cat, []) + GENERIC_NEUTRAL) if l not in used]
    else:
        cand = [l for l in (dish_pool(name) + CATEGORY_LINES.get(cat, []) + GENERIC_POSITIVE)
                if l not in used]
        # 好评池优先专属描述，避免整屏都是「出品稳定」
        rare = [l for l in cand if l in dish_pool(name)]
        if rare and rng.random() < 0.75:
            cand = rare
    if not cand:
        cand = [l for l in NEGATIVE_LINES + GENERIC_NEUTRAL + GENERIC_POSITIVE if l not in used]
    return rng.choice(cand) if cand else None


def main():
    parser = argparse.ArgumentParser(description='按菜品与评分重新生成模拟评论')
    parser.add_argument('--seed', type=int, default=2026, help='随机种子（可复现）')
    parser.add_argument('--min', type=int, default=2, help='每道菜最少评论数')
    parser.add_argument('--max', type=int, default=4, help='每道菜最多评论数')
    args = parser.parse_args()

    conn = get_db_connection()
    cur = conn.cursor()
    rng = random.Random(args.seed)

    try:
        mock_ids = load_mock_users(cur)
        if not mock_ids:
            print('没有找到 mock 用户，请先运行 generate_mock_data.py')
            sys.exit(1)

        ph = ','.join(['%s'] * len(mock_ids))
        cur.execute(f"DELETE FROM comments WHERE user_id IN ({ph})", mock_ids)
        deleted = cur.rowcount

        ratings_by_food = load_ratings_by_food(cur)
        now = datetime.now()
        rows, skipped = [], 0

        for fid, name, cat in load_foods(cur):
            raters = ratings_by_food.get(fid) or []
            if not raters:
                skipped += 1
                continue

            n = min(len(raters), rng.randint(args.min, args.max))
            # 作者按分数加权抽取：好评者更可能留下评论
            pool = list(raters)
            picked, used = [], set()
            for _ in range(n):
                weights = [max(1, s) ** 1.6 for _u, s, _t in pool]
                idx = rng.choices(range(len(pool)), weights=weights, k=1)[0]
                uid, score, rated_at = pool.pop(idx)
                text = pick_text(name, cat or '', score, used, rng)
                if not text:
                    continue
                used.add(text)
                created = rated_at + timedelta(hours=rng.randint(2, 72),
                                               minutes=rng.randint(0, 59))
                if created > now:
                    created = rated_at
                picked.append((uid, fid, text, created))
            rows.extend(picked)

        cur.executemany(
            "INSERT INTO comments (user_id, food_id, content, created_at) VALUES (%s, %s, %s, %s)",
            rows,
        )
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM comments")
        total = cur.fetchone()[0]
        print(f'已删除旧模拟评论 {deleted} 条')
        print(f'已生成新评论 {len(rows)} 条，表内共 {total} 条')
        if skipped:
            print(f'跳过 {skipped} 道无人评分的菜品（无作者可挑）')

    except Exception as e:
        conn.rollback()
        print('失败，已回滚:', type(e).__name__, e)
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
