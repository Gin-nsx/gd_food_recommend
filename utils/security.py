# utils/security.py
# 通用安全工具：CSRF 令牌、登录失败限流
#
# 说明：
# - CSRF 不引入 flask-wtf，用 session 里存一个随机令牌，表单/请求头带上即可。
# - 限流用进程内字典（单实例部署足够），key 为「IP + 用户名」，5 分钟内错 5 次锁定。
import hmac
import secrets
import threading
import time
from collections import deque

from flask import request, session

# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
CSRF_SESSION_KEY = '_csrf_token'
CSRF_FIELD = '_csrf_token'
CSRF_HEADER = 'X-CSRFToken'


def get_csrf_token() -> str:
    """取当前会话的 CSRF 令牌，没有就生成一个（同一个会话内保持不变）"""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def submitted_csrf_token() -> str:
    """从请求中取客户端提交的令牌：优先请求头（fetch），其次表单/JSON"""
    token = request.headers.get(CSRF_HEADER)
    if token:
        return token
    if request.form:
        return request.form.get(CSRF_FIELD, '')
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data.get(CSRF_FIELD, '')
    return ''


def validate_csrf() -> bool:
    """校验请求携带的 CSRF 令牌与会话中的是否一致（常量时间比较，防时序攻击）"""
    expected = session.get(CSRF_SESSION_KEY)
    if not expected:
        return False
    got = submitted_csrf_token()
    return bool(got) and hmac.compare_digest(str(expected), str(got))


# ---------------------------------------------------------------------------
# 站内跳转校验（防开放重定向）
# ---------------------------------------------------------------------------
def safe_internal_path(target):
    """只接受本站相对路径，用于 next / from 这类回跳参数。

    拒绝：空值、绝对 URL、协议相对 //evil.com、
          反斜杠变体 /\\evil.com（浏览器会当成 //evil.com）、含换行的注入串。
    """
    if not target:
        return None
    target = str(target).strip()
    if not target.startswith('/'):
        return None
    if target.startswith('//') or target.startswith('/\\'):
        return None
    if '\\' in target or '\n' in target or '\r' in target:
        return None
    return target


# ---------------------------------------------------------------------------
# 登录失败限流
# ---------------------------------------------------------------------------
MAX_FAILURES = 5           # 窗口内允许的失败次数
FAIL_WINDOW = 300          # 统计窗口（秒）
LOCK_SECONDS = 300         # 超限后锁定时长（秒）

_failures = {}
_lock = threading.Lock()


def _purge(bucket, now):
    while bucket and now - bucket[0] > FAIL_WINDOW:
        bucket.popleft()


def client_key(username: str) -> str:
    """限流键：同一 IP 上的同一用户名，避免一个人把别人锁死"""
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '')
    ip = ip.split(',')[0].strip()
    return f'{ip}|{(username or "").strip().lower()}'


def is_locked(key: str) -> bool:
    with _lock:
        bucket = _failures.get(key)
        if not bucket:
            return False
        now = time.time()
        _purge(bucket, now)
        return len(bucket) >= MAX_FAILURES


def record_failure(key: str) -> None:
    with _lock:
        now = time.time()
        bucket = _failures.setdefault(key, deque())
        _purge(bucket, now)
        bucket.append(now)


def clear_failures(key: str) -> None:
    with _lock:
        _failures.pop(key, None)


def lock_remaining(key: str) -> int:
    """剩余锁定秒数，用于提示用户"""
    with _lock:
        bucket = _failures.get(key)
        if not bucket:
            return 0
        now = time.time()
        _purge(bucket, now)
        if len(bucket) < MAX_FAILURES:
            return 0
        return max(1, int(FAIL_WINDOW - (now - bucket[0])))
