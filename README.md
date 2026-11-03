# 广东美食协同过滤推荐系统

> 基于 Python + Flask 的广东美食智能推荐系统毕业设计

## 项目简介

本系统是一个面向广东特色美食的智能推荐平台，采用**基于用户的协同过滤（User-based CF）算法**，结合余弦相似度与 K 近邻（K=5）为用户推荐个性化美食。系统提供用户、商家、管理员三类角色，覆盖美食浏览、评分、点赞、收藏、评论、举报等完整交互，并内置推荐算法效果评估模块（MAE/RMSE）。

## 技术栈

| 类别 | 技术 |
|------|------|
| 后端框架 | Python 3.11 + Flask 3.1 |
| 数据库 | MySQL 8.0 + PyMySQL |
| 推荐算法 | User-based 协同过滤（余弦相似度 + KNN） |
| 密码加密 | Werkzeug（`generate_password_hash`） |
| 可视化 | ECharts 5.4（本地引入） |
| 机器学习 | scikit-learn / numpy / pandas |

## 核心功能

### 用户端
- 注册 / 登录 / 退出（含商家注册，密码加密存储）
- 首页浏览、关键词搜索（含同义词扩展）、分类筛选
- 美食详情、评分（1-5 星）、点赞、收藏、评论
- 浏览记录、个人中心、过敏/忌口设置、黑名单管理、我的举报

### 商家端
- 商家注册（需管理员审核）
- 数据看板（ECharts 柱状图：本店美食评分 + 点赞排行）
- 美食上传、编辑、下架

### 管理员端
- 数据看板（ECharts 饼图/柱状图/折线图）
- 商家审核、美食审核、用户管理
- 举报管理（含处理弹窗与处理说明）

### 推荐算法
- User-based 协同过滤（余弦相似度、K=5 近邻、Top-20 推荐）
- 冷启动（新用户评分不足时热门美食兜底）
- 过敏强制过滤、忌口主动标注、黑名单双维度过滤
- 效果评估（留出法 20%、MAE/RMSE、K 值对比）

## 项目结构

```
gd_food_recommend/
├── app.py                  # Flask 主入口
├── config.py               # 数据库配置（支持环境变量）
├── requirements.txt        # 依赖清单
├── create_tables.py        # 建表脚本（10 张表）
├── insert_data.py          # 测试数据脚本
├── models/
│   └── user.py             # 用户数据操作
├── routes/
│   ├── auth.py             # 注册/登录/退出
│   ├── food.py             # 详情/评分/点赞/收藏/评论/举报
│   ├── merchant.py         # 商家端
│   ├── admin.py            # 管理员端
│   ├── user.py             # 个人中心/收藏/黑名单/过敏忌口
│   └── recommend.py        # 协同过滤推荐 + 冷启动 + 评估
├── utils/
│   ├── db.py               # get_db_connection()
│   └── cf.py               # 协同过滤算法 + MAE/RMSE 评估
├── templates/              # Jinja2 模板
└── static/js/
    └── echarts.min.js      # ECharts 本地引入
```

## 数据库设计（10 张表）

| 表名 | 用途 |
|------|------|
| `users` | 用户（user / merchant / admin），含过敏、忌口、商家审核状态 |
| `categories` | 美食分类（9 个广东特色品类） |
| `foods` | 美食（价格、原材料、口味描述、审核状态等） |
| `ratings` | 评分（1-5 星，user_id + food_id 唯一） |
| `likes` | 点赞 |
| `favorites` | 收藏 |
| `comments` | 评论 |
| `histories` | 浏览记录 |
| `blacklists` | 黑名单（支持美食 / 商家双维度） |
| `reports` | 举报（美食 / 评论） |

## 运行方式

### 1. 安装依赖

```bash
cd gd_food_recommend
pip install -r requirements.txt
```

### 2. 初始化数据库

在 MySQL 中创建数据库 `food_db`，然后执行：

```bash
python create_tables.py   # 建表
python insert_data.py     # 插入测试数据
```

### 3. 配置数据库连接

数据库连接信息在 `config.py` 中，默认值如下，可通过环境变量覆盖：

| 配置项 | 环境变量 | 默认值 |
|--------|----------|--------|
| 主机 | `DB_HOST` | `localhost` |
| 用户名 | `DB_USER` | `root` |
| 密码 | `DB_PASSWORD` | `123456` |
| 数据库名 | `DB_NAME` | `food_db` |

### 4. 启动

```bash
python app.py
```

访问 <http://127.0.0.1:5000>

## 测试账号

| 角色 | 用户名 | 密码 |
|------|--------|------|
| 管理员 | admin | 123456 |
| 商家 | 陈记肠粉店 / 镛记烧鹅 | 123456 |
| 普通用户 | user1 / user2 / user3 / testuser | 123456 |

## 推荐算法说明

- **算法**：User-based 协同过滤
- **相似度**：余弦相似度（Cosine Similarity）
- **近邻数**：K = 5
- **推荐数**：Top-20
- **冷启动**：新用户（评分 < 2 条）时用热门美食兜底
- **评估**：留出法（20% 测试集），MAE / RMSE 指标，支持 K=3/5/7/10 对比
