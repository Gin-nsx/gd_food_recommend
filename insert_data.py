import pymysql
from config import DB_CONFIG
from werkzeug.security import generate_password_hash


def insert_data():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    try:
        # 1. 插入分类
        categories = [
            ('肠粉',),
            ('烧鹅',),
            ('糖水',),
            ('点心',),
            ('粥品',),
            ('煲仔饭',),
            ('云吞面',),
            ('早茶',)
        ]
        cursor.executemany("INSERT IGNORE INTO categories (name) VALUES (%s)", categories)
        print("✅ 分类插入成功")

        # 2. 插入用户（密码统一为 123456）
        # 管理员
        cursor.execute("""
            INSERT IGNORE INTO users (username, password, role, is_active) 
            VALUES (%s, %s, %s, %s)
        """, ('admin', generate_password_hash('123456'), 'admin', True))

        # 普通用户
        users = [
            ('user1', generate_password_hash('123456'), 'user', True),
            ('user2', generate_password_hash('123456'), 'user', True),
            ('user3', generate_password_hash('123456'), 'user', True),
        ]
        cursor.executemany(
            "INSERT IGNORE INTO users (username, password, role, is_active) VALUES (%s, %s, %s, %s)",
            users
        )

        # 商家
        merchants = [
            ('陈记肠粉店', generate_password_hash('123456'), 'merchant', True, '荔湾区龙津路123号', 'approved'),
            ('镛记烧鹅', generate_password_hash('123456'), 'merchant', True, '越秀区北京路456号', 'approved'),
        ]
        for m in merchants:
            cursor.execute("""
                INSERT IGNORE INTO users (username, password, role, is_active, merchant_address, merchant_status) 
                VALUES (%s, %s, %s, %s, %s, %s)
            """, m)
        print("✅ 用户插入成功")

        # 3. 查询id用于关联
        cursor.execute("SELECT id FROM users WHERE username = '陈记肠粉店'")
        merchant1 = cursor.fetchone()
        cursor.execute("SELECT id FROM users WHERE username = '镛记烧鹅'")
        merchant2 = cursor.fetchone()
        cursor.execute("SELECT id FROM categories WHERE name = '肠粉'")
        cat1 = cursor.fetchone()
        cursor.execute("SELECT id FROM categories WHERE name = '烧鹅'")
        cat2 = cursor.fetchone()
        cursor.execute("SELECT id FROM categories WHERE name = '糖水'")
        cat3 = cursor.fetchone()
        cursor.execute("SELECT id FROM categories WHERE name = '点心'")
        cat4 = cursor.fetchone()
        cursor.execute("SELECT id FROM categories WHERE name = '粥品'")
        cat5 = cursor.fetchone()
        cursor.execute("SELECT id FROM categories WHERE name = '煲仔饭'")
        cat6 = cursor.fetchone()

        foods = [
            ('秘制叉烧肠粉', merchant1[0], cat1[0], 15, '肠粉、叉烧、特制酱汁、生菜', '咸甜适中，叉烧肥瘦相间', 'food1.jpg',
             4.8, 0, 'approved'),
            ('鲜虾肠粉', merchant1[0], cat1[0], 18, '肠粉、鲜虾、韭黄', '虾肉鲜甜，肠粉皮薄', 'food2.jpg', 4.7, 0,
             'approved'),
            ('牛肉肠粉', merchant1[0], cat1[0], 16, '肠粉、牛肉、生菜', '牛肉嫩滑，酱汁浓郁', 'food3.jpg', 4.6, 0,
             'approved'),
            ('深井烧鹅', merchant2[0], cat2[0], 68, '黑鬃鹅、秘制酱料', '皮脆肉嫩，汁水丰富', 'food4.jpg', 4.9, 0,
             'approved'),
            (
            '蜜汁叉烧', merchant2[0], cat2[0], 58, '梅头肉、蜜汁', '甜咸适中，外焦里嫩', 'food5.jpg', 4.7, 0, 'approved'),
            ('杨枝甘露', merchant2[0], cat3[0], 25, '芒果、西柚、椰奶、西米', '酸甜清爽，层次丰富', 'food6.jpg', 4.8, 0,
             'approved'),
            ('虾饺', merchant2[0], cat4[0], 38, '虾仁、笋丁、澄面', '皮薄馅多，鲜甜弹牙', 'food7.jpg', 4.9, 0, 'approved'),
            ('及第粥', merchant1[0], cat5[0], 12, '猪肝、猪腰、猪肚、大米', '鲜香绵滑，料足味美', 'food8.jpg', 4.5, 0,
             'approved'),
            ('皮蛋瘦肉粥', merchant1[0], cat5[0], 10, '皮蛋、瘦肉、大米', '经典粤式，暖胃首选', 'food9.jpg', 4.4, 0,
             'approved'),
            ('腊味煲仔饭', merchant1[0], cat6[0], 28, '腊肠、腊肉、米饭、酱油', '饭香四溢，锅巴焦脆', 'food10.jpg', 4.7, 0,
             'approved'),
        ]
        cursor.executemany("""
            INSERT IGNORE INTO foods 
            (name, merchant_id, category_id, price, ingredients, description, image_url, avg_rating, like_count, status) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, foods)
        print("✅ 美食插入成功")

        # 4. 查询用户和美食id，插入评分
        cursor.execute("SELECT id FROM users WHERE username = 'user1'")
        u1 = cursor.fetchone()
        cursor.execute("SELECT id FROM users WHERE username = 'user2'")
        u2 = cursor.fetchone()
        cursor.execute("SELECT id FROM users WHERE username = 'user3'")
        u3 = cursor.fetchone()

        cursor.execute("SELECT id FROM foods WHERE name = '秘制叉烧肠粉'")
        f1 = cursor.fetchone()
        cursor.execute("SELECT id FROM foods WHERE name = '深井烧鹅'")
        f2 = cursor.fetchone()
        cursor.execute("SELECT id FROM foods WHERE name = '虾饺'")
        f3 = cursor.fetchone()
        cursor.execute("SELECT id FROM foods WHERE name = '杨枝甘露'")
        f4 = cursor.fetchone()
        cursor.execute("SELECT id FROM foods WHERE name = '及第粥'")
        f5 = cursor.fetchone()
        cursor.execute("SELECT id FROM foods WHERE name = '腊味煲仔饭'")
        f6 = cursor.fetchone()
        cursor.execute("SELECT id FROM foods WHERE name = '鲜虾肠粉'")
        f7 = cursor.fetchone()
        cursor.execute("SELECT id FROM foods WHERE name = '蜜汁叉烧'")
        f8 = cursor.fetchone()

        ratings = [
            (u1[0], f1[0], 5),
            (u1[0], f2[0], 5),
            (u1[0], f3[0], 4),
            (u1[0], f4[0], 4),
            (u2[0], f1[0], 4),
            (u2[0], f2[0], 5),
            (u2[0], f5[0], 3),
            (u2[0], f6[0], 4),
            (u3[0], f2[0], 5),
            (u3[0], f3[0], 5),
            (u3[0], f7[0], 4),
            (u3[0], f8[0], 3),
        ]
        cursor.executemany(
            "INSERT IGNORE INTO ratings (user_id, food_id, score) VALUES (%s, %s, %s)",
            ratings
        )
        print("✅ 评分插入成功")

        # 关键：提交事务！
        conn.commit()
        print("🎉 所有测试数据插入完成！")

    except Exception as e:
        print(f"❌ 插入失败：{e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    insert_data()