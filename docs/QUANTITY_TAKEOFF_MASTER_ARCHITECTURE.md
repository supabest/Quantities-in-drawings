# 装修工程图纸智能识别与工程量计算系统 — 总体架构设计

> **文档版本**: v1.0
> **日期**: 2026-08-09
> **设计依据**: 项目任务书 §一 ~ §三十五

---

## 一、系统边界

```
┌────────────────────────────────────────────────────────────────────┐
│                    本系统：图纸解析 + 算量 + 审计                      │
│                                                                    │
│  DWG/DXF/PDF/PNG  ──→  工程量清单  ──→  Excel + 审核工作台           │
│                                                                    │
│                         ❌ 不包含：                                   │
│                          · 套价 / 综合单价                           │
│                          · 定额匹配                                  │
│                          · 材料询价                                  │
│                          · 人工基准价                                │
└────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ 标准 API
                                    ▼
┌────────────────────────────────────────────────────────────────────┐
│                     套价系统（另一个独立 Repo）                         │
│                     通过 API 消费 BOQ 数据                            │
└────────────────────────────────────────────────────────────────────┘
```

---

## 二、核心流水线

```
DWG / DXF / PDF / JPG / PNG
        ↓
┌─ Phase 1 ───────────────────────────────────────────────────┐
│ 多格式图纸解析                                                │
│   DWG → libredwg/ODA → DXF → ezdxf → 结构化几何             │
│   PDF → PyMuPDF + Tesseract → 语义文本                       │
│   图片 → OpenCV 预处理                                       │
└──────────────────────────────┬────────────────────────────────┘
                               ↓
┌─ Phase 2 ───────────────────────────────────────────────────┐
│ CAD 几何 + PDF 语义融合                                       │
│   每个元素标注 source_file / source_page / source_bbox       │
│   → DrawingUnifiedModel                                     │
└──────────────────────────────┬────────────────────────────────┘
                               ↓
┌─ Phase 3 ───────────────────────────────────────────────────┐
│ 空间理解                                                      │
│   房间/区域 → 墙 → 门/窗 → 地面区域 → 天花区域 → 柜体        │
│   图纸类型自动识别                                            │
└──────────────────────────────┬────────────────────────────────┘
                               ↓
┌─ Phase 4 ───────────────────────────────────────────────────┐
│ 图纸关联                                                      │
│   平面 ↔ 立面 ↔ 天花 ↔ 大样                                  │
│   ComponentRelation                                          │
└──────────────────────────────┬────────────────────────────────┘
                               ↓
┌─ Phase 5 ───────────────────────────────────────────────────┐
│ 尺寸标定                                                      │
│   优先级: CAD几何 > 尺寸标注 > 比例尺 > 交叉标定 > AI推算    │
│   无可信尺寸 → 低置信度                                      │
└──────────────────────────────┬────────────────────────────────┘
                               ↓
┌─ Phase 6 ───────────────────────────────────────────────────┐
│ 工程量计算                                                    │
│   QuantityRuleEngine: 8 分部分项确定式计算                    │
│   分区域并行（大项目）                                        │
│   每个计算步骤可追溯                                          │
└──────────────────────────────┬────────────────────────────────┘
                               ↓
┌─ Phase 7 ───────────────────────────────────────────────────┐
│ 三层审计                                                      │
│   ① 几何审计 ② 图纸交叉审计 ③ 历史漏项审计                  │
└──────────────────────────────┬────────────────────────────────┘
                               ↓
┌─ Phase 8 ───────────────────────────────────────────────────┐
│ 置信度评估 + 最终 AI 审计                                     │
└──────────────────────────────┬────────────────────────────────┘
                               ↓
┌─ Phase 9 ───────────────────────────────────────────────────┐
│ 人工复核工作台                                                │
│   查看 → 高亮 → 修改 → 确认/驳回 → 记录审计日志              │
└──────────────────────────────┬────────────────────────────────┘
                               ↓
┌─ Phase 10 ──────────────────────────────────────────────────┐
│ 最终 BOQ → Excel 导出                                         │
│   自动列宽 / 换行 / 冻结首行 / 置信度色标 / 分部汇总           │
└──────────────────────────────────────────────────────────────┘
```

