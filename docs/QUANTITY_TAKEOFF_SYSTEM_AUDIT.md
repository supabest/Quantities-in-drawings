# 装修图纸智能识别与工程量计算系统 — 代码库审计报告

> **审计日期**: 2026-08-09
> **审计版本**: v3.1（基于 `decoration-quantity` 仓库）
> **审计范围**: 全量代码、模板、静态资源、配置、部署

---

## 一、项目结构总览

```
decoration-quantity/
├── app/
│   ├── __init__.py          # Flask 应用工厂、DB/LoginManager 初始化、知识库种子数据
│   ├── models.py            # 核心数据模型（8 表、枚举常量、规范映射）
│   ├── auth.py              # 认证蓝图（登录/注册/管理员用户管理）
│   ├── routes.py            # 主路由蓝图（项目 CRUD/BOQ 查看/报告/知识库）
│   ├── api.py               # REST API 蓝图（上传/处理/BOQ 数据/自检/历史/报告生成）
│   ├── processing.py        # OpenCV 图像处理引擎（v3.1，核心算量逻辑）
│   ├── comparison.py        # 历史清单对照引擎（v3.1，解析/比较/自动补漏）
│   ├── templates/           # 15 个 Jinja2 模板
│   │   ├── base.html        # 基础布局（导航栏/Flash消息/页脚）
│   │   ├── index.html       # 首页（Hero + Features）
│   │   ├── dashboard.html   # 项目列表仪表盘
│   │   ├── project_*.html   # 项目 CRUD 页面（new/detail/edit）
│   │   ├── boq.html         # BOQ 查看（8 分部）
│   │   ├── report*.html     # 报告查看/导出
│   │   ├── knowledge_base*.html  # 知识库管理
│   │   ├── profile.html     # 用户个人中心
│   │   └── auth/            # 认证模板（login/register/admin_users）
│   └── static/
│       ├── css/style.css    # 完整 CSS（603 行，响应式）
│       ├── js/main.js       # 基础 JS（52 行，导航切换/Flash自消失/确认框）
│       ├── uploads/         # 上传图纸存储
│       ├── reports/         # 生成的 HTML 报告
│       └── knowledge_base.docx  # 原始知识库文档
├── config.py                # 配置类（单一类，环境变量优先）
├── run.py                   # 入口（gunicorn 兼容）
├── requirements.txt         # Python 依赖（14 个包）
├── Dockerfile               # Docker 构建（python:3.9-slim + gunicorn）
├── docker-compose.yml       # Docker 编排（单服务 + volume 挂载）
├── deploy/README.md         # 部署指南（Docker + Nginx + 直接部署）
├── migrations/              # 空目录（数据库迁移占位）
├── tests/                   # 空目录（测试占位）
└── venv/                    # Python 虚拟环境
```

---

## 二、模块逐项审计

### 2.1 数据模型（`models.py`）— 评分：⭐⭐⭐⭐ 可复用

**已有表：**

| 表名 | 用途 | 审计 |
|------|------|------|
| `users` | 用户认证 | ✅ 完善：审核制注册、管理员角色、软禁用 |
| `projects` | 项目 | ✅ 包含 project_type、default_floor_height、scale、status |
| `drawings` | 图纸 | ✅ 包含 drawing_type、annotated_path、process_order |
| `measurements` | 工程量清单项 | ✅ **已有 30+ 字段**，覆盖 label/division/category/source_tag/confidence/position/formula/project_features/base_layer/remarks |
| `reports` | 报告 | ✅ HTML 报告，含 summary_json |
| `historical_projects` | 历史项目 | ✅ 支持项目类型、summary_json |
| `knowledge_base_entries` | 知识库 | ✅ 支持 category/status/merged_to（知识库版本管理） |
| `self_check_items` | 自检清单 | ✅ 20 项标准自检项，版本标记 |

**枚举常量（已有且高质量）：**
- `DIVISIONS[8]` — 8 大分部工程（名称+单位+材质列表）
- `SOURCE_TAGS[6]` — 来源标签（图纸实测/几何推算/历史参照/经验补全/逻辑推理/待确认）
- `CONFIDENCE_LEVELS[3]` — 置信度等级（高/中/低，含使用说明）
- `PROJECT_TYPES[4]` — 项目类型（住宅/商业/大堂/售楼部）
- `BASE_LAYER_RULES` — 基层做法映射（10 种材质→做法描述）
- `HIDDEN_ITEMS[11]` — 隐含项目（水电点位、防水层、找平层、批腻子）

