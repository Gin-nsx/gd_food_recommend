# analyze_users.py
# 输出用户偏好画像：每个用户的评分/点赞/收藏/评论习惯 + 口味偏好分布 + 分层汇总
#
# 用法:
#   python analyze_users.py                # 生成 用户偏好画像.xlsx
#   python analyze_users.py --out xx.xlsx
import argparse
from collections import Counter, defaultdict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from utils.db import get_db_connection

MOCK_PREFIX = 'mock_'


def fetch(conn):
    cur = conn.cursor()

    # 用户基础
    cur.execute("""
        SELECT u.id, u.username, u.allergy, u.taboo
        FROM users u WHERE u.username LIKE %s ORDER BY u.id
    """, (MOCK_PREFIX + '%',))
    users = {r[0]: {'username': r[1], 'allergy': r[2], 'taboo': r[3]} for r in cur.fetchall()}

    # 评分明细（含分类）
    cur.execute("""
        SELECT r.user_id, r.score, f.category_id, c.name, r.food_id
        FROM ratings r
        JOIN foods f ON r.food_id = f.id
        LEFT JOIN categories c ON f.category_id = c.id
        JOIN users u ON r.user_id = u.id
        WHERE u.username LIKE %s
    """, (MOCK_PREFIX + '%',))
    ratings = cur.fetchall()

    # 点赞 / 收藏 / 评论
    for table, key in (('likes', 'likes'), ('favorites', 'favs'), ('comments', 'comments'),
                       ('histories', 'views')):
        cur.execute(f"""
            SELECT x.user_id, COUNT(*) FROM {table} x
            JOIN users u ON x.user_id = u.id
            WHERE u.username LIKE %s GROUP BY x.user_id
        """, (MOCK_PREFIX + '%',))
        for uid, n in cur.fetchall():
            if uid in users:
                users[uid][key] = n

    # 评论情绪（按该用户对这道菜的评分判定）
    cur.execute("""
        SELECT cm.user_id,
               SUM(CASE WHEN r.score >= 4 THEN 1 ELSE 0 END),
               SUM(CASE WHEN r.score = 3 THEN 1 ELSE 0 END),
               SUM(CASE WHEN r.score <= 2 THEN 1 ELSE 0 END)
        FROM comments cm
        JOIN ratings r ON r.user_id = cm.user_id AND r.food_id = cm.food_id
        JOIN users u ON cm.user_id = u.id
        WHERE u.username LIKE %s
        GROUP BY cm.user_id
    """, (MOCK_PREFIX + '%',))
    for uid, pos, mid, neg in cur.fetchall():
        if uid in users:
            users[uid]['c_pos'], users[uid]['c_mid'], users[uid]['c_neg'] = int(pos), int(mid), int(neg)

    # 分类维度：均分、评分数、点赞、收藏、PV、评论
    cur.execute("""
        SELECT c.id, c.name,
               COUNT(DISTINCT r.id),
               ROUND(AVG(r.score), 2),
               COUNT(DISTINCT l.id),
               COUNT(DISTINCT fv.id),
               COUNT(DISTINCT cm.id),
               (SELECT COUNT(*) FROM foods f2 WHERE f2.category_id = c.id AND f2.status='approved')
        FROM categories c
        LEFT JOIN foods f ON f.category_id = c.id AND f.status = 'approved'
        LEFT JOIN ratings r ON r.food_id = f.id
        LEFT JOIN likes l ON l.food_id = f.id
        LEFT JOIN food_views fv ON fv.food_id = f.id
        LEFT JOIN comments cm ON cm.food_id = f.id
        GROUP BY c.id, c.name
        ORDER BY c.id
    """)
    cats = cur.fetchall()
    cur.close()
    return users, ratings, cats


def build_profiles(users, ratings):
    per_user_cat = defaultdict(Counter)
    per_user_cat_sum = defaultdict(lambda: defaultdict(int))
    per_user_scores = defaultdict(list)

    for uid, score, cid, cname, _fid in ratings:
        if uid not in users:
            continue
        per_user_cat[uid][cname] += 1
        per_user_cat_sum[uid][cname] += score
        per_user_scores[uid].append(score)

    rows = []
    for uid, u in users.items():
        scores = per_user_scores.get(uid, [])
        n = len(scores)
        avg = round(sum(scores) / n, 2) if n else 0
        likes = u.get('likes', 0)
        favs = u.get('favs', 0)
        comments = u.get('comments', 0)

        # 口味偏好：按评分次数排序，均分做参考
        pref = []
        for cname, cnt in per_user_cat[uid].most_common(3):
            c_avg = round(per_user_cat_sum[uid][cname] / cnt, 2)
            pref.append((cname, cnt, c_avg))
        top1_share = (pref[0][1] / n) if pref and n else 0

        rows.append({
            'uid': uid,
            'username': u['username'],
            'n': n,
            'avg': avg,
            'high_ratio': round(sum(1 for s in scores if s >= 4) / n, 3) if n else 0,
            'cats': len(per_user_cat[uid]),
            'pref': pref,
            'top1_share': round(top1_share, 3),
            'likes': likes,
            'favs': favs,
            'comments': comments,
            'c_pos': u.get('c_pos', 0),
            'c_mid': u.get('c_mid', 0),
            'c_neg': u.get('c_neg', 0),
            'views': u.get('views', 0),
            'allergy': u['allergy'] or '',
            'taboo': u['taboo'] or '',
            'like_rate': round(likes / n, 2) if n else 0,
            'fav_rate': round(favs / n, 2) if n else 0,
        })
    return rows


def tier(row):
    if row['n'] >= 30:
        return '重度（≥30 条评分）'
    if row['n'] >= 18:
        return '中度（18~29 条）'
    return '轻度（<18 条）'