---

## 三、数据库设计（完整）

### 3.1 核心表清单

| 表名 | 说明 | 状态 |
|------|------|------|
| `users` | 用户 | ✅ 已有 |
| `projects` | 项目 | ✅ 已有（需扩展） |
| `drawings` | 图纸 | ✅ 已有（需扩展） |
| `drawing_pages` | 图纸多页（PDF） | 🆕 |
| `drawing_elements` | CAD/PDF 解析后的元素 | 🆕 |
| `drawing_relations` | 图纸间关联 | 🆕 |
| `rooms` | 房间 | 🆕 |
| `walls` | 墙体 | 🆕 |
| `doors` | 门 | 🆕 |
| `windows` | 窗 | 🆕 |
| `floor_regions` | 地面材料区域 | 🆕 |
| `wall_regions` | 墙面材料区域 | 🆕 |
| `ceiling_regions` | 天花材料区域 | 🆕 |
| `cabinets` | 固定柜体 | 🆕 |
| `components` | 构件 | 🆕 |
| `component_relations` | 构件关联 | 🆕 |
| `dimensions` | 尺寸标注 | 🆕 |
| `geometries` | 几何数据（GeoJSON） | 🆕 |
| `measurements` | 工程量清单项 | ✅ 已有（需扩展） |
| `quantity_tasks` | 算量任务 | 🆕 |
| `quantity_calculations` | 计算过程追溯 | 🆕 |
| `quantity_audits` | 审计记录 | 🆕 |
| `quantity_review_logs` | 人工复核日志 | 🆕 |
| `reports` | 报告 | ✅ 已有 |
| `historical_projects` | 历史项目 | ✅ 已有（需扩展） |
| `historical_quantity_items` | 历史标准化清单项 | 🆕 |
| `historical_quantity_features` | 历史特征 | 🆕 |
| `historical_project_templates` | 项目类型模板 | 🆕 |
| `historical_completeness_rules` | 漏项规则 | 🆕 |
| `knowledge_base_entries` | 知识库条目 | ✅ 已有 |
| `self_check_items` | 自检清单 | ✅ 已有 |

### 3.2 核心新表详细定义