**缺失字段/表（需要新增）：**

| 缺失项 | 说明 | 优先级 |
|--------|------|--------|
| `drawing_pages` | 多页图纸支持（PDF 多页） | 🔴 高 |
| `drawing_elements` | CAD/PDF 解析后的几何元素 | 🔴 高 |
| `drawing_relations` | 图纸间关联（平面↔立面↔天花↔大样） | 🔴 高 |
| `rooms/walls/doors/windows` | 独立的空间构件表 | 🔴 高 |
| `floor_regions/wall_regions/ceiling_regions` | 材料区域表 | 🔴 高 |
| `cabinets/components` | 固定柜体/构件表 | 🟡 中 |
| `component_relations` | 构件间关联 | 🟡 中 |
| `dimensions` | 尺寸标注独立存储 | 🟡 中 |
| `geometries` | 几何数据（Polygon WKT/GeoJSON） | 🔴 高 |
| `quantity_tasks` | 算量任务（Orchestrator/Worker 状态） | 🟡 中 |
| `quantity_calculations` | 计算过程追溯（公式+步骤） | 🔴 高 |
| `quantity_audits` | 审计记录 | 🔴 高 |
| `quantity_review_logs` | 人工复核日志 | 🟡 中 |
| `historical_quantity_items` | 历史项目标准化清单项 | 🔴 高 |
| `historical_quantity_features` | 标准化特征向量 | 🔴 高 |
| `historical_project_templates` | 项目类型模板 | 🔴 高 |
| `historical_completeness_rules` | 漏项规则 | 🔴 高 |
| Measurement 缺 `confidence_score` (0-100) | 当前只有 level 枚举 | 🔴 高 |
| Measurement 缺 `confidence_reasons[]` | 置信度评分依据 | 🔴 高 |
| Measurement 缺 `source_geometry` | 几何来源数据 | 🔴 高 |

### 2.2 图像处理引擎（`processing.py`）— 评分：⭐⭐ 需重建

**当前能力：**

| 功能 | 实现方式 | 准确性评估 |
|------|---------|-----------|
| 房间检测 | OpenCV 轮廓分析（灰度→二值化→形态学→轮廓） | ⚠️ 仅适合简单像素图，无法处理 CAD 线条图 |
| 门窗检测 | Hough 线检测 + 长度过滤 | ⚠️ 误检率高，无法区分门/窗/其他线段 |
| 房间分类 | 面积+长宽比例推断 | ⚠️ 基于经验规则，不可靠 |
| 墙面面积 | 周长×层高 - 洞口（几何推算） | ✅ 计算逻辑正确，但依赖房间检测精度 |
| 天棚面积 | = 地面面积（水平投影） | ✅ 逻辑正确 |
| 踢脚线 | 周长 - 门洞 - 柜体 | ✅ 逻辑正确 |
| 隐含项目 | 按房间类型经验补全 | ✅ 合理但需要更结构化 |
| 立面分析 | 整个图像当一面墙（粗糙估算） | ❌ 基本不可用 |
| 图纸标注 | 轮廓+文字 overlay | ✅ 可视化效果尚可 |

**核心问题：**
1. **仅支持像素图像**（PNG/JPG/BMP/TIFF），不支持 DWG/DXF/PDF
2. **所有几何数据来自 OpenCV 轮廓分析**，无 CAD 精度
3. **无多源融合**（CAD 几何 + PDF 语义）
4. **无图纸类型自动识别**
5. **无平面/立面/天花/大样关联**
6. **比例尺不精确**（`DEFAULT_PIXELS_PER_METER = 100`）
7. **房间分类完全靠面积推断**，不读文字标注

**复用建议：**
- `_generate_derived_measurements()` — 衍生项计算逻辑可迁移到 QuantityRuleEngine
- `_generate_hidden_items()` — 隐含项目逻辑可迁移到 HistoricalCompletenessAgent
- 标注图片生成 — 可复用为高亮区域功能

### 2.3 历史清单对比引擎（`comparison.py`）— 评分：⭐⭐⭐ 可扩展

