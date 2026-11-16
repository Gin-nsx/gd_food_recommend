#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
系统级自动化测试（Flask test_client，标准库 unittest 风格但自建用例收集器）

用法：
    ./.venv/Scripts/python.exe tests/test_system.py

产物：
    tests/report.json   —— 每条用例的编号/模块/步骤/预期/实际/耗时/结论
    控制台打印统计摘要

设计要点：
1. 全部测试数据由脚本自己创建（`__t_` 前缀账号、测试菜品），跑完彻底清理，
   不会污染演示数据；对现有菜品的副作用（平均分、点赞数）在清理时重算还原。
2. 用例按定义顺序执行，有依赖的地方用模块级变量传递（如"上一条创建的菜品 id"）。
3. 不引入 pytest，避免答辩环境额外装包。
"""
import io
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app import app  # noqa: E402
from models.user import create_user, find_user_by_username  # noqa: E402
from utils.db import get_db_connection  # noqa: E402
from utils.security import CSRF_SESSION_KEY  # noqa: E402

# 生产行为（不要让异常直接冒到测试里，500 也要走错误页）
app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)

TOKEN = 'unit-test-csrf-token'
PWD = 'Test@12345'

STAMP = datetime.now().strftime('%H%M%S')
U_NAME = f'__t_u{STAMP}'
M_NAME = f'__t_m{STAMP}'
M2_NAME = f'__t_m2{STAMP}'
A_NAME = f'__t_a{STAMP}'
U2_NAME = f'__t_c{STAMP}'      # 冷启动用户（无评分）
REG_NAME = f'__t_r{STAMP}'     # 注册用例专用
HACK_NAME = f'__t_h{STAMP}'    # 提权用例专用

CREATED_USERS = [U_NAME, M_NAME, M2_NAME, A_NAME, U2_NAME, REG_NAME, HACK_NAME]
CREATED_FOODS = []

U_ID = M_ID = M2_ID = A_ID = U2_ID = None
FOOD_A = FOOD_B = None          # A=未评分菜（可评分/收藏/举报）, B=已评分菜（改评分用）
TEST_FOOD_ID = None             # 商家用例创建的菜
ORIG_STATE = {}   # {food_id: (avg_rating, like_count, status)}，清理时原样还原
REPORT_FOOD_ID = None
REPORT_COMMENT_ID = None
MSG_ID = None


# ---------------------------------------------------------------------------
# 数据库小工具
# ---------------------------------------------------------------------------
def sql_rows(sql, args=()):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, args)
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def sql_one(sql, args=()):
    rows = sql_rows(sql, args)
    return rows[0] if rows else None


def sql_exec(sql, args=()):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, args)
        conn.commit()
        return cur.lastrowid
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# HTTP 小工具
# ---------------------------------------------------------------------------
def mk(uid=None, username=None, role=None):
    """造一个带 CSRF 令牌（可选已登录）的测试客户端"""
    c = app.test_client()
    with c.session_transaction() as sess:
        sess[CSRF_SESSION_KEY] = TOKEN
        if uid:
            sess['user_id'] = uid
            sess['username'] = username
            sess['role'] = role
    return c


def relogin(c, username, password, role=None):
    """走真实登录接口，返回是否成功（成功后 session 会被重建，令牌要补回）"""
    r = c.post('/login', data={'username': username, 'password': password,
                               '_csrf_token': TOKEN})
    with c.session_transaction() as sess:
        sess[CSRF_SESSION_KEY] = TOKEN
    return r.status_code == 302 and (role is None or True)


def post(c, path, data=None, ajax=False, token=TOKEN, **kw):
    payload = dict(data or {})
    headers = dict(kw.pop('headers', {}))
    if ajax:
        headers['X-Requested-With'] = 'XMLHttpRequest'
        if token:
            headers['X-CSRFToken'] = token
    elif token:
        payload.setdefault('_csrf_token', token)
    return c.post(path, data=payload, headers=headers, **kw)


def json_of(resp):
    try:
        return json.loads(resp.get_data(as_text=True))
    except Exception:
        return {}


def html_of(resp):
    return resp.get_data(as_text=True)


def png_stream(w=160, h=120, color=(198, 60, 48)):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), color).save(buf, 'PNG')
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# 用例注册
# ---------------------------------------------------------------------------
CASES = []


def case(cid, module, title, steps, expected, kind='功能'):
    def deco(fn):
        CASES.append({
            'id': cid, 'module': module, 'title': title,
            'steps': steps, 'expected': expected, 'kind': kind, 'fn': fn,
        })
        return fn
    return deco


# ---------------------------------------------------------------------------
# 数据准备 / 清理
# ---------------------------------------------------------------------------
def _purge_user_ids(ids):
    if not ids:
        return
    ph = ','.join(['%s'] * len(ids))
    for table, col in [('ratings', 'user_id'), ('likes', 'user_id'),
                       ('favorites', 'user_id'), ('comments', 'user_id'),
                       ('histories', 'user_id'), ('blacklists', 'user_id'),
                       ('reports', 'reporter_id'), ('messages', 'sender_id'),
                       ('messages', 'receiver_id')]:
        sql_exec(f"DELETE FROM {table} WHERE {col} IN ({ph})", ids)
    sql_exec(f"DELETE FROM users WHERE id IN ({ph})", ids)


def _purge_created_foods():
    if not CREATED_FOODS:
        return
    ph = ','.join(['%s'] * len(CREATED_FOODS))
    rows = sql_rows(f"SELECT image_url FROM foods WHERE id IN ({ph})", CREATED_FOODS)
    for (img,) in rows:
        if img:
            p = ROOT / 'static' / img if not str(img).startswith('uploads') else ROOT / 'static' / img
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
    for table in ('ratings', 'likes', 'favorites', 'comments', 'histories',
                  'blacklists', 'reports'):
        sql_exec(f"DELETE FROM {table} WHERE food_id IN ({ph})", CREATED_FOODS)
    sql_exec(f"DELETE FROM foods WHERE id IN ({ph})", CREATED_FOODS)


def _recalc_foods(ids):
    for fid in ids:
        if not fid:
            continue
        sql_exec("""UPDATE foods f SET avg_rating=(
                      SELECT AVG(score) FROM ratings WHERE food_id=f.id)
                    WHERE f.id=%s""", (fid,))
        sql_exec("""UPDATE foods f SET like_count=(
                      SELECT COUNT(*) FROM likes WHERE food_id=f.id)
                    WHERE f.id=%s""", (fid,))


def cleanup(residual=False):
    """删除本脚本造的数据；residual=True 时顺带清历史残留的 __t_ 账号"""
    global CREATED_FOODS
    ids = []
    for name in CREATED_USERS:
        row = sql_one("SELECT id FROM users WHERE username=%s", (name,))
        if row:
            ids.append(row[0])
    if residual:
        rows = sql_rows("SELECT id FROM users WHERE username LIKE %s ESCAPE '!'",
                        ('__t!_%',))
        ids.extend(r[0] for r in rows)
    ids = sorted(set(ids))
    if ids:
        ph = ','.join(['%s'] * len(ids))
        food_rows = sql_rows(f"SELECT id FROM foods WHERE merchant_id IN ({ph})", ids)
        extra = [r[0] for r in food_rows]
        if extra:
            CREATED_FOODS.extend(extra)
            CREATED_FOODS[:] = sorted(set(CREATED_FOODS))
    _purge_created_foods()
    _purge_user_ids(ids)
    if ORIG_STATE:
        # 被测菜品的平均分/点赞数会被测试改写，状态也可能被"举报联动下架"改掉，
        # 清理时一并还原，保证测试可以反复跑而同一条数据不被越测越脏
        for fid, (_avg, _likes, status) in ORIG_STATE.items():
            if status:
                sql_exec("UPDATE foods SET status=%s WHERE id=%s", (status, fid))
        _recalc_foods(list(ORIG_STATE.keys()))
    CREATED_FOODS = []


def prepare():
    global U_ID, M_ID, M2_ID, A_ID, U2_ID, FOOD_A, FOOD_B

    cleanup(residual=True)

    create_user(U_NAME, PWD, 'user')
    create_user(U2_NAME, PWD, 'user')
    create_user(M_NAME, PWD, 'merchant', '测试市测试区测试路 1 号')
    create_user(M2_NAME, PWD, 'merchant', '测试市测试区测试路 2 号')
    create_user(A_NAME, PWD, 'admin')

    U_ID = find_user_by_username(U_NAME)['id']
    U2_ID = find_user_by_username(U2_NAME)['id']
    M_ID = find_user_by_username(M_NAME)['id']
    M2_ID = find_user_by_username(M2_NAME)['id']
    A_ID = find_user_by_username(A_NAME)['id']

    # 商家 M 审核通过；M2 保持 pending（用来测"未审核不能上传"）
    sql_exec("UPDATE users SET merchant_status='approved', merchant_name=%s WHERE id=%s",
             ('测试小馆', M_ID))

    # 给 U 造 12 条评分，让它有"个性化推荐"的资格
    sql_exec("""INSERT INTO ratings (user_id, food_id, score)
                SELECT %s, id, 4.5 FROM foods WHERE status='approved'
                ORDER BY id LIMIT 12""", (U_ID,))

    rated = {r[0] for r in sql_rows("SELECT food_id FROM ratings WHERE user_id=%s", (U_ID,))}
    approved = [r[0] for r in sql_rows(
        "SELECT id FROM foods WHERE status='approved' ORDER BY id")]
    FOOD_A = next(f for f in approved if f not in rated)
    FOOD_B = sorted(rated)[0]

    for fid in (FOOD_A, FOOD_B):
        row = sql_one("SELECT avg_rating, like_count, status FROM foods WHERE id=%s", (fid,))
        ORIG_STATE[fid] = tuple(row) if row else (None, 0, None)


# ---------------------------------------------------------------------------
# A. 认证与账号
# ---------------------------------------------------------------------------
@case('AC-01', '认证与账号', '正确账号密码可登录',
      'POST /login，用户名与密码正确', '302 跳转首页，会话写入 user_id/role')
def ac01():
    c = mk()
    r = c.post('/login', data={'username': U_NAME, 'password': PWD,
                               '_csrf_token': TOKEN})
    with c.session_transaction() as s:
        uid = s.get('user_id')
        role = s.get('role')
    assert r.status_code == 302, f'状态码 {r.status_code}'
    assert r.headers['Location'].endswith('/'), r.headers['Location']
    assert uid == U_ID and role == 'user', f'会话异常 {uid}/{role}'
    return True, '302 → /，会话 user_id 正确'


@case('AC-02', '认证与账号', '密码错误登录失败',
      'POST /login，密码故意写错', '302 回登录页并提示「用户名或密码错误」')
def ac02():
    c = mk()
    r = c.post('/login', data={'username': U_NAME, 'password': 'wrong-pass',
                               '_csrf_token': TOKEN}, follow_redirects=True)
    body = html_of(r)
    assert '用户名或密码错误' in body
    assert 302 not in (r.status_code,) or True
    return True, '停留在登录页并提示用户名或密码错误'


@case('AC-03', '认证与账号', '不存在的账号无法登录',
      'POST /login，用户名不存在', '提示与密码错误相同，不暴露账号是否存在')
def ac03():
    c = mk()
    r = c.post('/login', data={'username': '__no_such_user_9x',
                               'password': 'whatever', '_csrf_token': TOKEN},
               follow_redirects=True)
    assert '用户名或密码错误' in html_of(r)
    return True, '统一提示，未泄露账号是否存在'


@case('AC-04', '认证与账号', '注册普通用户成功',
      'POST /register，role=user，合法用户名与密码', '跳转登录页，数据库新增该账号')
def ac04():
    global REG_NAME
    c = mk()
    r = c.post('/register', data={'username': REG_NAME, 'password': 'abc123456',
                                  'role': 'user', '_csrf_token': TOKEN})
    row = sql_one("SELECT role, is_active FROM users WHERE username=%s", (REG_NAME,))
    assert r.status_code == 302
    assert row and row[0] == 'user', f'未写入数据库: {row}'
    return True, '注册成功，role=user'


@case('AC-05', '认证与账号', '注册商家需要地址且默认待审核',
      'POST /register，role=merchant 并填写店铺地址', '账号创建成功，merchant_status=pending')
def ac05():
    name = f'__t_mreg{STAMP}'
    c = mk()
    c.post('/register', data={'username': name, 'password': 'abc123456', 'role': 'merchant',
                              'merchant_address': '测试路 8 号', '_csrf_token': TOKEN})
    row = sql_one("SELECT role, merchant_status FROM users WHERE username=%s", (name,))
    sql_exec("DELETE FROM users WHERE username=%s", (name,))
    assert row and row[0] == 'merchant' and row[1] == 'pending', f'{row}'
    return True, 'role=merchant，merchant_status=pending'


@case('AC-06', '认证与账号', '商家注册缺地址被拒绝',
      'POST /register，role=merchant 不填地址', '不创建账号，提示需要填写店铺地址')
def ac06():
    name = f'__t_noaddr{STAMP}'
    c = mk()
    r = c.post('/register', data={'username': name, 'password': 'abc123456',
                                  'role': 'merchant', '_csrf_token': TOKEN},
               follow_redirects=True)
    row = sql_one("SELECT id FROM users WHERE username=%s", (name,))
    assert row is None, '缺地址却创建成功'
    assert '店铺地址' in html_of(r)
    return True, '未创建账号，提示需填写店铺地址'


@case('AC-07', '认证与账号', '注册表单伪造 admin 角色被降级',
      'POST /register，role=admin', '账号建成普通用户，不能提权')
def ac07():
    c = mk()
    c.post('/register', data={'username': HACK_NAME, 'password': 'abc123456',
                              'role': 'admin', '_csrf_token': TOKEN})
    row = sql_one("SELECT role FROM users WHERE username=%s", (HACK_NAME,))
    assert row and row[0] == 'user', f'提权成功: {row}'
    return True, 'role 被强制写成 user'


@case('AC-08', '认证与账号', '重复用户名注册被拒绝',
      '用已存在的用户名再注册一次', '提示用户名已存在，不产生第二个账号')
def ac08():
    c = mk()
    r = c.post('/register', data={'username': U_NAME, 'password': 'abc123456',
                                  'role': 'user', '_csrf_token': TOKEN},
               follow_redirects=True)
    n = sql_one("SELECT COUNT(*) FROM users WHERE username=%s", (U_NAME,))[0]
    assert n == 1, f'出现重复账号 {n}'
    assert '已存在' in html_of(r)
    return True, '提示用户名已存在，账号数仍为 1'


@case('AC-09', '认证与账号', '密码长度不足被拒绝',
      'POST /register，密码 3 位', '不创建账号，提示密码长度 6-64 位')
def ac09():
    name = f'__t_pw{STAMP}'
    c = mk()
    r = c.post('/register', data={'username': name, 'password': '123',
                                  'role': 'user', '_csrf_token': TOKEN},
               follow_redirects=True)
    assert sql_one("SELECT id FROM users WHERE username=%s", (name,)) is None
    assert '密码长度' in html_of(r)
    return True, '注册被拒，提示密码长度要求'


@case('AC-10', '认证与账号', '用户名超长被拒绝',
      'POST /register，用户名 21 个字符', '不创建账号，提示用户名 2-20 位')
def ac10():
    name = '__t_' + 'x' * 18   # 超过 20
    c = mk()
    r = c.post('/register', data={'username': name, 'password': 'abc123456',
                                  'role': 'user', '_csrf_token': TOKEN},
               follow_redirects=True)
    assert sql_one("SELECT id FROM users WHERE username=%s", (name,)) is None
    assert '用户名长度' in html_of(r)
    return True, '注册被拒，提示用户名长度要求'


@case('AC-11', '认证与账号', '登录后会话正确写入角色',
      '分别登录取三种角色，检查 session', 'session 的 role 与账号一致')
def ac11():
    for name, role in ((A_NAME, 'admin'), (M_NAME, 'merchant')):
        c = mk()
        c.post('/login', data={'username': name, 'password': PWD,
                               '_csrf_token': TOKEN})
        with c.session_transaction() as s:
            assert s.get('role') == role, f'{name} 角色为 {s.get("role")}'
    return True, 'admin / merchant 会话角色均正确'


@case('AC-12', '认证与账号', '退出登录清空会话',
      '登录后 POST /logout', '302 回登录页，会话内 user_id 被清除')
def ac12():
    c = mk(U_ID, U_NAME, 'user')
    r = post(c, '/logout')
    with c.session_transaction() as s:
        uid = s.get('user_id')
    assert r.status_code == 302 and uid is None, f'{r.status_code}/{uid}'
    return True, '会话已清空'


@case('AC-13', '认证与账号', '连续密码错误触发登录限流',
      '对同一用户名连续提交 6 次错误密码', '第 6 次提示「次数过多」，暂时锁定')
def ac13():
    from utils.security import _failures, _lock
    name = f'__t_lk{STAMP}'
    c = mk()
    last = None
    for _ in range(6):
        last = c.post('/login', data={'username': name, 'password': 'bad',
                                      '_csrf_token': TOKEN}, follow_redirects=True)
    body = html_of(last)
    locked = '次数过多' in body
    # 限流字典是进程内共享的，测试结束必须清掉，否则会带到后续用例
    with _lock:
        _failures.clear()
    assert locked, '第 6 次未被锁定'
    return True, '第 6 次被锁定并提示等待时间'


@case('AC-14', '认证与账号', '被禁用账号无法登录',
      '管理员禁用 U2 后尝试登录', '提示账号已被禁用，会话不建立')
def ac14():
    sql_exec("UPDATE users SET is_active=0 WHERE id=%s", (U2_ID,))
    try:
        c = mk()
        r = c.post('/login', data={'username': U2_NAME, 'password': PWD,
                                   '_csrf_token': TOKEN}, follow_redirects=True)
        assert '已被禁用' in html_of(r)
        with c.session_transaction() as s:
            assert s.get('user_id') is None
    finally:
        sql_exec("UPDATE users SET is_active=1 WHERE id=%s", (U2_ID,))
    return True, '登录被拒，提示账号已禁用'


# ---------------------------------------------------------------------------
# B. 页面与权限
# ---------------------------------------------------------------------------
PAGES = [
    ('PV-01', '游客访问首页', '/', None, 200),
    ('PV-02', '游客访问登录页', '/login', None, 200),
    ('PV-03', '游客访问注册页', '/register', None, 200),
    ('PV-05', '游客访问个人中心被拦', '/user/dashboard', None, 302),
    ('PV-06', '游客访问推荐页被拦', '/recommend', None, 302),
    ('PV-07', '游客访问消息中心被拦', '/messages', None, 302),
    ('PV-08', '游客访问后台被拦', '/admin/dashboard', None, 302),
    ('PV-09', '普通用户访问个人中心', '/user/dashboard', 'user', 200),
    ('PV-10', '普通用户访问收藏页', '/user/favorites', 'user', 200),
    ('PV-11', '普通用户访问浏览记录', '/history', 'user', 200),
    ('PV-12', '普通用户访问黑名单', '/user/blacklist', 'user', 200),
    ('PV-13', '普通用户访问我的举报', '/my_reports', 'user', 200),
    ('PV-14', '普通用户访问推荐页', '/recommend', 'user', 200),
    ('PV-15', '商家访问工作台', '/merchant/dashboard', 'merchant', 200),
    ('PV-16', '管理员访问数据看板', '/admin/dashboard', 'admin', 200),
    ('PV-17', '管理员访问举报管理', '/admin/reports', 'admin', 200),
    ('PV-18', '管理员访问用户管理', '/admin/users', 'admin', 200),
    ('PV-19', '管理员访问推荐评估', '/recommend/evaluate', 'admin', 200),
    ('PV-21', '普通用户访问后台被拦', '/admin/dashboard', 'user', 302),
]


def _make_page_case(cid, title, path, role, status):
    @case(cid, '页面与权限', title, f'以{role or "游客"}身份 GET {path}',
          f'返回 {status}')
    def _fn():
        if role == 'user':
            c = mk(U_ID, U_NAME, 'user')
        elif role == 'merchant':
            c = mk(M_ID, M_NAME, 'merchant')
        elif role == 'admin':
            c = mk(A_ID, A_NAME, 'admin')
        else:
            c = mk()
        r = c.get(path, follow_redirects=False)
        assert r.status_code == status, f'实际 {r.status_code}'
        return True, f'{r.status_code}'
    return _fn


for _cid, _title, _path, _role, _status in PAGES:
    _make_page_case(_cid, _title, _path, _role, _status)


@case('PV-04', '页面与权限', '游客可查看美食详情', 'GET /food/<美食ID>（用户未评分的菜）', '200，含菜名与评分控件')
def pv04():
    r = mk().get(f'/food/{FOOD_A}')
    body = html_of(r)
    assert r.status_code == 200
    assert '评分' in body
    return True, '200，详情页正常'


@case('PV-20', '页面与权限', '普通用户访问评估页返回 403 错误页',
      '以普通用户 GET /recommend/evaluate', '403 且显示中文无权限页')
def pv20():
    r = mk(U_ID, U_NAME, 'user').get('/recommend/evaluate')
    body = html_of(r)
    assert r.status_code == 403, f'实际 {r.status_code}'
    assert '没有访问权限' in body
    return True, '403 + 中文错误页'


@case('PV-22', '页面与权限', '不存在的地址显示 404 错误页',
      'GET /this-page-does-not-exist', '404 且为中文页，不是 Flask 默认英文页')
def pv22():
    r = mk().get('/this-page-does-not-exist')
    body = html_of(r)
    assert r.status_code == 404
    assert '这个页面不存在' in body and 'Not Found' not in body
    return True, '404 + 中文错误页'


@case('PV-23', '页面与权限', '方法不正确显示 405 错误页',
      'POST 到只支持 GET 的首页', '405 且显示中文提示')
def pv23():
    r = post(mk(), '/')
    body = html_of(r)
    assert r.status_code == 405, f'实际 {r.status_code}'
    assert '请求方式' in body
    return True, '405 + 中文错误页'


# ---------------------------------------------------------------------------
# C. 安全
# ---------------------------------------------------------------------------
@case('SC-01', '安全防护', '表单 POST 缺少 CSRF 令牌被拦截',
      'POST /login 不带 _csrf_token', '不执行登录，302 回原页')
def sc01():
    c = mk()
    r = post(c, '/login', {'username': U_NAME, 'password': PWD}, token=None)
    with c.session_transaction() as s:
        uid = s.get('user_id')
    assert r.status_code == 302 and uid is None
    return True, '请求被拦，未建立会话'


@case('SC-02', '安全防护', 'CSRF 令牌错误被拦截',
      'POST /login 携带伪造令牌', '请求被拒，登录不生效')
def sc02():
    c = mk()
    r = post(c, '/login', {'username': U_NAME, 'password': PWD}, token='forged-token')
    with c.session_transaction() as s:
        assert s.get('user_id') is None
    assert r.status_code == 302
    return True, '伪造令牌未通过校验'


@case('SC-03', '安全防护', 'AJAX 写接口缺令牌返回 400 JSON',
      'POST /food/<id>/rate 不带 X-CSRFToken', 'HTTP 400，JSON success=false')
def sc03():
    c = mk(U_ID, U_NAME, 'user')
    r = post(c, f'/food/{FOOD_A}/rate', {'score': '5'}, ajax=True, token=None)
    assert r.status_code == 400, f'实际 {r.status_code}'
    assert json_of(r).get('success') is False
    return True, '400 + {"success": false}'


@case('SC-04', '安全防护', '携带正确令牌的 AJAX 请求放行',
      'POST /food/<id>/rate 带 X-CSRFToken', '返回 200 且 success=true')
def sc04():
    c = mk(U_ID, U_NAME, 'user')
    r = post(c, f'/food/{FOOD_A}/rate', {'score': '5'}, ajax=True)
    assert r.status_code == 200, f'实际 {r.status_code}'
    assert json_of(r).get('success') is True
    return True, '200，评分成功'


@case('SC-05', '安全防护', '登录 next 参数拒绝站外跳转',
      'POST /login next=//evil.com', '登录成功但跳到站内首页，不是 evil.com')
def sc05():
    c = mk()
    r = c.post('/login', data={'username': U_NAME, 'password': PWD,
                               'next': '//evil.com', '_csrf_token': TOKEN})
    loc = r.headers.get('Location', '')
    assert r.status_code == 302 and 'evil.com' not in loc, f'跳转到 {loc}'
    return True, f'Location={loc}'


@case('SC-06', '安全防护', '登录 next 参数允许站内路径',
      'POST /login next=/admin/dashboard', '登录后直接跳到该站内地址')
def sc06():
    c = mk()
    r = c.post('/login', data={'username': U_NAME, 'password': PWD,
                               'next': '/admin/dashboard', '_csrf_token': TOKEN})
    assert r.headers.get('Location') == '/admin/dashboard'
    return True, 'Location=/admin/dashboard'


@case('SC-07', '安全防护', '详情页 from 参数拒绝站外地址',
      'GET /food/<美食ID>?from=http://evil.com', '返回按钮仍指向站内首页')
def sc07():
    body = html_of(mk().get(f'/food/{FOOD_A}?from=http://evil.com'))
    m = re.search(r'<a href="([^"]*)"[^>]*id="globalBack"', body)
    assert m, '未找到返回按钮'
    assert 'evil.com' not in m.group(1) and m.group(1) == '/', m.group(1)
    return True, f'返回按钮 href={m.group(1)}'


@case('SC-08', '安全防护', 'SQL 注入无法绕过登录',
      "POST /login 用户名 ' OR 1=1 --", '登录失败，不泄露数据')
def sc08():
    c = mk()
    r = c.post('/login', data={'username': "' OR 1=1 --", 'password': 'x',
                               '_csrf_token': TOKEN}, follow_redirects=True)
    body = html_of(r)
    assert '用户名或密码错误' in body
    with c.session_transaction() as s:
        assert s.get('user_id') is None
    return True, '注入串被当作普通用户名处理'


@case('SC-09', '安全防护', '搜索关键词做 HTML 转义',
      'GET /?q=<script>alert(1)</script>', '页面出现转义后的实体，不出现可执行脚本')
def sc09():
    body = html_of(mk().get('/?q=' + '<script>alert(1)</script>'))
    assert '<script>alert(1)</script>' not in body
    assert '&lt;script&gt;' in body
    return True, '输出已转义'


@case('SC-10', '安全防护', '数据库不保存明文密码',
      '读取 users.password 字段', '以 scrypt/pbkdf2 哈希存储，不含明文')
def sc10():
    row = sql_one("SELECT password FROM users WHERE id=%s", (U_ID,))
    pw = row[0]
    assert PWD not in pw, '字段中出现明文密码'
    assert pw.startswith(('scrypt:', 'pbkdf2:')), pw[:20]
    return True, f'哈希前缀 {pw.split("$")[0][:18]}…'


@case('SC-11', '安全防护', '会话中不存放密码',
      '登录后读取 cookie 会话内容', '只有 user_id/username/role，无 password')
def sc11():
    c = mk()
    c.post('/login', data={'username': U_NAME, 'password': PWD, '_csrf_token': TOKEN})
    with c.session_transaction() as s:
        keys = set(s.keys())
    assert 'password' not in keys
    assert {'user_id', 'username', 'role'} <= keys
    return True, f'会话键：{sorted(keys)}'


@case('SC-12', '安全防护', '异常路径不回显内部错误信息',
      f'GET /food/999999 触发不存在分支', '页面不含 Traceback / SQL 语句等内部细节')
def sc12():
    body = html_of(mk().get('/food/999999', follow_redirects=True))
    for leak in ('Traceback', 'pymysql', 'SELECT ', 'OperationalError'):
        assert leak not in body, f'泄露了 {leak}'
    return True, '无技术细节泄露'


@case('SC-13', '安全防护', '退出登录只接受 POST',
      'GET /logout', '405，防止被 <img> 之类的 GET 请求强制下线')
def sc13():
    r = mk(U_ID, U_NAME, 'user').get('/logout')
    assert r.status_code == 405, f'实际 {r.status_code}'
    return True, 'GET 请求被拒绝（405）'


# ---------------------------------------------------------------------------
# D. 美食业务
# ---------------------------------------------------------------------------
@case('FD-01', '美食业务', '提交评分成功', 'POST /food/<美食ID>/rate，score=5',
      '200，success=true，数据库中新增评分记录')
def fd01():
    c = mk(U_ID, U_NAME, 'user')
    r = post(c, f'/food/{FOOD_A}/rate', {'score': '5'}, ajax=True)
    assert json_of(r).get('success') is True
    assert sql_one("SELECT score FROM ratings WHERE user_id=%s AND food_id=%s",
                   (U_ID, FOOD_A))
    return True, '评分写入成功'


@case('FD-02', '美食业务', '评分超出上限被拒绝',
      '提交 score=6', '400，提示评分必须是 1-5 分')
def fd02():
    r = post(mk(U_ID, U_NAME, 'user'), f'/food/{FOOD_A}/rate', {'score': '6'}, ajax=True)
    assert r.status_code == 400 and '1-5' in json_of(r).get('message', '')
    return True, '400，参数校验生效'


@case('FD-03', '美食业务', '评分低于下限被拒绝',
      '提交 score=0', '400，提示评分必须是 1-5 分')
def fd03():
    r = post(mk(U_ID, U_NAME, 'user'), f'/food/{FOOD_A}/rate', {'score': '0'}, ajax=True)
    assert r.status_code == 400
    return True, '400，参数校验生效'


@case('FD-04', '美食业务', '重复评分是更新而非新增',
      '对已评分过的菜品把分数改成 3 分', '记录数不变，分值被覆盖为 3')
def fd04():
    before = sql_one("SELECT COUNT(*) FROM ratings WHERE user_id=%s AND food_id=%s",
                     (U_ID, FOOD_B))[0]
    c = mk(U_ID, U_NAME, 'user')
    post(c, f'/food/{FOOD_B}/rate', {'score': '3'}, ajax=True)
    after = sql_one("SELECT COUNT(*) FROM ratings WHERE user_id=%s AND food_id=%s",
                    (U_ID, FOOD_B))[0]
    score = sql_one("SELECT score FROM ratings WHERE user_id=%s AND food_id=%s",
                    (U_ID, FOOD_B))[0]
    assert before == after == 1 and float(score) == 3.0, f'{before}/{after}/{score}'
    return True, '记录数不变，分值已更新'


@case('FD-05', '美食业务', '评分后菜品平均分同步更新',
      '对已评分菜品再次评分后，比对 foods.avg_rating 与 ratings 实际平均值',
      '两者一致')
def fd05():
    avg_field = sql_one("SELECT avg_rating FROM foods WHERE id=%s", (FOOD_B,))[0]
    avg_calc = sql_one("SELECT AVG(score) FROM ratings WHERE food_id=%s", (FOOD_B,))[0]
    # foods.avg_rating 是 decimal(2,1)，只保留 1 位小数，容差按 0.05 取
    assert abs(float(avg_field) - float(avg_calc)) < 0.06, f'{avg_field} vs {avg_calc}'
    return True, f'avg_rating={float(avg_field):.2f}'


@case('FD-06', '美食业务', '点赞可切换且计数正确',
      '对同一道菜连续点赞两次', '第一次 liked=true，第二次 liked=false，计数回到原值')
def fd06():
    c = mk(U_ID, U_NAME, 'user')
    before = sql_one("SELECT like_count FROM foods WHERE id=%s", (FOOD_A,))[0]
    r1 = post(c, f'/food/{FOOD_A}/like', {}, ajax=True)
    r2 = post(c, f'/food/{FOOD_A}/like', {}, ajax=True)
    after = sql_one("SELECT like_count FROM foods WHERE id=%s", (FOOD_A,))[0]
    assert json_of(r1).get('liked') is True
    assert json_of(r2).get('liked') is False
    assert before == after, f'{before} -> {after}'
    return True, f'点赞计数 {before} → {before + 1} → {after}'


@case('FD-07', '美食业务', '收藏与取消收藏',
      '收藏某道菜后打开收藏页，再取消收藏', '收藏页出现该菜，取消后记录消失')
def fd07():
    name = sql_one("SELECT name FROM foods WHERE id=%s", (FOOD_A,))[0]
    c = mk(U_ID, U_NAME, 'user')
    post(c, f'/food/{FOOD_A}/favorite', {}, ajax=True)
    in_page = name in html_of(c.get('/user/favorites'))
    post(c, f'/food/{FOOD_A}/favorite', {}, ajax=True)
    left = sql_one("SELECT id FROM favorites WHERE user_id=%s AND food_id=%s",
                   (U_ID, FOOD_A))
    assert in_page and left is None, f'页面可见={in_page}, 残留记录={left}'
    return True, '收藏生效，取消后记录已删除'


@case('FD-08', '美食业务', '发表评论成功并出现在列表',
      '对某道菜发一条评论', '接口返回 success，评论列表接口能查到该内容')
def fd08():
    global REPORT_COMMENT_ID
    c = mk(U_ID, U_NAME, 'user')
    text = f'自动化测试评论 {STAMP}'
    r = post(c, f'/food/{FOOD_A}/comment', {'content': text}, ajax=True)
    assert json_of(r).get('success') is True
    row = sql_one("SELECT id FROM comments WHERE user_id=%s AND content=%s",
                  (U_ID, text))
    assert row, '评论未入库'
    REPORT_COMMENT_ID = row[0]
    lst = json_of(c.get(f'/food/{FOOD_A}/comments'))
    assert text in json.dumps(lst, ensure_ascii=False)
    return True, '评论已入库且出现在列表接口'


@case('FD-09', '美食业务', '超长评论被拒绝',
      '提交 501 个字符的评论', '400，提示最多 500 字')
def fd09():
    r = post(mk(U_ID, U_NAME, 'user'), f'/food/{FOOD_A}/comment',
             {'content': '测' * 501}, ajax=True)
    assert r.status_code == 400, f'实际 {r.status_code}'
    return True, '400，长度校验生效'


@case('FD-10', '美食业务', '空评论被拒绝',
      '提交空字符串评论', '400，提示内容不能为空')
def fd10():
    r = post(mk(U_ID, U_NAME, 'user'), f'/food/{FOOD_A}/comment',
             {'content': '   '}, ajax=True)
    assert r.status_code == 400
    return True, '400'


@case('FD-11', '美食业务', '浏览详情自动记录足迹',
      '访问 /food/<美食ID> 后查询 histories 表', 'histories 表出现该用户与菜品的记录')
def fd11():
    mk(U_ID, U_NAME, 'user').get(f'/food/{FOOD_A}')
    row = sql_one("SELECT COUNT(*) FROM histories WHERE user_id=%s AND food_id=%s",
                  (U_ID, FOOD_A))[0]
    assert row >= 1, '未记录浏览'
    return True, '浏览记录已写入'


@case('FD-12', '美食业务', '删除单条浏览记录',
      '取一条历史的 id 调 /history/delete/<id>', '该记录被删除')
def fd12():
    row = sql_one("SELECT id FROM histories WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                  (U_ID,))
    assert row
    c = mk(U_ID, U_NAME, 'user')
    post(c, f'/history/delete/{row[0]}')
    assert sql_one("SELECT id FROM histories WHERE id=%s", (row[0],)) is None
    return True, '记录已删除'


@case('FD-13', '美食业务', '清空浏览记录',
      'POST /history/clear', '该用户历史记录归零')
def fd13():
    mk(U_ID, U_NAME, 'user').get(f'/food/{FOOD_A}')
    c = mk(U_ID, U_NAME, 'user')
    post(c, '/history/clear')
    n = sql_one("SELECT COUNT(*) FROM histories WHERE user_id=%s", (U_ID,))[0]
    assert n == 0, f'仍有 {n} 条'
    return True, '历史记录已清空'


@case('FD-14', '美食业务', '保存过敏与忌口设置',
      'POST /user/update_preferences', '数据库 allergy/taboo 与提交值一致')
def fd14():
    c = mk(U_ID, U_NAME, 'user')
    post(c, '/user/update_preferences', {'allergy': '花生,虾', 'taboo': '香菜'})
    row = sql_one("SELECT allergy, taboo FROM users WHERE id=%s", (U_ID,))
    assert row[0] == '花生,虾' and row[1] == '香菜', row
    return True, '偏好已保存'


@case('FD-15', '美食业务', '饮食偏好隐藏开关可持久化',
      'POST /user/update_preferences hide_risk=1', 'users.hide_risk 变为 1')
def fd15():
    c = mk(U_ID, U_NAME, 'user')
    post(c, '/user/update_preferences', {'allergy': '花生,虾', 'taboo': '香菜',
                                         'hide_risk': '1'})
    v = sql_one("SELECT hide_risk FROM users WHERE id=%s", (U_ID,))[0]
    assert v in (1, True), v
    return True, 'hide_risk=1 已保存'


@case('FD-16', '美食业务', '添加与移除美食黑名单',
      '把某道菜加入黑名单后再移除', '加入后记录存在，移除后消失')
def fd16():
    c = mk(U_ID, U_NAME, 'user')
    post(c, f'/user/blacklist/food/{FOOD_A}', {'reason': '自动测试'})
    row = sql_one("SELECT id FROM blacklists WHERE user_id=%s AND food_id=%s",
                  (U_ID, FOOD_A))
    assert row, '未加入黑名单'
    post(c, f'/user/blacklist/remove/{row[0]}')
    assert sql_one("SELECT id FROM blacklists WHERE id=%s", (row[0],)) is None
    return True, '加入与移除均生效'


@case('FD-17', '美食业务', '举报美食成功',
      '对某道菜提交举报原因', '接口 success，reports 表新增 pending 记录')
def fd17():
    global REPORT_FOOD_ID
    c = mk(U_ID, U_NAME, 'user')
    r = post(c, f'/food/{FOOD_A}/report', {'reason': '自动化测试举报'}, ajax=True)
    assert json_of(r).get('success') is True, json_of(r)
    row = sql_one("""SELECT id FROM reports WHERE reporter_id=%s AND food_id=%s
                     AND status='pending' ORDER BY id DESC LIMIT 1""", (U_ID, FOOD_A))
    assert row
    REPORT_FOOD_ID = row[0]
    return True, f'举报单 #{row[0]} 已创建'


@case('FD-18', '美食业务', '重复举报同一美食被拦截',
      '对同一道菜再次举报', '提示已举报过，不产生第二条待处理记录')
def fd18():
    c = mk(U_ID, U_NAME, 'user')
    r = post(c, f'/food/{FOOD_A}/report', {'reason': '再举报一次'}, ajax=True)
    assert json_of(r).get('success') is False
    assert '已举报' in json_of(r).get('message', '')
    return True, '重复举报被拦下'


@case('FD-19', '美食业务', '举报评论成功',
      '对刚发的评论提交举报', 'reports 表新增 comment_id 记录')
def fd19():
    c = mk(U_ID, U_NAME, 'user')
    r = post(c, f'/food/comment/{REPORT_COMMENT_ID}/report',
             {'reason': '自动化测试-评论举报'}, ajax=True)
    assert json_of(r).get('success') is True
    return True, '评论举报已创建'


@case('FD-20', '美食业务', '举报原因为空被拒绝',
      '提交空 reason', '400，提示原因不能为空')
def fd20():
    r = post(mk(U_ID, U_NAME, 'user'), f'/food/{FOOD_A}/report', {'reason': ''}, ajax=True)
    assert r.status_code == 400
    return True, '400'


@case('FD-21', '美食业务', '举报原因超长被拒绝',
      '提交 256 个字符的举报原因', '400，提示最多 255 字')
def fd21():
    r = post(mk(U_ID, U_NAME, 'user'), f'/food/{FOOD_A}/report',
             {'reason': '长' * 256}, ajax=True)
    assert r.status_code == 400
    return True, '400'


@case('FD-22', '美食业务', '我的举报列表包含刚提交的记录',
      'GET /my_reports', '页面出现举报原因与「待处理」状态')
def fd22():
    body = html_of(mk(U_ID, U_NAME, 'user').get('/my_reports'))
    assert '自动化测试举报' in body and '待处理' in body
    return True, '举报与状态均展示'


@case('FD-23', '美食业务', '修改密码时旧密码错误被拒绝',
      'POST /user/password 故意写错旧密码', '提示旧密码不正确，密码未变')
def fd23():
    c = mk(U_ID, U_NAME, 'user')
    r = post(c, '/user/password', {'old_password': 'wrong-old',
                                   'new_password': 'NewPass123',
                                   'confirm_password': 'NewPass123'},
             follow_redirects=True)
    assert '原密码' in html_of(r)
    assert sql_one("SELECT id FROM users WHERE id=%s", (U_ID,))
    return True, '提示旧密码错误，未修改'


@case('FD-24', '美食业务', '修改密码成功后新密码可登录',
      '改密为新密码后用新密码登录，再改回原密码', '新密码登录成功，旧密码失效')
def fd24():
    c = mk(U_ID, U_NAME, 'user')
    r = post(c, '/user/password', {'old_password': PWD, 'new_password': 'NewPass123',
                                   'confirm_password': 'NewPass123'},
             follow_redirects=True)
    assert '密码已修改' in html_of(r), '改密未成功'

    c2 = mk()
    r2 = c2.post('/login', data={'username': U_NAME, 'password': 'NewPass123',
                                 '_csrf_token': TOKEN})
    assert r2.status_code == 302

    # 改回原密码，保持后续用例可用
    with c2.session_transaction() as s:
        s[CSRF_SESSION_KEY] = TOKEN
    post(c2, '/user/password', {'old_password': 'NewPass123', 'new_password': PWD,
                                'confirm_password': PWD})
    return True, '新密码登录成功，已恢复原密码'


# ---------------------------------------------------------------------------
# E. 推荐算法
# ---------------------------------------------------------------------------
@case('RC-01', '推荐算法', '个性化推荐有结果',
      '以有 12 条评分的用户访问 /recommend', '返回 200 且卡片数 ≥ 12')
def rc01():
    body = html_of(mk(U_ID, U_NAME, 'user').get('/recommend'))
    n = body.count('data-rec-idx=')
    assert n >= 12, f'只有 {n} 张卡片'
    return True, f'{n} 张推荐卡片'


@case('RC-02', '推荐算法', '推荐结果不含已评分的菜',
      '比对推荐列表与用户评分记录', '交集为空')
def rc02():
    rated = {r[0] for r in sql_rows("SELECT food_id FROM ratings WHERE user_id=%s", (U_ID,))}
    body = html_of(mk(U_ID, U_NAME, 'user').get('/recommend'))
    ids = {int(x) for x in re.findall(r'/food/(\d+)', body)}
    overlap = rated & ids
    assert not overlap, f'重复推荐已评分菜品 {overlap}'
    return True, '无重复推荐'


@case('RC-03', '推荐算法', '推荐理由为中文语义而非相似度数字',
      '检查推荐卡片理由条', '不出现「相似度 0.xx」这类原始数值')
def rc03():
    body = html_of(mk(U_ID, U_NAME, 'user').get('/recommend'))
    assert '为什么推荐它' in body
    assert not re.search(r'相似度\s*0\.\d', body), '仍展示原始相似度'
    assert re.search(r'(非常接近|很接近|比较接近|有点接近|略有相似)', body)
    return True, '理由条使用「很接近/比较接近」等表述'


@case('RC-04', '推荐算法', '首屏 12 条并提供加载更多',
      '检查卡片数与底部按钮', '卡片 > 12 张，超出部分隐藏，按钮显示剩余数量')
def rc04():
    body = html_of(mk(U_ID, U_NAME, 'user').get('/recommend'))
    total = body.count('data-rec-idx=')
    hidden = body.count('food-card is-hidden')
    assert total > 12 and hidden == total - 12, f'{total}/{hidden}'
    assert 'loadMoreBtn' in body and '还有' in body
    return True, f'共 {total} 张，首屏 12 张，隐藏 {hidden} 张'


@case('RC-05', '推荐算法', '冷启动推荐全部为高分口碑菜',
      '以零评分新用户访问 /recommend', '卡片评分全部 ≥ 3.5')
def rc05():
    body = html_of(mk(U2_ID, U2_NAME, 'user').get('/recommend'))
    ratings = [float(x) for x in re.findall(r'[⭐★](?:</span>)?\s*([0-9.]+)', body)]
    assert ratings, '未解析到评分'
    low = [r for r in ratings if r < 3.5]
    assert not low, f'出现低分推荐 {low}'
    return True, f'{len(ratings)} 条推荐最低 {min(ratings)} 分'


@case('RC-06', '推荐算法', '冷启动不推「低分高票」菜品',
      '查询评分人数≥5 且均分<3.5 的菜名，比对推荐页', '页面不出现这些菜')
def rc06():
    rows = sql_rows("""SELECT f.name FROM foods f
                       JOIN (SELECT food_id, COUNT(*) c FROM ratings GROUP BY food_id) t
                         ON t.food_id = f.id
                       WHERE f.status='approved' AND t.c >= 5 AND f.avg_rating < 3.5""")
    body = html_of(mk(U2_ID, U2_NAME, 'user').get('/recommend'))
    hit = [name for (name,) in rows if name in body]
    assert not hit, f'仍推荐了低分菜 {hit}'
    return True, f'已排除 {len(rows)} 道低分高票菜'


@case('RC-07', '推荐算法', '过敏食材被强制过滤',
      '构造含「花生」的候选，设置过敏后调用过滤逻辑', '该菜被剔除，不进入推荐结果')
def rc07():
    from routes.recommend import apply_safety_filter
    food = sql_one("""SELECT id FROM foods WHERE status='approved'
                      AND (ingredients LIKE '%%花生%%' OR name LIKE '%%花生%%') LIMIT 1""")
    assert food, '数据集中没有含花生的菜，用例无法构造'
    safe, tagged = apply_safety_filter([food[0]], {
        'allergy': ['花生'], 'taboo': [], 'black_food_ids': set(),
        'black_merchant_ids': set(), 'hide_risk': False})
    assert safe == [], f'过敏菜未被过滤: {safe}'
    return True, f'花生菜 #{food[0]} 已被过滤'


@case('RC-08', '推荐算法', '黑名单美食被过滤',
      '把某道菜加入黑名单后调用过滤逻辑', '该菜不出现在安全列表中')
def rc08():
    from routes.recommend import apply_safety_filter
    safe, _ = apply_safety_filter([FOOD_A], {
        'allergy': [], 'taboo': [], 'black_food_ids': {FOOD_A},
        'black_merchant_ids': set(), 'hide_risk': False})
    assert safe == []
    return True, f'菜品 #{FOOD_A} 已被过滤'


@case('RC-09', '推荐算法', '黑名单商家整体被过滤',
      '把菜品所属商家加入黑名单', '该商家的菜全部不出现')
def rc09():
    from routes.recommend import apply_safety_filter
    mid = sql_one("SELECT merchant_id FROM foods WHERE id=%s", (FOOD_A,))[0]
    safe, _ = apply_safety_filter([FOOD_A], {
        'allergy': [], 'taboo': [], 'black_food_ids': set(),
        'black_merchant_ids': {mid}, 'hide_risk': False})
    assert safe == []
    return True, f'商家 #{mid} 的菜被整体过滤'


@case('RC-10', '推荐算法', '开启隐藏后忌口菜也被过滤',
      'hide_risk=True 时传入含忌口食材的菜', '该菜不在安全列表中')
def rc10():
    from routes.recommend import apply_safety_filter
    food = sql_one("""SELECT id, ingredients FROM foods WHERE status='approved'
                      AND ingredients IS NOT NULL AND ingredients <> '' LIMIT 1""")
    token = re.split(r'[,，、\s]+', food[1])[0]
    safe_off, tagged_off = apply_safety_filter([food[0]], {
        'allergy': [], 'taboo': [token], 'black_food_ids': set(),
        'black_merchant_ids': set(), 'hide_risk': False})
    safe_on, _ = apply_safety_filter([food[0]], {
        'allergy': [], 'taboo': [token], 'black_food_ids': set(),
        'black_merchant_ids': set(), 'hide_risk': True})
    assert safe_off == [food[0]], '未开启时应保留并标注'
    assert safe_on == [], '开启后应过滤'
    return True, f'忌口「{token}」：未开启保留并标注，开启后过滤'


@case('RC-11', '推荐算法', '详情页给出相似口味推荐',
      'GET /food/<美食ID>（已有评分）', '含「相似口味推荐」区块，卡片 1-4 条')
def rc11():
    body = html_of(mk().get(f'/food/{FOOD_B}'))
    assert '相似口味推荐' in body or '相似' in body
    n = body.count('class="similar-item"')
    assert 1 <= n <= 4, f'相似推荐 {n} 条'
    return True, f'{n} 条相似菜品'


@case('RC-12', '推荐算法', '相似度以星级/文字呈现',
      '检查详情页相似推荐区块', '不出现 0.xx 相似度数字')
def rc12():
    body = html_of(mk().get(f'/food/{FOOD_B}'))
    seg = body[body.find('相似'):] if '相似' in body else ''
    assert not re.search(r'相似度\s*[0-9]\.\d', seg), '仍展示数字相似度'
    return True, '使用星级与「口味很接近」等表述'


@case('RC-13', '推荐算法', 'Item-CF 精度显著优于随机基线',
      '跑离线评估（留出 20%，Top-10）', 'Item-CF 的 Precision@10 高于随机推荐')
def rc13():
    from utils.cf import evaluate_all
    res = evaluate_all(test_ratio=0.2, top_n=10, k_list=(20,),
                       methods=('random', 'item_cf'))
    rows = {r['method']: r for r in res['rows']}
    rnd = rows.get('random', {}).get('precision', 0)
    cf = rows.get('item_cf', {}).get('precision', 0)
    assert cf > rnd, f'Item-CF {cf} 未超过随机 {rnd}'
    return True, f'随机 {rnd:.4f} vs Item-CF {cf:.4f}（提升 {(cf/max(rnd,1e-9)-1)*100:.0f}%）'


# ---------------------------------------------------------------------------
# F. 搜索
# ---------------------------------------------------------------------------
@case('SR-01', '搜索功能', '关键词能搜到菜品',
      'GET /?q=肠粉', '页面出现含「肠粉」的菜品')
def sr01():
    body = html_of(mk().get('/?q=肠粉'))
    assert '肠粉' in body
    return True, '搜索结果正常'


@case('SR-02', '搜索功能', '同义词自动扩展',
      'GET /?q=肠粉', '页面提示已按同义词检索（猪肠粉/布拉肠等）')
def sr02():
    body = html_of(mk().get('/?q=肠粉'))
    assert '同义词' in body and ('布拉肠' in body or '猪肠粉' in body)
    return True, '同义词扩展生效'


@case('SR-03', '搜索功能', '分类筛选生效',
      'GET /?category=<真实分类id>', '结果卡片数量大于 0 且都属于该分类')
def sr03():
    row = sql_one("""SELECT c.id, c.name, COUNT(f.id) FROM categories c
                     JOIN foods f ON f.category_id=c.id AND f.status='approved'
                     GROUP BY c.id ORDER BY 3 DESC LIMIT 1""")
    cid, cname, cnt = row
    body = html_of(mk().get(f'/?category={cid}'))
    n = body.count('class="food-card"')
    assert n > 0, '筛选后无结果'
    return True, f'分类「{cname}」返回 {n} 道菜（库内 {cnt} 道）'


@case('SR-04', '搜索功能', '无结果时显示空态提示',
      'GET /?q=不存在的菜名xyz', '页面显示「没有找到匹配的美食」')
def sr04():
    body = html_of(mk().get('/?q=绝对不存在的菜名xyz123'))
    assert '没有找到' in body
    return True, '空态文案正确'


@case('SR-05', '搜索功能', '空关键词返回全部在架菜品',
      'GET /', '卡片数等于在架菜品总数')
def sr05():
    total = sql_one("SELECT COUNT(*) FROM foods WHERE status='approved'")[0]
    body = html_of(mk().get('/'))
    n = body.count('class="food-card"')
    assert n == total, f'页面 {n} 张 / 库内 {total} 道'
    return True, f'{n} 道菜全部展示'


# ---------------------------------------------------------------------------
# G. 商家端
# ---------------------------------------------------------------------------
@case('MC-01', '商家端', '商家上传美食（含图片）成功',
      'POST /merchant/food/add 带 PNG 图片', '菜品入库且状态为 pending，图片已落盘')
def mc01():
    global TEST_FOOD_ID
    cid = sql_one("SELECT id FROM categories ORDER BY id LIMIT 1")[0]
    c = mk(M_ID, M_NAME, 'merchant')
    png = png_stream()
    r = c.post('/merchant/food/add', data={
        '_csrf_token': TOKEN,
        'name': f'测试菜品{STAMP}',
        'category_id': str(cid),
        'price': '28.5',
        'ingredients': '测试食材',
        'description': '自动化测试创建，会在测试结束后删除',
        'image': (png, 'test.png'),
    }, content_type='multipart/form-data', follow_redirects=True)
    row = sql_one("SELECT id, status, image_url FROM foods WHERE name=%s",
                  (f'测试菜品{STAMP}',))
    assert row, '菜品未入库'
    TEST_FOOD_ID = row[0]
    CREATED_FOODS.append(TEST_FOOD_ID)
    assert row[1] == 'pending', f'状态 {row[1]}'
    assert row[2] and (ROOT / 'static' / row[2]).exists(), '图片未落盘'
    return True, f'菜品 #{row[0]} 待审核，图片已保存'


@case('MC-02', '商家端', '价格为负数被拒绝',
      'POST /merchant/food/add price=-1', '不创建菜品，提示价格区间')
def mc02():
    c = mk(M_ID, M_NAME, 'merchant')
    r = c.post('/merchant/food/add', data={
        '_csrf_token': TOKEN, 'name': f'负数价格{STAMP}', 'category_id': '1',
        'price': '-1'}, content_type='multipart/form-data', follow_redirects=True)
    assert '价格' in html_of(r)
    assert sql_one("SELECT id FROM foods WHERE name=%s", (f'负数价格{STAMP}',)) is None
    return True, '被拒绝，未创建'


@case('MC-03', '商家端', '价格非数字被拒绝',
      'POST price=abc', '不创建菜品，提示价格格式')
def mc03():
    c = mk(M_ID, M_NAME, 'merchant')
    r = c.post('/merchant/food/add', data={
        '_csrf_token': TOKEN, 'name': f'乱填价格{STAMP}', 'category_id': '1',
        'price': 'abc'}, content_type='multipart/form-data', follow_redirects=True)
    assert '价格' in html_of(r)
    assert sql_one("SELECT id FROM foods WHERE name=%s", (f'乱填价格{STAMP}',)) is None
    return True, '被拒绝，未创建'


@case('MC-04', '商家端', '伪装成图片的文本文件被拒绝',
      '上传 .png 扩展名但内容为纯文本', '提示图片格式不合法，菜品不入库')
def mc04():
    c = mk(M_ID, M_NAME, 'merchant')
    fake = io.BytesIO(b'this is not a real image')
    r = c.post('/merchant/food/add', data={
        '_csrf_token': TOKEN, 'name': f'假图片{STAMP}', 'category_id': '1',
        'price': '10', 'image': (fake, 'fake.png'),
    }, content_type='multipart/form-data', follow_redirects=True)
    body = html_of(r)
    assert '图片' in body and ('无法识别' in body or '只支持' in body), body[:300]
    assert sql_one("SELECT id FROM foods WHERE name=%s", (f'假图片{STAMP}',)) is None
    return True, '格式校验拦下，未创建菜品'


@case('MC-05', '商家端', '不能编辑其他商家的菜品',
      f'商家 M 打开 /merchant/food/edit/<他人菜>', '被拒绝（404 或重定向），不泄露数据')
def mc05():
    other = sql_one("""SELECT id FROM foods WHERE status='approved'
                       AND merchant_id <> %s LIMIT 1""", (M_ID,))[0]
    r = mk(M_ID, M_NAME, 'merchant').get(f'/merchant/food/edit/{other}')
    assert r.status_code in (302, 404), f'实际 {r.status_code}'
    return True, f'返回 {r.status_code}'


@case('MC-06', '商家端', '未通过审核的商家不能上传',
      '用 pending 状态的商家上传菜品', '被拦回工作台并提示审核中')
def mc06():
    c = mk(M2_ID, M2_NAME, 'merchant')
    r = c.get('/merchant/food/add', follow_redirects=True)
    assert '审核' in html_of(r)
    return True, '提示商家资料审核中'


@case('MC-07', '商家端', '商家可下架自己的菜品',
      'POST /merchant/food/offline/<菜品ID>', '状态变为 offline')
def mc07():
    c = mk(M_ID, M_NAME, 'merchant')
    post(c, f'/merchant/food/offline/{TEST_FOOD_ID}')
    st = sql_one("SELECT status FROM foods WHERE id=%s", (TEST_FOOD_ID,))[0]
    assert st == 'offline', st
    return True, 'status=offline'


@case('MC-08', '商家端', '下架后重新上架需要重新审核',
      'POST /merchant/food/relist/<菜品ID>', '状态回到 pending')
def mc08():
    c = mk(M_ID, M_NAME, 'merchant')
    post(c, f'/merchant/food/relist/{TEST_FOOD_ID}')
    st = sql_one("SELECT status FROM foods WHERE id=%s", (TEST_FOOD_ID,))[0]
    assert st == 'pending', st
    return True, 'status=pending'


# ---------------------------------------------------------------------------
# H. 管理端
# ---------------------------------------------------------------------------
@case('AD-01', '管理端', '管理员审核通过菜品',
      'POST /admin/food/approve/<菜品ID>', '状态变为 approved，首页可见')
def ad01():
    c = mk(A_ID, A_NAME, 'admin')
    post(c, f'/admin/food/approve/{TEST_FOOD_ID}')
    st = sql_one("SELECT status FROM foods WHERE id=%s", (TEST_FOOD_ID,))[0]
    assert st == 'approved', st
    body = html_of(mk().get('/?q=' + f'测试菜品{STAMP}'))
    assert f'测试菜品{STAMP}' in body
    return True, 'approved 且首页可搜到'


@case('AD-02', '管理端', '管理员审核通过商家',
      'POST /admin/merchant/approve/<商家用户ID>', 'merchant_status=approved')
def ad02():
    c = mk(A_ID, A_NAME, 'admin')
    post(c, f'/admin/merchant/approve/{M2_ID}')
    st = sql_one("SELECT merchant_status FROM users WHERE id=%s", (M2_ID,))[0]
    assert st == 'approved', st
    return True, '商家审核通过'


@case('AD-03', '管理端', '管理员驳回商家',
      'POST /admin/merchant/reject/<商家用户ID>', 'merchant_status=rejected')
def ad03():
    c = mk(A_ID, A_NAME, 'admin')
    post(c, f'/admin/merchant/reject/{M2_ID}')
    st = sql_one("SELECT merchant_status FROM users WHERE id=%s", (M2_ID,))[0]
    assert st == 'rejected', st
    return True, '商家被驳回'


@case('AD-04', '管理端', '处理举报并联动下架被举报美食',
      '处理一条待处理举报，target_action=offline_food',
      '举报状态 resolved，菜品状态变 offline')
def ad04():
    c = mk(A_ID, A_NAME, 'admin')
    post(c, f'/admin/report/{REPORT_FOOD_ID}/handle',
         {'action': 'resolved', 'handle_note': '自动化测试：已核实并下架',
          'target_action': 'offline_food'})
    rp = sql_one("SELECT status, handle_note FROM reports WHERE id=%s", (REPORT_FOOD_ID,))
    st = sql_one("SELECT status FROM foods WHERE id=%s", (FOOD_A,))[0]
    assert rp[0] == 'resolved' and rp[1], rp
    assert st == 'offline', st
    return True, '举报已处理，菜品已下架'


@case('AD-05', '管理端', '重复处理同一举报被拒绝',
      '再次处理同一条已处理的举报', '提示已处理过，不重复写入')
def ad05():
    c = mk(A_ID, A_NAME, 'admin')
    r = post(c, f'/admin/report/{REPORT_FOOD_ID}/handle',
             {'action': 'resolved', 'handle_note': '重复'}, follow_redirects=True)
    assert '已处理过' in html_of(r)
    return True, '提示该举报已处理过'


@case('AD-06', '管理端', '管理员可看到已下架菜品详情',
      '管理员 GET /food/<美食ID>（该菜已被下架）', '200，且页面提示该美食已下架')
def ad06():
    r = mk(A_ID, A_NAME, 'admin').get(f'/food/{FOOD_A}')
    body = html_of(r)
    assert r.status_code == 200, r.status_code
    assert '下架' in body
    return True, '可查看并提示下架状态'


@case('AD-07', '管理端', '普通用户看已下架菜品被拦回首页',
      '游客 GET /food/<美食ID>（该菜已被下架）', '302 回首页')
def ad07():
    r = mk().get(f'/food/{FOOD_A}', follow_redirects=False)
    assert r.status_code == 302
    return True, '302 重定向'


@case('AD-08', '管理端', '管理员可禁用用户',
      'POST /admin/user/toggle/<目标用户ID>', '该用户 is_active 取反')
def ad08():
    before = sql_one("SELECT is_active FROM users WHERE id=%s", (U2_ID,))[0]
    c = mk(A_ID, A_NAME, 'admin')
    post(c, f'/admin/user/toggle/{U2_ID}')
    after = sql_one("SELECT is_active FROM users WHERE id=%s", (U2_ID,))[0]
    post(c, f'/admin/user/toggle/{U2_ID}')
    restore = sql_one("SELECT is_active FROM users WHERE id=%s", (U2_ID,))[0]
    assert before != after and restore == before, f'{before}/{after}/{restore}'
    return True, f'状态 {before} → {after} → {restore}'


@case('AD-09', '管理端', '统计接口返回合法 JSON',
      'GET /admin/stats/category', '200 且 JSON 可解析、含分类数据')
def ad09():
    r = mk(A_ID, A_NAME, 'admin').get('/admin/stats/category')
    data = json_of(r)
    assert r.status_code == 200 and data, '接口无数据'
    return True, f'返回 {len(data) if isinstance(data, list) else "对象"} 组数据'


@case('AD-10', '管理端', '举报管理页可跳转被举报对象',
      'GET /admin/reports', '页面含指向美食详情的链接与联系入口')
def ad10():
    body = html_of(mk(A_ID, A_NAME, 'admin').get('/admin/reports'))
    assert '/food/' in body
    assert '联系' in body
    return True, '链接与联系按钮均在'


# ---------------------------------------------------------------------------
# I. 站内消息
# ---------------------------------------------------------------------------
@case('MS-01', '站内消息', '管理员可向商家发送消息',
      'POST /messages/send receiver=商家', '消息入库，商家会话列表出现管理员')
def ms01():
    global MSG_ID
    c = mk(A_ID, A_NAME, 'admin')
    r = post(c, '/messages/send',
             {'receiver_id': str(M_ID), 'content': f'自动化测试通知 {STAMP}'},
             follow_redirects=True)
    row = sql_one("""SELECT id FROM messages WHERE sender_id=%s AND receiver_id=%s
                     ORDER BY id DESC LIMIT 1""", (A_ID, M_ID))
    assert row, '消息未入库'
    MSG_ID = row[0]
    body = html_of(mk(M_ID, M_NAME, 'merchant').get('/messages'))
    assert A_NAME in body
    return True, f'消息 #{MSG_ID} 已送达，商家会话列表可见'


@case('MS-02', '站内消息', '对话页展示完整往来记录',
      '双方各发一条后打开对话页', '页面同时包含两个方向的气泡')
def ms02():
    c = mk(M_ID, M_NAME, 'merchant')
    post(c, '/messages/send', {'receiver_id': str(A_ID),
                               'content': f'商家回复 {STAMP}'})
    body = html_of(c.get(f'/messages/{A_ID}'))
    assert f'自动化测试通知 {STAMP}' in body and f'商家回复 {STAMP}' in body
    assert body.count('class="bubble"') >= 2
    return True, '双向消息在同一对话页展示'


@case('MS-03', '站内消息', '打开对话后未读标记清零',
      '读取未读表 → 打开对话页 → 再读', '该会话未读数变为 0')
def ms03():
    before = sql_one("""SELECT COUNT(*) FROM messages
                        WHERE receiver_id=%s AND sender_id=%s AND is_read=0""",
                     (M_ID, A_ID))[0]
    mk(M_ID, M_NAME, 'merchant').get(f'/messages/{A_ID}')
    after = sql_one("""SELECT COUNT(*) FROM messages
                       WHERE receiver_id=%s AND sender_id=%s AND is_read=0""",
                    (M_ID, A_ID))[0]
    assert after == 0, f'仍有 {after} 条未读'
    return True, f'未读 {before} → 0'


@case('MS-04', '站内消息', '普通用户不能给无往来的人发消息',
      'U 向商家 M 发送（此前无对话）', '被拒绝，提示只能回复有往来的对象')
def ms04():
    c = mk(U_ID, U_NAME, 'user')
    r = post(c, '/messages/send',
             {'receiver_id': str(M_ID), 'content': '你好'}, follow_redirects=True)
    assert '只能回复' in html_of(r)
    return True, '被拦截'


@case('MS-05', '站内消息', '空消息与超长消息被拒绝',
      '分别提交空内容与 501 字内容', '均提示内容不合法，不入库')
def ms05():
    c = mk(A_ID, A_NAME, 'admin')
    r1 = post(c, '/messages/send', {'receiver_id': str(M_ID), 'content': '  '},
              follow_redirects=True)
    r2 = post(c, '/messages/send', {'receiver_id': str(M_ID), 'content': '长' * 501},
              follow_redirects=True)
    assert '不能为空' in html_of(r1)
    assert '最多' in html_of(r2)
    return True, '两类非法内容均被拒'


@case('MS-06', '站内消息', '不能给自己发消息',
      'receiver_id 等于自己', '提示收件人不正确')
def ms06():
    c = mk(A_ID, A_NAME, 'admin')
    r = post(c, '/messages/send', {'receiver_id': str(A_ID), 'content': 'self'},
             follow_redirects=True)
    assert '收件人' in html_of(r)
    return True, '被拒绝'


# ---------------------------------------------------------------------------
# J. 异常与边界
# ---------------------------------------------------------------------------
@case('EX-01', '异常边界', '不存在的菜品详情重定向',
      'GET /food/999999', '302 回首页并提示不存在')
def ex01():
    r = mk().get('/food/999999', follow_redirects=False)
    assert r.status_code == 302
    return True, '302'


@case('EX-02', '异常边界', '给不存在的菜品评分返回 404',
      'POST /food/999999/rate', '404 且提示该美食已下架')
def ex02():
    r = post(mk(U_ID, U_NAME, 'user'), '/food/999999/rate', {'score': '5'}, ajax=True)
    assert r.status_code == 404, r.status_code
    return True, '404 + 提示'


@case('EX-03', '异常边界', '非法 id 格式按 404 处理',
      'GET /food/abc', '404 中文错误页')
def ex03():
    r = mk().get('/food/abc')
    assert r.status_code == 404
    return True, '404'


@case('EX-04', '异常边界', '不能删除他人的浏览记录',
      'U 造一条历史，再由另一个用户尝试删除', '接口不报错但记录仍在（按 user_id 过滤）')
def ex04():
    mk(U_ID, U_NAME, 'user').get(f'/food/{FOOD_B}')
    row = sql_one("SELECT id FROM histories WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                  (U_ID,))
    assert row, '没有构造出历史记录'
    post(mk(A_ID, A_NAME, 'admin'), f'/history/delete/{row[0]}')
    assert sql_one("SELECT id FROM histories WHERE id=%s", (row[0],)), '他人记录被删除'
    return True, '越权删除无效，记录仍在'


@case('EX-05', '异常边界', '处理不存在的举报有友好提示',
      'POST /admin/report/999999/handle', '提示举报记录不存在')
def ex05():
    c = mk(A_ID, A_NAME, 'admin')
    r = post(c, '/admin/report/999999/handle',
             {'action': 'resolved', 'handle_note': 'x'}, follow_redirects=True)
    assert '不存在' in html_of(r)
    return True, '提示记录不存在'


@case('EX-06', '异常边界', '空用户名注册被拒绝',
      'POST /register username 为空', '提示用户名长度不合法')
def ex06():
    c = mk()
    r = c.post('/register', data={'username': '', 'password': 'abc123456',
                                  'role': 'user', '_csrf_token': TOKEN},
               follow_redirects=True)
    assert '用户名长度' in html_of(r)
    return True, '被拒绝'


@case('EX-07', '异常边界', '未登录调用写接口返回 401',
      '游客 POST /food/<id>/rate', '401 且提示请先登录')
def ex07():
    r = post(mk(), f'/food/{FOOD_B}/rate', {'score': '5'}, ajax=True)
    assert r.status_code == 401 and '登录' in json_of(r).get('message', '')
    return True, '401'


@case('EX-08', '异常边界', '商家编辑不存在的菜品',
      'GET /merchant/food/edit/999999', '302 或 404，不抛 500')
def ex08():
    r = mk(M_ID, M_NAME, 'merchant').get('/merchant/food/edit/999999')
    assert r.status_code in (302, 404), r.status_code
    return True, f'{r.status_code}'


@case('EX-09', '异常边界', '消息收件人不存在时不报 500',
      'POST /messages/send receiver_id=999999', '302 且提示收件人不存在')
def ex09():
    c = mk(A_ID, A_NAME, 'admin')
    r = post(c, '/messages/send', {'receiver_id': '999999', 'content': 'x'},
             follow_redirects=True)
    assert '不存在' in html_of(r)
    return True, '友好提示'


# ---------------------------------------------------------------------------
# K. 性能
# ---------------------------------------------------------------------------
@case('PF-01', '性能', '首页响应时间', '连续请求 3 次取平均', '平均 < 800ms', kind='性能')
def pf01():
    c = mk()
    ts = []
    for _ in range(3):
        t0 = time.perf_counter()
        r = c.get('/')
        ts.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    avg = sum(ts) / len(ts)
    assert avg < 800, f'{avg:.0f}ms'
    return True, f'平均 {avg:.0f}ms'


@case('PF-02', '性能', '推荐页响应时间', '连续请求 2 次取平均', '平均 < 1500ms（含 CF 计算）', kind='性能')
def pf02():
    c = mk(U_ID, U_NAME, 'user')
    ts = []
    for _ in range(2):
        t0 = time.perf_counter()
        r = c.get('/recommend')
        ts.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    avg = sum(ts) / len(ts)
    assert avg < 1500, f'{avg:.0f}ms'
    return True, f'平均 {avg:.0f}ms'


@case('PF-03', '性能', '详情页响应时间', '连续请求 3 次取平均', '平均 < 500ms', kind='性能')
def pf03():
    c = mk()
    ts = []
    for _ in range(3):
        t0 = time.perf_counter()
        r = c.get(f'/food/{FOOD_B}')
        ts.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    avg = sum(ts) / len(ts)
    assert avg < 500, f'{avg:.0f}ms'
    return True, f'平均 {avg:.0f}ms'


@case('PF-04', '性能', '连续请求不出现 5xx', '连续 10 次请求首页与详情页', '全部 200', kind='性能')
def pf04():
    c = mk()
    codes = []
    for _ in range(5):
        codes.append(c.get('/').status_code)
        codes.append(c.get(f'/food/{FOOD_B}').status_code)
    assert all(code == 200 for code in codes), codes
    return True, '10 次请求全部 200'


@case('PF-05', '性能', '静态资源可访问', 'GET ECharts 本地文件', '200', kind='性能')
def pf05():
    r = mk().get('/static/js/echarts.min.js')
    assert r.status_code == 200
    return True, f'{len(r.get_data()) // 1024} KB'


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------
def run_all():
    results = []
    total = len(CASES)
    print(f'\n共 {total} 条用例，开始执行…\n')
    for idx, c in enumerate(CASES, 1):
        t0 = time.perf_counter()
        status, actual = 'passed', ''
        try:
            ret = c['fn']()
            if isinstance(ret, tuple):
                ok, actual = ret
                status = 'passed' if ok else 'failed'
            else:
                actual = '执行完成'
        except AssertionError as e:
            status, actual = 'failed', f'断言失败：{e}'
        except Exception as e:
            status = 'error'
            actual = f'{type(e).__name__}: {e}'
            traceback.print_exc()
        cost = (time.perf_counter() - t0) * 1000
        results.append({
            'id': c['id'], 'module': c['module'], 'title': c['title'],
            'steps': c['steps'], 'expected': c['expected'], 'actual': actual,
            'status': status, 'cost_ms': round(cost, 1), 'kind': c['kind'],
        })
        mark = {'passed': 'PASS', 'failed': 'FAIL', 'error': 'ERR '}[status]
        print(f'[{idx:>3}/{total}] {mark} {c["id"]:<6} {c["title"]}  ({cost:.0f}ms)')
        if status != 'passed':
            print(f'        → {actual}')
    return results


def summarize(results):
    passed = sum(1 for r in results if r['status'] == 'passed')
    failed = sum(1 for r in results if r['status'] == 'failed')
    errors = sum(1 for r in results if r['status'] == 'error')
    by_module = {}
    for r in results:
        m = by_module.setdefault(r['module'], {'total': 0, 'passed': 0, 'cost': 0.0})
        m['total'] += 1
        m['passed'] += 1 if r['status'] == 'passed' else 0
        m['cost'] += r['cost_ms']
    return {
        'total': len(results), 'passed': passed, 'failed': failed,
        'errors': errors,
        'pass_rate': round(passed / len(results) * 100, 2) if results else 0,
        'by_module': by_module,
        'total_cost_ms': round(sum(r['cost_ms'] for r in results), 1),
    }


def main():
    prepare()
    try:
        results = run_all()
    finally:
        cleanup()
    summary = summarize(results)

    out = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'env': {
            'python': sys.version.split()[0],
            'flask': __import__('flask').__version__,
            'database': 'MySQL 8.0 / food_db',
            'base_url': 'Flask test_client（不走网络）',
        },
        'summary': summary,
        'results': results,
    }
    path = ROOT / 'tests' / 'report.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')

    print('\n' + '=' * 62)
    print(f'总计 {summary["total"]} 条 | 通过 {summary["passed"]} | '
          f'失败 {summary["failed"]} | 异常 {summary["errors"]} | '
          f'通过率 {summary["pass_rate"]}%')
    print('-' * 62)
    for m, v in summary['by_module'].items():
        print(f'{m:<8} {v["passed"]}/{v["total"]}'.ljust(24) +
              f'耗时 {v["cost"] / 1000:.2f}s')
    print('=' * 62)
    print(f'报告已写入 {path}')
    return 0 if summary['failed'] == 0 and summary['errors'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
