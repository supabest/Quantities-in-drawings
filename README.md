# 装修图纸自动计量系统

装修/机电工程量自动计算（takeoff）项目，包含**两条技术路线**：

| 路线 | 位置 | 输入 | 状态 |
|---|---|---|---|
| **矢量算量主线** | `modeling/` + `app/orchestrator/` + 手册 `docs/QUANTITY_TAKEOFF_MANUAL.md` | DXF（模型空间）+ 施工图纸 PDF（矢量 drawings/文字） | **主力**：227户型墙/地/天/固装/机电全专业已封账 |
| 位图 Web 应用 | `app/processing.py` + Flask 模板 | 位图平面图（PNG/JPG/…） | 遗留管线：仅适用无矢量源扫描件 |

## 核心纪律（详见算量手册第一部分）

- **几何是数值唯一来源**；BOQ/清单仅作列项与单位模板，生产对账不依赖 BOQ。
- **三态账目**：已确认 / 图上没有（走单方估算并披露）/ 未能读取（挂账），每行带计算式。
- 估算（D 层单方）永不与图纸实测混账。

## 矢量算量主线

- **建模**：`modeling/run_model.py` 由定稿 DXF 提取墙/房间/门窗，产出
  `outputs/model/` 下 GLB / IFC / DXF 与各专业 takeoff 表（楼面/天花/墙面/固装）。
- **图纸识读**：`app/orchestrator/` 对整套施工图纸 PDF 做页分析、标定（px/m 校验）、
  分区（zone）、覆盖度与审计；`app/orchestrator/mep/` 沉淀 MEP 能力
  （`registration` 配准+归属索引，`counts` 文字锚/回路解析）。
- **机电 MEP 算量（M1→M4 封账）**：
  `outputs/model/mep_legends.md`（图例建档）→ `mep_takeoff_m2.md`（点位计数 +
  DXF↔PDF 配准房间归属）→ `mep_takeoff_m3.md`（照明回路拓扑）→ `mep_takeoff_m4.md`
  （对账定案 + 三态总账）。
- **回归基准**：`tests/test_mep_golden.py` 以封账数字为 golden set，改动提取算法后
  自动比对（图纸在库本地时可全量复算，否则跑工件一致性层）。

## Web 应用（位图管线）

- 用户认证（账户申请审批制）、图纸上传、OpenCV 自动分析（轮廓=房间面积/周长、
  Hough=墙长、门窗计数）、标注叠加、HTML 报告、管理员面板。
- 处理流程：上传 → 预处理（灰度/去噪/二值化/形态学）→ 轮廓检测 → 线检测 →
  构件识别 → 标注叠加 → 报告生成。
- 注意：位图管线无语义、无比例标定，精度有天然上限；有 DXF/矢量 PDF 时一律走主线。

## 快速开始

### 本地开发

```bash
cd decoration-quantity
python3 -m venv venv
source venv/bin/activate  # macOS/Linux；Windows: venv\Scripts\activate
pip install -r requirements.txt
python3 run.py            # http://localhost:5000
```

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

## 管理员账号（无默认密码）

系统**不再内置默认密码**。首次启动 seed admin 时：

- 设置了环境变量 `ADMIN_PASSWORD` → 直接使用；
- 未设置 → 自动生成**一次性随机密码，仅在启动日志打印一次**，请立即登录修改。

```bash
export ADMIN_EMAIL="you@example.com"
export ADMIN_PASSWORD="强密码"    # 推荐写入 .env（参考 .env.example）
```

## 技术栈

- **后端**：Python 3.9 / Flask / SQLAlchemy（Alembic 迁移，SQLite↔PG）
- **矢量解析**：PyMuPDF（PDF drawings/text）、ezdxf、shapely、numpy/OpenCV（栅格辅助目视验证）
- **前端**：HTML / CSS / JavaScript (Jinja2 模板)
- **部署**：Docker / Gunicorn / Nginx

## 项目结构

```
decoration-quantity/
├── app/
│   ├── __init__.py      # Flask 应用工厂（含 admin seed）
│   ├── models.py        # 数据模型
│   ├── auth.py          # 认证路由
│   ├── routes.py        # 主路由
│   ├── api.py           # API 路由
│   ├── processing.py    # 位图图像管线（遗留路线）
│   ├── orchestrator/    # 矢量图纸识读/标定/分区/审计（主线）
│   ├── templates/       # HTML 模板
│   └── static/          # 静态文件（uploads/ reports/）
├── modeling/            # DXF→模型与各专业 takeoff（主线）
├── docs/                # 算量手册与专题文档
├── outputs/model/       # 封账产出（各专业 takeoff 表、MEP M1-M4 报告）
├── scripts/             # 健康检查/迁移等工具脚本
├── tests/               # E2E 与 golden 回归测试
├── deploy/              # 部署文档
├── migrations/          # Alembic
├── Dockerfile / docker-compose.yml
├── config.py            # 配置
└── run.py               # 入口
```

> 原始图纸目录 `装修图纸/` 体积大且涉密，已在 `.gitignore` 中排除，不入库。
