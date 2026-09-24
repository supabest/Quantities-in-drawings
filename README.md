# 装修图纸自动计量系统

基于计算机视觉的 Web 应用，自动识别装修平面图中的房间、墙体、门窗等构件，计算面积、周长、数量等工程数据，一键生成计量报告。

## 功能特性

- **用户认证系统** — 账户申请审批制，保护知识产权
- **图纸上传与管理** — 支持 PNG、JPG、BMP、TIFF 格式
- **自动计量** — OpenCV 计算机视觉管线自动分析图纸
  - 🏠 房间检测与面积、周长计算
  - 🧱 墙体长度检测
  - 🚪 门窗识别与计数
  - 📊 构件统计
- **图纸标注** — 在原图上叠加标注结果（面积标签、轮廓线）
- **完整报告** — HTML 格式计量报告，支持下载
- **管理员面板** — 用户审批、启用/禁用、删除
- **轻量部署** — 支持 Docker 一键部署到轻量云

## 快速开始

### 本地开发

```bash
# 1. 进入项目目录
cd decoration-quantity

# 2. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# venv\Scripts\activate   # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 运行
python3 run.py
```

访问 http://localhost:5000

### Docker 部署

```bash
docker-compose up -d
```

详细部署指南请参考 [deploy/README.md](deploy/README.md)。

## Phase 5a · PostgreSQL 本地开发环境

> ⚠️ **端口说明**：本机若已跑着别的 PostgreSQL（如套价系统）会占用 5432，
> 因此 compose 默认把容器映射到宿主机 **5433**（容器内仍 5432）。连接串一律用
> `127.0.0.1:5433`（显式 IPv4，避开 `localhost`→IPv6 的解析串扰）。

```bash
# 1. 启动 PG
docker compose up -d db

# 2. 建表（跑 Alembic 基线，跳过默认 seed）
export DATABASE_URL=postgresql://quant:quant_dev_pw@127.0.0.1:5433/decoration_quant
SKIP_DB_CREATE_ALL=1 FLASK_APP=run.py python -m flask db upgrade

# 3.（可选）从现有 app.db 导入数据
python scripts/migrate_sqlite_to_pg.py --truncate

# 4. 启动应用（自动 seed admin 与知识库）
python run.py
```

**E2E 测试切换 PG**（默认仍走 SQLite，无依赖可跑）：
```bash
export TEST_DATABASE_URL=postgresql://quant:quant_dev_pw@127.0.0.1:5433/decoration_quant_test
python tests/test_orchestrator_e2e.py
```

**开发日常命令**：
```bash
python -m flask db migrate -m "描述"   # 自动写新迁移
python -m flask db upgrade               # 应用迁移
python -m flask db stamp head            # 将已有库标记为已迁移（一次性）
```

> 若 venv 是从别处移动过来的，`flask`/`python` 裸命令可能失效，改用绝对路径
> `venv/bin/python -m flask ...` 与 `venv/bin/python ...`。

## 默认管理员

| 邮箱 | 密码 |
|------|------|
| admin@decoration-quant.com | admin123456 |

⚠️ **生产环境请务必修改默认密码！**

## 技术栈

- **后端**：Python 3.9 / Flask / SQLAlchemy
- **前端**：HTML / CSS / JavaScript (Jinja2 模板)
- **数据库**：SQLite（开发）/ PostgreSQL（生产）
- **图像处理**：OpenCV / NumPy
- **部署**：Docker / Gunicorn / Nginx

## 项目结构

```
decoration-quantity/
├── app/
│   ├── __init__.py      # Flask 应用工厂
│   ├── models.py        # 数据模型
│   ├── auth.py          # 认证路由
│   ├── routes.py        # 主路由
│   ├── api.py           # API 路由
│   ├── processing.py    # 图像处理引擎
│   ├── templates/       # HTML 模板
│   └── static/          # 静态文件
│       ├── css/
│       ├── js/
│       ├── uploads/     # 上传的图纸
│       └── reports/     # 生成的报告
├── deploy/              # 部署文档
├── Dockerfile
├── docker-compose.yml
├── config.py            # 配置
└── run.py               # 入口
```

## 处理流程

1. **上传图纸** — 拖拽或选择平面图
2. **图像预处理** — 灰度化、去噪、二值化、形态学操作
3. **轮廓检测** — 查找房间边界轮廓，过滤噪声
4. **线检测** — Hough 变换检测墙体线段
5. **构件识别** — 检测门窗等构件
6. **标注叠加** — 在原图上绘制检测结果
7. **报告生成** — 汇总所有测量数据，生成 HTML 报告