```sql
-- 图纸元素表（CAD/PDF 解析的每个实体）
CREATE TABLE drawing_elements (
    id INTEGER PRIMARY KEY,
    drawing_id INTEGER REFERENCES drawings(id),
    page_number INTEGER DEFAULT 1,

    -- 元素标识
    source_type TEXT NOT NULL,       -- 'line'/'polyline'/'hatch'/'text'/'dimension'/'block'/'image'
    source_layer TEXT,               -- CAD 图层名
    source_element_id TEXT,          -- CAD handle / PDF 元素索引
    source_file TEXT,                -- 来源文件
    source_page INTEGER,             -- 来源页码
    source_bbox TEXT,                -- 来源边界框 (JSON: [x1,y1,x2,y2])

    -- 几何数据（GeoJSON）
    geometry TEXT,                   -- GeoJSON geometry

    -- 语义数据
    text_content TEXT,               -- 文字内容（如有）
    material_code TEXT,              -- 材料编号（如有）
    pattern_name TEXT,               -- 填充图案名称（如有）
    block_name TEXT,                 -- 图块名称（如有）

    -- 元数据
    confidence REAL DEFAULT 1.0,
    properties JSON,                 -- 扩展属性

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 构件关联表
CREATE TABLE component_relations (
    id INTEGER PRIMARY KEY,
    component_type TEXT,              -- 'material_region' / 'room' / 'wall' / 'opening'
    component_id INTEGER,
    related_drawing_id INTEGER REFERENCES drawings(id),
    related_page INTEGER,
    relation_type TEXT,               -- 'elevation' / 'detail' / 'material_spec'
    confidence REAL,
    evidence JSON,                    -- 关联证据

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 算量任务表
CREATE TABLE quantity_tasks (
    id INTEGER PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id),
    task_type TEXT,                   -- 'parse_cad' / 'parse_pdf' / 'spatial' / 'relate' / 'calculate' / 'audit'
    zone_id TEXT,                     -- 区域 ID（大项目）
    status TEXT DEFAULT 'pending',    -- pending/running/completed/failed/retrying
    worker_id TEXT,
    input_data JSON,
    output_data JSON,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 计算过程追溯表
CREATE TABLE quantity_calculations (
    id INTEGER PRIMARY KEY,
    measurement_id INTEGER REFERENCES measurements(id),

    -- 计算过程
    spatial_source_type TEXT,         -- 'cad_geometry' / 'dimension_read' / 'scale_derived' / 'ai_estimated'
    spatial_source_id TEXT,           -- 对应 drawing_elements.id
    formula_text TEXT NOT NULL,       -- 完整计算式
    formula_steps JSON,               -- 分步计算
    input_values JSON,                -- 输入参数值
    output_value REAL NOT NULL,

    -- 可追溯
    drawing_id INTEGER REFERENCES drawings(id),
    page_number INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 审计记录表
CREATE TABLE quantity_audits (
    id INTEGER PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id),
    audit_type TEXT,                  -- 'geometric' / 'cross_drawing' / 'historical' / 'final_ai'
    measurement_id INTEGER REFERENCES measurements(id),
    finding_type TEXT,                -- 'duplicate' / 'missing' / 'anomaly' / 'conflict' / 'low_confidence'
    severity TEXT,                    -- 'error' / 'warning' / 'info'
    description TEXT,
    evidence JSON,
    resolution TEXT,                  -- 'auto_fixed' / 'manual_review' / 'accepted' / 'rejected'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 人工复核日志
CREATE TABLE quantity_review_logs (
    id INTEGER PRIMARY KEY,
    measurement_id INTEGER REFERENCES measurements(id),
    user_id INTEGER REFERENCES users(id),
    action TEXT,                      -- 'confirm' / 'reject' / 'modify'
    old_value JSON,
    new_value JSON,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 四、DrawingUnifiedModel 设计

```python
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime

@dataclass
class DrawingSource:
    """来源信息 — 每个数据元素都标注来源，保证可追溯。"""
    source_file: str             # 文件名
    source_page: int = 1         # 页码
    source_layer: Optional[str] = None     # CAD 图层
    source_element_id: Optional[str] = None  # CAD handle / PDF 元素ID
    source_bbox: Optional[List[float]] = None  # [x1, y1, x2, y2]
    source_geometry: Optional[dict] = None     # 原始几何数据

@dataclass
class DrawingUnifiedModel:
    """多源图纸统一数据模型。

    承载一个项目所有图纸的解析结果，所有几何和语义数据统一存储。
    """

    # 元数据
    project_id: int
    created_at: datetime = field(default_factory=datetime.utcnow)

    # 图纸信息
    drawings: List['DrawingInfo'] = field(default_factory=list)

    # 几何元素（按类型组织）
    lines: List['LineElement'] = field(default_factory=list)
    polylines: List['PolylineElement'] = field(default_factory=list)
    arcs: List['ArcElement'] = field(default_factory=list)
    circles: List['CircleElement'] = field(default_factory=list)
    hatches: List['HatchElement'] = field(default_factory=list)
    blocks: List['BlockElement'] = field(default_factory=list)

    # 语义元素
    texts: List['TextElement'] = field(default_factory=list)
    dimensions: List['DimensionElement'] = field(default_factory=list)

    # 空间构件
    rooms: List['RoomElement'] = field(default_factory=list)
    walls: List['WallElement'] = field(default_factory=list)
    doors: List['OpeningElement'] = field(default_factory=list)
    windows: List['OpeningElement'] = field(default_factory=list)

    # 材料区域
    floor_regions: List['MaterialRegion'] = field(default_factory=list)
    wall_regions: List['MaterialRegion'] = field(default_factory=list)
    ceiling_regions: List['MaterialRegion'] = field(default_factory=list)

    # 柜体/构件
    cabinets: List['CabinetElement'] = field(default_factory=list)
    components: List['ComponentElement'] = field(default_factory=list)

    # 关联关系
    relations: List['ComponentRelation'] = field(default_factory=list)

    # 图纸间关联
    drawing_relations: List['DrawingRelation'] = field(default_factory=list)


