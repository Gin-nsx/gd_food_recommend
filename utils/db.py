# 数据库连接工具
import pymysql
from config import DB_CONFIG

def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)