def style_header(ws, widths):
    fill = PatternFill('solid', fgColor='A23A25')
    for idx, w in enumerate(widths, start=1):
        cell = ws.cell(row=1, column=idx)
        cell.font = Font(bold=True, color='FFFFFF', size=11)
        cell.fill = fill
        cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.column_dimensions[get_column_letter(idx)].width = w
    ws.freeze_panes = 'A2'
    ws.row_dimensions[1].height = 24


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='用户偏好画像.xlsx')
    args = parser.parse_args()

    conn = get_db_connection()
    try:
        users, ratings, cats = fetch(conn)
    finally:
        conn.close()

    profiles = build_profiles(users, ratings)
    profiles.sort(key=lambda r: -r['n'])

    wb = Workbook()

    # ---------- Sheet1 用户画像 ----------
    ws = wb.active
    ws.title = '用户画像'
    headers = ['用户', '评分数', '均分', '好评率(≥4★)', '评过的分类数',
               '最常评的分类', '该分类条数', '该分类均分',
               '第二偏好', '第三偏好', '偏好集中度',
               '点赞数', '点赞率', '收藏数', '收藏率',
               '评论数', '好评', '中评', '差评', '浏览数', '活跃度', '过敏/忌口']
    ws.append(headers)
    style_header(ws, [12, 8, 7, 12, 12, 14, 11, 11, 12, 12, 10, 8, 8, 8, 8, 8, 7, 7, 7, 8, 18, 14])

    for r in profiles:
        pref = r['pref'] + [('', '', '')] * (3 - len(r['pref']))
        ws.append([
            r['username'], r['n'], r['avg'], r['high_ratio'], r['cats'],
            pref[0][0], pref[0][1], pref[0][2],
            f'{pref[1][0]}（{pref[1][1]}）' if pref[1][0] else '',
            f'{pref[2][0]}（{pref[2][1]}）' if pref[2][0] else '',
            r['top1_share'],
            r['likes'], r['like_rate'], r['favs'], r['fav_rate'],
            r['comments'], r['c_pos'], r['c_mid'], r['c_neg'], r['views'],
            tier(r),
            '、'.join(x for x in (r['allergy'], r['taboo']) if x) or '无',
        ])

    # ---------- Sheet2 分类偏好 ----------
    ws2 = wb.create_sheet('分类偏好')
    ws2.append(['分类', '在架菜品', '评分数', '平均分', '点赞数', '点赞/评分',
                '浏览 PV', '评论数', '热度指数'])
    style_header(ws2, [14, 10, 9, 9, 9, 11, 10, 9, 10])
    for cid, name, n_rating, avg, n_like, n_view, n_cmt, n_food in cats:
        like_ratio = round((n_like or 0) / n_rating, 2) if n_rating else 0
        heat = round((n_view or 0) / 100 + (n_like or 0) * 0.5 + (n_cmt or 0) * 1.5, 1)
        ws2.append([name, n_food, n_rating, float(avg or 0), n_like or 0, like_ratio,
                    n_view or 0, n_cmt or 0, heat])

    # ---------- Sheet3 分层汇总 ----------
    ws3 = wb.create_sheet('分层汇总')
    ws3.append(['用户分层', '人数', '人均评分', '人均点赞', '人均收藏', '人均评论',
                '平均好评率', '偏好集中度均值'])
    style_header(ws3, [20, 8, 10, 10, 10, 10, 12, 14])
    tiers = defaultdict(list)
    for r in profiles:
        tiers[tier(r)].append(r)
    for name in ('重度（≥30 条评分）', '中度（18~29 条）', '轻度（<18 条）'):
        group = tiers.get(name, [])
        if not group:
            continue
        k = len(group)
        ws3.append([
            name, k,
            round(sum(g['n'] for g in group) / k, 1),
            round(sum(g['likes'] for g in group) / k, 1),
            round(sum(g['favs'] for g in group) / k, 1),
            round(sum(g['comments'] for g in group) / k, 1),
            round(sum(g['high_ratio'] for g in group) / k, 3),
            round(sum(g['top1_share'] for g in group) / k, 3),
        ])

    # 打印结论
    total = len(profiles)
    print(f'用户数 {total}')
    print('评分: 总数 %d，人均 %.1f，均分 %.2f' % (
        sum(p['n'] for p in profiles), sum(p['n'] for p in profiles) / total,
        sum(p['avg'] * p['n'] for p in profiles) / sum(p['n'] for p in profiles)))
    print('人均点赞 %.1f / 收藏 %.1f / 评论 %.1f / 浏览 %.1f' % (
        sum(p['likes'] for p in profiles) / total,
        sum(p['favs'] for p in profiles) / total,
        sum(p['comments'] for p in profiles) / total,
        sum(p['views'] for p in profiles) / total))
    print('分层:', {k: len(v) for k, v in tiers.items()})
    print('评论情绪: 好评 %d / 中评 %d / 差评 %d' % (
        sum(p['c_pos'] for p in profiles), sum(p['c_mid'] for p in profiles),
        sum(p['c_neg'] for p in profiles)))

    top_cats = Counter()
    for p in profiles:
        for cname, cnt, _avg in p['pref']:
            top_cats[cname] += 1
    print('最爱分类分布（按每人 Top1）:', top_cats.most_common())

    for p in profiles[:5]:
        print(f"  {p['username']}: {p['n']} 评 {p['avg']} 分｜" +
              '、'.join(f'{c}({n}条/{a}分)' for c, n, a in p['pref']) +
              f"｜赞{p['likes']} 藏{p['favs']} 评{p['comments']}")

    wb.save(args.out)
    print('已生成', args.out)


if __name__ == '__main__':
    main()