@dataclass
class LineElement:
    id: str
    source: DrawingSource
    x1: float; y1: float
    x2: float; y2: float
    length: Optional[float] = None
    layer: Optional[str] = None

@dataclass
class PolylineElement:
    id: str
    source: DrawingSource
    points: List[tuple]  # [(x1,y1), (x2,y2), ...]
    is_closed: bool = False
    area: Optional[float] = None
    perimeter: Optional[float] = None
    layer: Optional[str] = None

@dataclass
class HatchElement:
    id: str
    source: DrawingSource
    boundary: List[tuple]   # Polygon 边界
    pattern_name: Optional[str] = None
    fill_color: Optional[str] = None
    layer: Optional[str] = None

@dataclass
class TextElement:
    id: str
    source: DrawingSource
    text: str
    x: float; y: float
    font_size: Optional[float] = None
    layer: Optional[str] = None

@dataclass
class DimensionElement:
    id: str
    source: DrawingSource
    text: str              # 标注文字
    measurement: float     # 标注数值
    x: float; y: float     # 标注位置
    layer: Optional[str] = None

@dataclass
class RoomElement:
    id: str
    name: str
    boundary: List[tuple]  # Polygon
    area: float
    perimeter: float
    source_drawings: List[DrawingSource] = field(default_factory=list)

@dataclass
class MaterialRegion:
    id: str
    material_code: str      # ST-01, CT-02...
    material_type: str      # 石材/瓷砖/木饰面...
    geometry: List[tuple]   # Polygon
    area: float
    room_id: Optional[str] = None
    semantic_source: Optional[str] = None  # 'pdf' / 'cad_hatch' / 'text_annotation'
    sources: List[DrawingSource] = field(default_factory=list)

@dataclass
class ComponentRelation:
    id: str
    component_id: str
    relation_type: str     # 'elevation' / 'detail' / 'material_ref'
    source_drawings: List[str]
    source_pages: List[int]
    related_detail_id: Optional[str] = None
