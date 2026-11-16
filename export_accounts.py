# -*- coding: utf-8 -*-
"""
导出全部账号清单为 CSV（不含明文密码）。

说明：users.password 存的是 werkzeug scrypt 加盐哈希，单向不可逆，
数据库里没有任何明文密码，所以无法"导出密码"。

用法：
    python export_accounts.py                 # 只导出清单
    python export_accounts.py --reset 677839  # 先把全部账号重置为该密码，再导出
"""
import csv
import sys
import datetime

from werkzeug.security import generate_password_hash

from utils.db import get_db_connection

OUT = 'accounts_export.csv'


def main():
    reset_pwd = None
    if '--reset' in sys.argv:
        idx = sys.argv.index('--reset')
        if idx + 1 < len(sys.argv):
            reset_pwd = sys.argv[idx + 1]

    conn = get_db_connection()
    cur = conn.cursor()

    if reset_pwd:
        # 统一哈希，避免逐行算 220 次（scrypt 很慢）
        h = generate_password_hash(reset_pwd)
        cur.execute("UPDATE users SET password = %s", (h,))
        conn.commit()
        print(f'已重置 {cur.rowcount} 个账号的密码')

    cur.execute("""
        SELECT u.id, u.username, u.role, u.merchant_name, u.merchant_status,
               u.is_active, u.created_at,
               (SELECT COUNT(*) FROM ratings  r WHERE r.user_id = u.id) AS ratings,
               (SELECT COUNT(*) FROM foods    f WHERE f.merchant_id = u.id) AS foods,
               (SELECT COUNT(*) FROM comments c WHERE c.user_id = u.id) AS comments
        FROM users u
        ORDER BY FIELD(u.role, 'admin', 'merchant', 'user'), u.id
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    with open(OUT, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['ID', '用户名', '角色', '密码', '商家名', '商家状态',
                    '账号可用', '注册时间', '评分数', '菜品数', '评论数'])
        for (uid, name, role, mname, mstatus, active, created,
             n_rate, n_food, n_cmt) in rows:
            w.writerow([
                uid, name, {'admin': '管理员', 'merchant': '商家', 'user': '普通用户'}.get(role, role),
                reset_pwd or '（scrypt 哈希，不可导出）',
                mname or '', mstatus or '', '是' if active else '否',
                created.strftime('%Y-%m-%d') if created else '',
                n_rate, n_food, n_cmt,
            ])

    print(f'已导出 {len(rows)} 个账号 -> {OUT}')


if __name__ == '__main__':
    main()
