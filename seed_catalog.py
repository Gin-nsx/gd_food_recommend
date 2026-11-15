# seed_catalog.py
# 将 catalog.py 中的分类、商家、菜品同步到数据库（可重复执行，幂等）
#
# 用法:
#   python seed_catalog.py
#
# 说明:
#   1. 补齐 12 个分类，并把旧版「一菜一分类」的菜品归并到新分类
#   2. 补齐商家账号（新建商家默认密码 123456，状态已通过）
#   3. 补齐菜品目录（按菜名判重，已存在则只校正分类）
#   4. 用 ratings 表真实均值重算 avg_rating，没有评分时取 0
import sys

import pymysql
from werkzeug.security import generate_password_hash

from catalog import CATEGORIES, CATEGORY_ALIAS, FOODS, MERCHANTS
from utils.db import get_db_connection


def get_or_create_categories(cur):
    cur.execute("SELECT id, name FROM categories")
    existing = {name: cid for cid, name in cur.fetchall()}

    for name in CATEGORIES:
        if name not in existing:
            cur.execute("INSERT INTO categories (name) VALUES (%s)", (name,))
            existing[name] = cur.lastrowid

    return existing


def remap_old_categories(cur):
    """把旧分类下的菜品迁移到新分类，然后删除空壳旧分类"""
    cur.execute("SELECT id, name FROM categories")
    rows = cur.fetchall()
    moved = 0
    removed = 0

    for old_name, new_name in CATEGORY_ALIAS.items():
        if old_name == new_name:
            continue

        old_ids = [cid for cid, name in rows if name == old_name]
        if not old_ids:
            continue

        cur.execute("SELECT id FROM categories WHERE name = %s", (new_name,))
        new_row = cur.fetchone()
        if not new_row:
            continue
        new_id = new_row[0]

        placeholders = ','.join(['%s'] * len(old_ids))
        cur.execute(
            f"UPDATE foods SET category_id = %s WHERE category_id IN ({placeholders})",
            [new_id] + old_ids
        )
        moved += cur.rowcount

        for old_id in old_ids:
            cur.execute("SELECT COUNT(*) FROM foods WHERE category_id = %s", (old_id,))
            if cur.fetchone()[0] == 0:
                cur.execute("DELETE FROM categories WHERE id = %s", (old_id,))
                removed += 1

    return moved, removed


def ensure_merchants(cur):
    cur.execute("SELECT id, username FROM users WHERE role = 'merchant'")
    existing = {name: uid for uid, name in cur.fetchall()}

    # 复用系统已有的商家顺序：陈记肠粉店 / 镛记烧鹅 / food1
    ids = []
    created = 0
    pwd_hash = generate_password_hash('123456')

    for name in MERCHANTS:
        if name in existing:
            ids.append(existing[name])
            continue

        cur.execute(
            """INSERT INTO users (username, password, role, merchant_name,
                                  merchant_status, is_active)
               VALUES (%s, %s, 'merchant', %s, 'approved', 1)""",
            (name, pwd_hash, name)
        )
        ids.append(cur.lastrowid)
        created += 1

    return ids, created


def ensure_foods(cur, category_map, merchant_ids):
    cur.execute("SELECT id, name, category_id FROM foods")
    existing = {name: (fid, cid) for fid, name, cid in cur.fetchall()}

    inserted = 0
    fixed = 0

    for name, cat, price, ingredients, desc, m_idx in FOODS:
        cat_id = category_map[cat]

        if name in existing:
            fid, cur_cat = existing[name]
            if cur_cat != cat_id:
                cur.execute("UPDATE foods SET category_id = %s WHERE id = %s", (cat_id, fid))
                fixed += 1
            continue

        cur.execute(
            """INSERT INTO foods (name, merchant_id, category_id, price,
                                  ingredients, description, avg_rating,
                                  like_count, status)
               VALUES (%s, %s, %s, %s, %s, %s, 0, 0, 'approved')""",
            (name, merchant_ids[m_idx], cat_id, price, ingredients, desc)
        )
        inserted += 1

    return inserted, fixed


def recompute_avg_rating(cur):
    """用 ratings 真实均值回填 avg_rating；无评分保持 0"""
    cur.execute("""
        UPDATE foods f
        LEFT JOIN (
            SELECT food_id, ROUND(AVG(score), 1) AS avg_score
            FROM ratings GROUP BY food_id
        ) r ON f.id = r.food_id
        SET f.avg_rating = COALESCE(r.avg_score, 0)
    """)
    return cur.rowcount


def main():
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        category_map = get_or_create_categories(cur)
        moved, removed = remap_old_categories(cur)
        merchant_ids, merchants_created = ensure_merchants(cur)
        inserted, fixed = ensure_foods(cur, category_map, merchant_ids)
        recompute_avg_rating(cur)

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM categories")
        cat_total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM foods")
        food_total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users WHERE role = 'merchant'")
        merchant_total = cur.fetchone()[0]

        print('分类: 共 %d 个（迁移菜品 %d 条，清理空分类 %d 个）' % (cat_total, moved, removed))
        print('商家: 共 %d 个（本次新建 %d 个）' % (merchant_total, merchants_created))
        print('菜品: 共 %d 道（本次新增 %d 道，校正分类 %d 道）' % (food_total, inserted, fixed))
        print('平均评分已按 ratings 真实均值重算')

    except Exception as e:
        conn.rollback()
        print('失败，已回滚:', type(e).__name__, e)
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