```

---

## 五、开发阶段总览

| Phase | 名称 | 内容 | 可运行 | 预计工作量 |
|-------|------|------|--------|-----------|
| **Phase 0** | 代码库审计 + 架构设计 | ✅ 本轮完成 | ✅ | 1 天 |
| **Phase 1** | DWG/DXF/PDF 解析 | CAD/PDF 解析引擎 + 测试 | ✅ 解析API可用 | 2-3 周 |
| **Phase 2** | 统一图纸数据模型 | DrawingUnifiedModel + 数据库迁移 | ✅ 数据层可用 | 1-2 周 |
| **Phase 3** | 空间理解 | 房间/区域/材料/构件识别 | ✅ 可输出空间模型 | 2-3 周 |
| **Phase 4** | 几何计算引擎 | QuantityRuleEngine (8 分部) | ✅ BOQ 可生成 | 2 周 |
| **Phase 5** | 多 Agent/Worker 并行 | Orchestrator + 并发 + 任务队列 | ✅ 大项目可并行 | 2 周 |
| **Phase 6** | 图纸关联 | 平面/立面/天花/大样关联 | ✅ 关联可用 | 1-2 周 |
| **Phase 7** | 历史项目数据库 + 漏项 Agent | 标准化特征 + 模板 + 漏项检查 | ✅ 漏项提示可用 | 2 周 |
| **Phase 8** | 三层审计 | 几何审计/交叉审计/漏项审计 + AI 总审计 | ✅ 审计报告可用 | 1-2 周 |
| **Phase 9** | 人工复核工作台 | 交互式审核 UI + 修改日志 | ✅ 审核流程可用 | 2 周 |
| **Phase 10** | Excel 导出 + 生产部署 | 专业 Excel + 部署 + 监控 | ✅ 生产就绪 | 1-2 周 |

---

## 六、技术栈决策

### 6.1 后端

| 组件 | 当前 | 目标 | 原因 |
|------|------|------|------|
| Web 框架 | Flask 3.0 | Flask（保留） | 已有完整骨架 |
| 数据库 | SQLite | SQLite(dev) / PostgreSQL(prod) | 已有支持 |
| ORM | SQLAlchemy | SQLAlchemy + Alembic | 增加 Migration |
| 任务队列 | 无 | Celery + Redis | 异步处理 |
| CAD 解析 | 无 | ezdxf + ODA File Converter | 见 CAD_PARSING.md |
| PDF 解析 | 无 | PyMuPDF + Tesseract | 见 CAD_PARSING.md |
| 几何计算 | OpenCV(像素) | Shapely(矢量) | 精度提升 |
| 图像处理 | OpenCV | OpenCV (保留用于标注) | 可视化 |
| AI 调用 | 无 | Claude API | 语义理解 |
| 日志 | 无 | structlog | 结构化日志 |

### 6.2 前端

| 组件 | 当前 | 目标 | 原因 |
|------|------|------|------|
| 模板引擎 | Jinja2 | Jinja2 (保留) | 页面路由 |
| CSS | 原生 CSS | 保留 + 扩展 | 已有完整设计系统 |
| JS 框架 | 无 | HTMX + Alpine.js | 轻量交互增强 |
| 图纸查看 | 无 | 自研 Canvas/标注层 | 需在原图上高亮 |
| 审核工作台 | 无 | 新页面 + WebSocket | 实时交互 |

### 6.3 基础设施

| 组件 | 当前 | 目标 |
|------|------|------|
| 容器化 | Docker | Docker + docker-compose (含 CAD 容器) |
| 数据库 | SQLite 文件 | SQLite(dev) / PostgreSQL(prod via env var) |
| 存储 | 本地文件系统 | 本地 + 可选 S3 |
| 监控 | 无 | Docker 日志 + Sentry |
| CI/CD | 无 | GitHub Actions (测试 + 部署) |
| CAD 工具 | 无 | Docker cad-parser 容器 |

---

## 七、可追溯性设计

**每条工程量必须能回答："为什么是这个量？从哪里来的？"**

```python
# 追溯链示例：客厅 ST-01 石材地面 = 23.68m²

