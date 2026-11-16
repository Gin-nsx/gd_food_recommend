# seed_comments.py
# 重新生成模拟评论：按「菜品分类 + 菜名关键词」匹配评论池，保证评论与菜对得上。
#
# 背景：
#   旧模拟评论来自一个约 10 句的通用模板池，随机分配给所有菜品，
#   导致「鲜虾腐皮卷」下面出现「粥底绵密」「糖水甜度适中」这种张冠李戴的评论。
#
# 生成规则：
#   1. 先删掉所有 mock 用户的旧评论（真实用户的评论保留）；
#   2. 每道菜抽 2~4 条评论，来源优先级：菜名关键词专属池 > 分类池 > 通用池；
#   3. 同一道菜内评论不重复、同一用户不在同一道菜下重复发言；
#   4. 评论时间散布在近 300 天内，可复现（固定随机种子）。
#
# 用法:
#   python seed_comments.py             # 重新生成（默认 200 个 mock 用户池）
#   python seed_comments.py --seed 7    # 换一批评论
import argparse
import random
import sys
from datetime import datetime, timedelta

from utils.db import get_db_connection

MOCK_PREFIX = 'mock_'

# ========== 1. 菜名关键词专属池（命中菜名即优先使用） ==========
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

# ========== 2. 分类通用池 ==========
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

# ========== 3. 全菜通用的中性评价（兜底） ==========
GENERIC_LINES = [
    '出品稳定，价格对得起品质。',
    '带外地朋友来吃，都说比想象中好吃。',
    '环境一般，但胜在味道正宗。',
    '会回购，已经推荐给同事了。',
    '价格实惠，出品稳定，隔几天就想吃。',
    '份量有一点少，但味道确实地道。',
    '排队有点久，味道对得起这个等待。',
    '比想象中好，下次还会来。',
    '老字号的味道，吃了很多年没变过。',
    '发到家庭群，爸妈都问是哪家。',
]

# 低分向的吐槽（少量混入，避免全是彩虹屁）
NEGATIVE_LINES = [
    '偏咸了一点，下次让师傅少放盐。',
    '等了挺久的，希望出餐能再快一点。',
    '今天出品比之前差一点，可能是高峰期。',
    '希望份量能再大一点。',
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


def pick_lines(name, cat, rng, n):
    """为一道菜挑 n 条互不重复的评论：专属池 > 分类池 > 通用池，混入少量吐槽"""
    dish_pool = []
    for key, lines in DISH_LINES.items():
        if key in name:
            dish_pool.extend(lines)
    cat_pool = CATEGORY_LINES.get(cat, [])
    gen_pool = GENERIC_LINES

    chosen, used = [], set()

    def take(pool, k):
        cand = [l for l in pool if l not in used]
        rng.shuffle(cand)
        for l in cand[:k]:
            used.add(l)
            chosen.append(l)

    # 约 60% 概率带 1 条专属（有专属池时），其余从分类/通用池补
    if dish_pool:
        take(dish_pool, min(len(dish_pool), rng.randint(1, 2)))
    take(cat_pool, rng.randint(1, 2))
    take(gen_pool, max(0, n - len(chosen)))
    if len(chosen) < n:
        take(dish_pool, n - len(chosen))
    if len(chosen) < n:
        take(gen_pool, n - len(chosen))

    # 10% 概率把最后一条换成吐槽
    if chosen and rng.random() < 0.10:
        chosen[-1] = rng.choice(NEGATIVE_LINES)

    return chosen[:max(1, n)]


def main():
    parser = argparse.ArgumentParser(description='按菜品重新生成模拟评论')
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

        # 只删 mock 用户的旧评论，真实用户评论保留
        placeholders = ','.join(['%s'] * len(mock_ids))
        cur.execute(f"DELETE FROM comments WHERE user_id IN ({placeholders})", mock_ids)
        deleted = cur.rowcount

        foods = load_foods(cur)
        now = datetime.now()
        rows = []
        for fid, name, cat in foods:
            n = rng.randint(args.min, args.max)
            lines = pick_lines(name, cat or '', rng, n)
            # 随机挑作者，同一用户不会在同道菜下出现两次
            authors = rng.sample(mock_ids, min(len(mock_ids), len(lines)))
            for uid, text in zip(authors, lines):
                created = now - timedelta(days=rng.randint(0, 300),
                                          hours=rng.randint(0, 23),
                                          minutes=rng.randint(0, 59))
                rows.append((uid, fid, text, created))

        cur.executemany(
            "INSERT INTO comments (user_id, food_id, content, created_at) VALUES (%s, %s, %s, %s)",
            rows,
        )
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM comments")
        total = cur.fetchone()[0]
        print(f'已删除旧模拟评论 {deleted} 条')
        print(f'已生成新评论 {len(rows)} 条，表内共 {total} 条')
        print(f'覆盖菜品 {len(foods)} 道')

    except Exception as e:
        conn.rollback()
        print('失败，已回滚:', type(e).__name__, e)
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
