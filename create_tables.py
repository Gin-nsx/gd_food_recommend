# 建表脚本：与线上 food_db（MySQL 8.0）结构保持一致
# 用法：python create_tables.py
# 说明：会自动创建数据库（若不存在），再建 12 张表；可重复执行，不会清空数据。
import pymysql

from config import DB_CONFIG

# 建库语句需要不指定 database 连接
_CFG_NO_DB = {k: v for k, v in DB_CONFIG.items() if k != 'database'}

CREATE_DATABASE_SQL = """
CREATE DATABASE IF NOT EXISTS `{db}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci
"""

CREATE_TABLES_SQL = """
-- 1. 用户表
CREATE TABLE IF NOT EXISTS users (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '用户ID',
    username VARCHAR(50) UNIQUE NOT NULL COMMENT '用户名',
    password VARCHAR(255) NOT NULL COMMENT '密码（加密）',
    role ENUM('user', 'merchant', 'admin') DEFAULT 'user' COMMENT '角色',
    email VARCHAR(100) COMMENT '邮箱',
    phone VARCHAR(20) COMMENT '手机号',
    merchant_name VARCHAR(100) COMMENT '店铺名称',
    merchant_address VARCHAR(255) COMMENT '店铺地址',
    merchant_status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending' COMMENT '商家审核状态',
    is_active BOOLEAN DEFAULT TRUE COMMENT '账号是否启用',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间',
    taboo TEXT COMMENT '忌口食材',
    allergy TEXT COMMENT '过敏食材',
    hide_risk TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否在首页/推荐隐藏含过敏或忌口的美食'
) COMMENT='用户表';

-- 2. 分类表
CREATE TABLE IF NOT EXISTS categories (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '分类ID',
    name VARCHAR(50) UNIQUE NOT NULL COMMENT '分类名称',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
) COMMENT='分类表';

-- 3. 美食表
CREATE TABLE IF NOT EXISTS foods (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '美食ID',
    name VARCHAR(100) NOT NULL COMMENT '美食名称',
    merchant_id INT NOT NULL COMMENT '所属商家ID',
    category_id INT COMMENT '分类ID',
    price DECIMAL(10,2) COMMENT '价格',
    ingredients TEXT COMMENT '原材料',
    description TEXT COMMENT '口味描述',
    image_url VARCHAR(255) COMMENT '图片地址',
    avg_rating DECIMAL(2,1) DEFAULT 0 COMMENT '平均评分',
    like_count INT DEFAULT 0 COMMENT '点赞数',
    status ENUM('pending', 'approved', 'rejected', 'offline') DEFAULT 'pending' COMMENT '审核状态',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    KEY idx_foods_merchant (merchant_id),
    KEY idx_foods_category (category_id),
    KEY idx_foods_status_rating (status, avg_rating),
    FOREIGN KEY (merchant_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
) COMMENT='美食表';

-- 4. 评分表
CREATE TABLE IF NOT EXISTS ratings (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '评分ID',
    user_id INT NOT NULL COMMENT '用户ID',
    food_id INT NOT NULL COMMENT '美食ID',
    score INT NOT NULL COMMENT '评分（1-5星）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '评分时间',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY unique_rating (user_id, food_id),
    KEY idx_ratings_food (food_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE,
    CONSTRAINT ratings_score_chk CHECK (score >= 1 AND score <= 5)
) COMMENT='评分表';

-- 5. 点赞表
CREATE TABLE IF NOT EXISTS likes (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '点赞ID',
    user_id INT NOT NULL COMMENT '用户ID',
    food_id INT NOT NULL COMMENT '美食ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '点赞时间',
    UNIQUE KEY unique_like (user_id, food_id),
    KEY idx_likes_food (food_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE
) COMMENT='点赞表';

-- 6. 收藏表
CREATE TABLE IF NOT EXISTS favorites (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '收藏ID',
    user_id INT NOT NULL COMMENT '用户ID',
    food_id INT NOT NULL COMMENT '美食ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '收藏时间',
    UNIQUE KEY unique_favorite (user_id, food_id),
    KEY idx_favorites_food (food_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE
) COMMENT='收藏表';

-- 7. 评论表
CREATE TABLE IF NOT EXISTS comments (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '评论ID',
    user_id INT NOT NULL COMMENT '用户ID',
    food_id INT NOT NULL COMMENT '美食ID',
    content TEXT NOT NULL COMMENT '评论内容',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '评论时间',
    KEY idx_comments_user (user_id),
    KEY idx_comments_food (food_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE
) COMMENT='评论表';

-- 8. 浏览记录表
CREATE TABLE IF NOT EXISTS histories (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '记录ID',
    user_id INT NOT NULL COMMENT '用户ID',
    food_id INT NOT NULL COMMENT '美食ID',
    last_view_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后浏览时间',
    UNIQUE KEY unique_history (user_id, food_id),
    KEY idx_histories_food (food_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE
) COMMENT='浏览记录表';

-- 9. 拉黑表（支持美食 / 商家双维度）
CREATE TABLE IF NOT EXISTS blacklists (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '拉黑ID',
    user_id INT NOT NULL COMMENT '用户ID',
    food_id INT COMMENT '被拉黑美食ID',
    merchant_id INT COMMENT '被拉黑商家ID',
    reason VARCHAR(255) COMMENT '拉黑原因',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '拉黑时间',
    KEY idx_blacklists_user (user_id),
    KEY idx_blacklists_food (food_id),
    KEY idx_blacklists_merchant (merchant_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE,
    FOREIGN KEY (merchant_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT blacklists_target_chk CHECK (food_id IS NOT NULL OR merchant_id IS NOT NULL)
) COMMENT='拉黑表';

-- 10. 举报表（handle_note 为管理员处理说明，后期新增字段）
CREATE TABLE IF NOT EXISTS reports (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '举报ID',
    reporter_id INT NOT NULL COMMENT '举报人ID',
    food_id INT COMMENT '被举报美食ID',
    comment_id INT COMMENT '被举报评论ID',
    reason VARCHAR(255) NOT NULL COMMENT '举报原因',
    status ENUM('pending', 'resolved', 'rejected') DEFAULT 'pending' COMMENT '处理状态',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '举报时间',
    resolved_at DATETIME COMMENT '处理时间',
    handle_note VARCHAR(255) COMMENT '管理员处理说明',
    KEY idx_reports_reporter (reporter_id),
    KEY idx_reports_food (food_id),
    KEY idx_reports_comment (comment_id),
    FOREIGN KEY (reporter_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE,
    FOREIGN KEY (comment_id) REFERENCES comments(id) ON DELETE CASCADE,
    CONSTRAINT reports_target_chk CHECK (food_id IS NOT NULL OR comment_id IS NOT NULL)
) COMMENT='举报表';

-- 11. 站内消息表（管理员 ↔ 商家/用户的沟通渠道）
CREATE TABLE IF NOT EXISTS messages (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '消息ID',
    sender_id INT NOT NULL COMMENT '发送者ID',
    receiver_id INT NOT NULL COMMENT '接收者ID',
    content VARCHAR(1000) NOT NULL COMMENT '消息正文',
    related_report_id INT COMMENT '关联举报ID（管理员从举报页发起时记录）',
    is_read TINYINT(1) NOT NULL DEFAULT 0 COMMENT '接收方是否已读',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '发送时间',
    KEY idx_messages_receiver (receiver_id, is_read),
    KEY idx_messages_sender (sender_id),
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (receiver_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (related_report_id) REFERENCES reports(id) ON DELETE SET NULL
) COMMENT='站内消息表';

-- 12. 浏览明细表（PV 口径，供首页每日热榜统计）
CREATE TABLE IF NOT EXISTS food_views (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '浏览记录ID',
    food_id INT NOT NULL COMMENT '美食ID',
    user_id INT COMMENT '用户ID（未登录为空）',
    visitor_key VARCHAR(64) COMMENT '匿名访客标识（session 摘要，仅用于去重）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '浏览时间',
    KEY idx_food_views_time (created_at),
    KEY idx_food_views_food_time (food_id, created_at),
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
) COMMENT='美食浏览明细表（PV 口径）';
"""


def create_database():
    """创建数据库（若不存在）"""
    conn = pymysql.connect(**_CFG_NO_DB)
    cursor = conn.cursor()
    try:
        cursor.execute(CREATE_DATABASE_SQL.format(db=DB_CONFIG['database']))
        print(f"✅ 数据库 {DB_CONFIG['database']} 已就绪")
    finally:
        cursor.close()
        conn.close()


def create_tables():
    create_database()

    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        for sql in CREATE_TABLES_SQL.strip().split(';'):
            if sql.strip():
                cursor.execute(sql.strip())
        conn.commit()

        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"✅ 建表完成，共 {len(tables)} 张表：{', '.join(sorted(tables))}")
    except Exception as e:
        print(f"❌ 建表失败: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    create_tables()