**已有能力：**
- Excel 解析（`openpyxl`，支持中文列头）
- 分部自动归类（关键词匹配）
- 历史 vs 当前对比（模糊匹配）
- 自动补漏（`source_tag = '历史参照'`）
- 合理性统计（墙地比、灯光密度、插座密度）

**不足：**
- 对比基于关键词字符串匹配，不是特征向量匹配
- 无标准化特征抽取（category/material/thickness/finish/location）
- 无项目类型模板
- 无系统性漏项规则引擎

**复用建议：**
- 整体逻辑可升级为 HistoricalCompletenessAgent 的基础
- Excel 解析可独立为 `HistoricalBOQParser`

### 2.4 路由层

#### `routes.py` — 评分：⭐⭐⭐ 可复用
- 标准 Flask 蓝图模式
- 项目 CRUD 完整
- BOQ 查看（8 分部组织）
- 知识库 CRUD
- ✅ 代码清晰，可直接复用为基础

#### `api.py` — 评分：⭐⭐⭐ 需扩展
- 图纸上传（含 drawing_type 自动检测）
- 处理触发（同步）
- 历史清单上传/解析/对比/补漏
- 自检清单 CRUD
- 报告生成（`_render_report_html`）
- ❌ 全是同步处理，大文件会超时
- ❌ 无任务队列、无进度上报

#### `auth.py` — 评分：⭐⭐⭐⭐ 可直接复用
- Flask-Login 标准集成
- 审批制注册
- 管理员用户管理（审批/启用禁用/删除）
- ✅ 功能完整，无需修改

### 2.5 前端

#### 模板（15 个 HTML 文件）— 评分：⭐⭐⭐ 可复用
- Jinja2 模板，原生 HTML
- 响应式设计（移动端适配）
- 状态 badge、进度条、modal、拖拽上传
- ✅ 基础 UI 框架可复用
- ❌ 无前端框架（无 React/Vue），不利于交互密集型功能
- ❌ 人工复核工作台需要大量交互（高亮区域、修改、确认/驳回），纯 SSR 难以实现

#### CSS（`style.css`）— 评分：⭐⭐⭐⭐ 良好基础
- 603 行，CSS 变量体系
- 完整的组件库（按钮/卡片/表格/表单/badge/pagination/modal）
- 响应式断点
- ✅ 设计系统可复用

#### JavaScript（`main.js`）— 评分：⭐ 极简
- 仅 52 行：导航切换、Flash 消失、文件大小格式化
- ❌ 无前端框架，无 API 调用封装
- ❌ 交互功能严重不足

### 2.6 配置与部署

#### `config.py` — 评分：⭐⭐⭐ 够用
- 环境变量优先 + 默认值降级
- SQLite 默认 / PostgreSQL 可切换
- 上传限制 32MB
- 允许文件类型含 `dxf`（已在列表中但未被处理引擎使用）
- ❌ 缺少日志配置
- ❌ 缺少 Celery/任务队列配置
- ❌ 缺少 CAD 工具路径配置

#### Docker — 评分：⭐⭐⭐ 可用但需扩展
- 单容器部署，Gunicorn 2 workers
- Volume 挂载 db/uploads/reports
- JSON-file 日志驱动（10MB×3 滚动）
- ❌ 无 CAD 解析工具的 Docker 方案
- ❌ 无 worker 容器（任务处理）
- ❌ 无 PostgreSQL 容器选项

### 2.7 测试 — 评分：❌ 缺失
- `tests/` 目录为空
- 无任何单元测试、集成测试、端到端测试
- 无测试数据（图纸、标准答案）

### 2.8 日志 — 评分：❌ 缺失
- 无应用级日志
- 仅 Docker JSON-file 日志驱动
- 无结构化日志（JSON 格式）
- 无审计轨迹

### 2.9 AI 调用 — 评分：❌ 未集成
- 当前项目完全没有 AI/LLM 调用
- 所有"智能"都是硬编码规则

### 2.10 Supabase / Cloudflare — 评分：❌ 未使用
- 当前无任何云服务集成
- 纯自托管（SQLite + Docker）

---

## 三、复用/替换/新增决策矩阵

