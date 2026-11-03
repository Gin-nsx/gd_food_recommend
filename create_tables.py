import pymysql
from config import DB_CONFIG

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
    allergy TEXT COMMENT '过敏食材',
    taboo TEXT COMMENT '忌口食材',
    is_active BOOLEAN DEFAULT TRUE COMMENT '账号是否启用',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间'
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
    FOREIGN KEY (merchant_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
) COMMENT='美食表';

-- 4. 评分表
CREATE TABLE IF NOT EXISTS ratings (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '评分ID',
    user_id INT NOT NULL COMMENT '用户ID',
    food_id INT NOT NULL COMMENT '美食ID',
    score INT NOT NULL CHECK (score >= 1 AND score <= 5) COMMENT '评分（1-5星）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '评分时间',
    UNIQUE KEY unique_rating (user_id, food_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE
) COMMENT='评分表';

-- 5. 点赞表
CREATE TABLE IF NOT EXISTS likes (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '点赞ID',
    user_id INT NOT NULL COMMENT '用户ID',
    food_id INT NOT NULL COMMENT '美食ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '点赞时间',
    UNIQUE KEY unique_like (user_id, food_id),
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
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE
) COMMENT='浏览记录表';

-- 9. 拉黑表
CREATE TABLE IF NOT EXISTS blacklists (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '拉黑ID',
    user_id INT NOT NULL COMMENT '用户ID',
    food_id INT COMMENT '被拉黑美食ID',
    merchant_id INT COMMENT '被拉黑商家ID',
    reason VARCHAR(255) COMMENT '拉黑原因',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '拉黑时间',
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE,
    FOREIGN KEY (merchant_id) REFERENCES users(id) ON DELETE CASCADE,
    CHECK (food_id IS NOT NULL OR merchant_id IS NOT NULL)
) COMMENT='拉黑表';

-- 10. 举报表
CREATE TABLE IF NOT EXISTS reports (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '举报ID',
    reporter_id INT NOT NULL COMMENT '举报人ID',
    food_id INT COMMENT '被举报美食ID',
    comment_id INT COMMENT '被举报评论ID',
    reason VARCHAR(255) NOT NULL COMMENT '举报原因',
    status ENUM('pending', 'resolved', 'rejected') DEFAULT 'pending' COMMENT '处理状态',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '举报时间',
    resolved_at DATETIME COMMENT '处理时间',
    FOREIGN KEY (reporter_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE CASCADE,
    FOREIGN KEY (comment_id) REFERENCES comments(id) ON DELETE CASCADE,
    CHECK (food_id IS NOT NULL OR comment_id IS NOT NULL)
) COMMENT='举报表';
"""

def create_tables():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        for sql in CREATE_TABLES_SQL.strip().split(';'):
            if sql.strip():
                cursor.execute(sql.strip())
        conn.commit()
        print("✅ 所有表创建成功！")
    except Exception as e:
        print(f"❌ 建表失败: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    create_tables()