measurement = {
    "id": 142,
    "label": "客厅-ST-01石材地面",
    "quantity": 23.68,
    "unit": "m²",

    # 追溯链
    "trace": {
        "project": {"id": 1, "name": "XX售楼部"},
        "spatial": {"room": "客厅", "region_id": "R-03"},
        "geometry_source": {
            "type": "CAD_DXF",
            "file": "A-01_floor_plan.dxf",
            "element_id": "2F3A",
            "element_type": "LWPOLYLINE",
            "layer": "FLOOR-BOUNDARY",
        },
        "semantic_source": {
            "type": "PDF_TEXT",
            "file": "A-01_floor_plan.pdf",
            "page": 3,
            "text_block": "ST-01 石材",
        },
        "material_code": "ST-01",
        "material_source": {
            "type": "MATERIAL_SCHEDULE",
            "file": "material_schedule.pdf",
            "page": 1,
            "row": 5,
        },
        "formula": {
            "expression": "Polygon(P023).area",
            "input_geometry": [[0,0], [5200,0], [5200,4550], [0,4550]],
            "intermediate_steps": [
                {"step": 1, "description": "CAD坐标转换为实际尺寸", "formula": "5200mm × 4550mm"},
                {"step": 2, "description": "计算Polygon面积", "formula": "5.20 × 4.55 = 23.66m²"},
                {"step": 3, "description": "与标注尺寸核对", "result": "标注23.68m²，偏差0.08%，通过"},
            ],
            "final_value": 23.68,
        },
        "confidence": {
            "score": 96,
            "level": "high",
            "reasons": [
                "DXF几何精度高 (+30)",
                "标注尺寸与几何一致 (+20)",
                "PDF材料编号明确 (+15)",
                "无相关大样冲突 (+15)",
                "平面/材料表一致 (+16)",
            ],
        },
        "audit": {
            "geometric_audit": "通过（偏差<1%）",
            "cross_drawing_audit": "通过（材料表与平面一致）",
            "historical_audit": "通过（同类型项目均有此项）",
        },
        "related_drawings": [
            {"file": "S-03_elevation.pdf", "page": 2, "relation": "立面确认石材区域范围"},
        ],
    },
}
```

---

## 八、系统模块地图

```
src/
├── core/                           # 核心引擎（确定性计算，不依赖 Flask）
│   ├── cad_parser/                 # Phase 1: CAD 解析
│   │   ├── dwg_reader.py           #   DWG→DXF 转换
│   │   ├── dxf_extractor.py        #   DXF 实体提取
│   │   ├── pdf_extractor.py        #   PDF 文字/矢量提取
│   │   └── ocr_engine.py           #   OCR 增强
│   ├── unified_model/              # Phase 2: 统一数据模型
│   │   ├── model.py                #   DrawingUnifiedModel 定义
│   │   ├── merger.py               #   CAD+PDF 融合引擎
│   │   └── source_tracker.py       #   来源追踪器
│   ├── spatial/                    # Phase 3: 空间理解
│   │   ├── room_detector.py        #   房间识别
│   │   ├── wall_detector.py        #   墙体识别
│   │   ├── opening_detector.py     #   门窗识别
│   │   ├── material_region.py      #   材料区域识别
│   │   └── drawing_classifier.py   #   图纸类型识别
│   ├── quantity_engine/            # Phase 4: 算量引擎
│   │   ├── rule_engine.py          #   QuantityRuleEngine 核心
│   │   ├── rules/                  #   8 个分部计算规则
│   │   │   ├── flooring.py         #    第 1 分部
│   │   │   ├── wall.py             #    第 2 分部
│   │   │   ├── ceiling.py          #    第 3 分部
│   │   │   ├── openings.py         #    第 4 分部
│   │   │   ├── baseboard.py        #    第 5 分部
│   │   │   ├── cabinets.py         #    第 6 分部
│   │   │   ├── plumbing.py         #    第 7 分部
│   │   │   └── electrical.py       #    第 8 分部
│   │   └── calculator.py           #   几何计算器（Shapely）
│   ├── audit/                      # Phase 8: 审计
│   │   ├── geometric_audit.py      #   几何审计
│   │   ├── cross_drawing_audit.py  #   图纸交叉审计
│   │   ├── historical_audit.py     #   历史漏项审计
│   │   └── ai_auditor.py           #   AI 总审计
│   ├── confidence/                 # 置信度引擎
│   │   └── scorer.py               #   程序化评分
│   └── historical/                 # Phase 7: 历史知识库
│       ├── feature_extractor.py    #   特征标准化
│       ├── template_manager.py     #   模板管理
│       ├── completeness_agent.py   #   漏项检查 Agent
│       └── excel_parser.py         #   Excel 导入（升级现有）
│
├── orchestration/                  # Phase 5: Agent/Worker 编排
│   ├── orchestrator.py             #   Orchestrator (Agent 0)
│   ├── cad_agent.py                #   CAD 解析 Agent
│   ├── pdf_agent.py                #   PDF 解析 Agent
│   ├── spatial_agent.py            #   空间理解 Agent
│   ├── relation_agent.py           #   图纸关联 Agent
│   ├── worker.py                   #   Worker 基类
│   ├── zone_worker.py              #   区域 Worker
│   ├── concurrency.py              #   并发管理
│   └── task_tracker.py             #   任务状态追踪
│
├── web/                            # Web 应用（Flask）
│   ├── app.py                      #   应用工厂（替换 __init__.py）
│   ├── auth.py                     #   认证（保留现有）
│   ├── routes/
│   │   ├── main.py                 #   主页面路由
│   │   ├── project.py              #   项目 CRUD
│   │   ├── upload.py               #   上传处理
│   │   ├── boq.py                  #   BOQ 查看/导出
│   │   ├── audit.py                #   审计/审核路由
│   │   └── api.py                  #   REST API（扩展）
│   ├── templates/                  #   Jinja2 模板（保留现有）
│   └── static/                     #   静态文件
│       ├── css/
│       ├── js/
│       └── uploads/
│
├── tests/                          # 测试
│   ├── unit/
│   │   ├── test_cad_parser.py
│   │   ├── test_pdf_extractor.py
│   │   ├── test_rule_engine.py
│   │   └── test_confidence.py
│   ├── integration/
│   │   ├── test_full_pipeline.py
│   │   └── test_historical.py
│   ├── fixtures/                   # 测试数据
│   │   ├── simple_floor_plan.dxf
│   │   ├── annotated_drawing.pdf
│   │   └── expected_boq.json
│   └── quantity/                   # 真实项目测试
│       └── README.md
│
├── migrations/                     # Alembic 数据库迁移
├── docker/                         # Docker 配置
│   ├── cad-parser/                 #   CAD 解析容器
│   │   └── Dockerfile
│   └── docker-compose.yml          #   扩展编排
│
├── docs/                           # 设计文档
│   ├── QUANTITY_TAKEOFF_SYSTEM_AUDIT.md
│   ├── CAD_PARSING.md
│   ├── HISTORICAL_QUANTITY_KB.md
│   ├── MULTI_AGENT_QUANTITY_ARCHITECTURE.md
│   └── QUANTITY_TAKEOFF_MASTER_ARCHITECTURE.md  ← 本文档
│
├── config.py                       # 配置（保留并扩展）
├── requirements.txt                # 依赖（扩展）
└── README.md                       # 项目说明（更新）
```

---

## 九、关键性能指标

| 指标 | 目标 | 度量方式 |
|------|------|---------|
| CAD 解析成功率 | >95% | 成功解析图纸数 / 总上传图纸数 |
| 房间识别准确率 | >90% | 正确识别房间数 / 总房间数 |
| 材料识别准确率 | >85% | 正确识别材料区域数 / 总材料区域数 |
| 工程量误差率 | <5% | ｜AI工程量 - 人工标准｜/ 人工标准 |
| 漏项率 | <10% | 遗漏项数 / 标准清单项数 |
| 重复计算率 | <2% | 重复项数 / 总清单项数 |
| 低置信度率 | <20% | 低置信度项数 / 总清单项数 |
| 人工修正率 | <15% | 人工修改项数 / 总清单项数 |
| 小项目处理时间 | <30s | 端到端处理时间 |
| 中型项目处理时间 | <5min | 端到端处理时间 |
| 大项目处理时间 | <30min | 端到端处理时间（取决于规模） |

---

## 十、下一步行动

Phase 0 完成后，进入 Phase 1：

1. **安装 CAD 解析依赖**
   ```bash
   brew install libredwg tesseract tesseract-lang
   pip install ezdxf PyMuPDF pdfplumber shapely
   ```

2. **实现 CADParser（`core/cad_parser/`）**
   - `dwg_reader.py` — libredwg DWG→DXF
   - `dxf_extractor.py` — ezdxf 结构化实体提取
   - 单元测试（使用真实 CAD 样本）

3. **实现 PDFExtractor（`core/cad_parser/`）**
   - `pdf_extractor.py` — PyMuPDF 文字/矢量提取
   - 材料编号正则提取
   - 房间名称识别

4. **保持现有系统可运行**
   - 新模块独立于现有 Flask 应用
   - 通过 `core/` 包提供 API
   - 现有 `processing.py` 保留作为 fallback

5. **git commit**
   ```
   feat: Phase 0 架构设计文档 + 技术栈审计
   docs: 系统审计报告 / CAD解析方案 / 历史知识库 / Agent架构 / 总体架构
   ```