### ✅ 可直接复用
| 模块 | 复用方式 |
|------|---------|
| `models.py` User/Project/Drawing/Report/SelfCheckItem | 保留并扩展 |
| `auth.py` | 完整保留 |
| `routes.py` 基础 CRUD | 保留，扩展 BOQ 和审核路由 |
| `config.py` | 保留并扩展 |
| 前端模板 base/dashboard/auth | 保留布局，增强交互 |
| CSS 设计系统 | 完整保留 |
| Docker 部署 | 保留并扩展 |
| `DIVISIONS/SOURCE_TAGS/CONFIDENCE_LEVELS` 枚举 | 完整保留 |

### 🔧 需要重构
| 模块 | 重构方向 |
|------|---------|
| `processing.py` | 拆分为：CAD 解析器 + PDF 解析器 + 空间理解引擎 + 几何计算引擎 |
| `comparison.py` | 升级为 HistoricalCompletenessAgent + HistoricalQuantityKnowledgeBase |
| `api.py` | 从同步改为异步任务队列，增加 WebSocket 进度推送 |
| `Measurement` 模型 | 增加 confidence_score/reasons/source_geometry 字段 |

### 🆕 需要新增
| 模块 | 说明 |
|------|------|
| CAD 解析引擎 | DWG/DXF → 结构化几何数据 |
| PDF 解析引擎 | PDF → 语义数据（文字/材料/饰面） |
| DrawingUnifiedModel | 多源图纸统一数据模型 |
| 图纸类型识别器 | 自动分类图纸类型 |
| 空间理解引擎 | 房间/墙/门/窗/区域识别 |
| 图纸关联引擎 | 平面↔立面↔天花↔大样 |
| QuantityRuleEngine | 8 分部分项计算规则引擎 |
| HistoricalQuantityKnowledgeBase | 历史项目标准化特征数据库 |
| HistoricalCompletenessAgent | 漏项检查 |
| QuantityAuditAgent | 三层审计 |
| 人工复核 Workbench | 前端交互式审核界面 |
| 任务队列 | Celery/RQ + Redis |
| 日志系统 | 结构化日志 + 审计轨迹 |
| 测试体系 | 单元测试 + 集成测试 + 测试数据 |

---

## 四、与未来套价系统的接口预留

当前 `Measurement.to_dict()` 已经是接近标准输出的格式：

```python
{
    'id': ...,
    'label': '客厅-ST-01石材地面',
    'division_id': 1,
    'area': 23.68,
    'unit': 'm²',
    'source_tag': '图纸实测',
    'confidence_level': 'high',
    'position_desc': '客厅-地面，A-01 P3',
    'formula_text': '5.2×4.55 = 23.68m²',
    'project_features': 'ST-01 天然石材',
    'material_code': 'ST-01',
    'remarks': '...',
}
```

需增加的 API 端点：
- `GET /api/v1/projects/{id}/boq` — 标准化 BOQ 输出
- `GET /api/v1/projects/{id}/boq/items` — 分页清单项
- `POST /api/v1/projects/{id}/boq/export` — 触发 Excel 导出

套价系统通过 HTTP API 读取，不与本系统共享数据库。

---

## 五、结论

当前项目是一个**功能性的装修图纸算量 MVP**（v3.1），核心价值在于：

1. **完善的数据模型和 8 分部体系** — 已经是领域建模的最佳实践
2. **工程量计算规则** — 衍生项计算（墙面/天棚/踢脚线/隐含项）逻辑正确
3. **历史清单对比** — 已有基本的补漏对比能力
4. **完整的 Web 应用骨架** — 认证/CRUD/UI/Docker 部署就绪

但作为专业的"图纸智能识别"系统，存在以下根本性不足：

1. **无 CAD 数据源支持** — 仅基于 OpenCV 像素分析，精度不足
2. **无 PDF 解析** — 无法读取材料标注、设计说明
3. **无多源融合** — CAD 几何 + PDF 语义未打通
4. **无图纸关联** — 平面/立面/天花/大样孤立处理
5. **无 AI 调用** — 所有决策靠硬编码规则
6. **无日志/测试/审计** — 不可观测、不可验证

**建议开发策略：不重写，而是在现有骨架的基础上渐进式重建核心引擎。**

Phase 1-10 计划中，Phase 1-3（CAD/PDF 解析 + 统一模型）是决定系统价值的关键。
