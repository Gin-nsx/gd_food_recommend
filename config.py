import os
import pymysql

# 数据库连接配置
# 密码优先从环境变量读取，未设置时使用默认值（本地开发可直接运行）
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', '123456'),
    'database': os.environ.get('DB_NAME', 'food_db'),
    'charset': 'utf8mb4'
}

def get_connection():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)