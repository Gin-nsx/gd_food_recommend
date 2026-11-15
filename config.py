# 全局配置：数据库连接 + 应用安全配置
import os
import secrets
from datetime import timedelta
from pathlib import Path

import pymysql

BASE_DIR = Path(__file__).resolve().parent

# ========== 数据库连接配置 ==========
# 优先从环境变量读取，未设置时使用本地开发默认值
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', '123456'),
    'database': os.environ.get('DB_NAME', 'food_db'),
    'charset': 'utf8mb4'
}


# ========== 应用安全配置 ==========
def load_secret_key() -> str:
    """
    获取 SECRET_KEY（用于 session 签名）：
    1. 优先读环境变量 SECRET_KEY（部署时用这个）
    2. 其次读项目根目录下的 .flask_secret 文件
    3. 都没有则随机生成一份并写入 .flask_secret（已在 .gitignore 中忽略）
    绝不把密钥硬编码进源码，避免仓库泄露后 session 被伪造。
    """
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key

    key_file = BASE_DIR / '.flask_secret'
    if key_file.exists():
        saved = key_file.read_text(encoding='utf-8').strip()
        if saved:
            return saved

    new_key = secrets.token_hex(32)
    key_file.write_text(new_key, encoding='utf-8')
    return new_key


SECRET_KEY = load_secret_key()

# 调试模式默认关闭；本地开发需要时用 set FLASK_DEBUG=1 开启
DEBUG = os.environ.get('FLASK_DEBUG', '0') == '1'

# 监听地址与端口
HOST = os.environ.get('FLASK_HOST', '127.0.0.1')
PORT = int(os.environ.get('FLASK_PORT', '5000'))

# session 安全参数
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
PERMANENT_SESSION_LIFETIME = timedelta(days=7)


def get_connection():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)
