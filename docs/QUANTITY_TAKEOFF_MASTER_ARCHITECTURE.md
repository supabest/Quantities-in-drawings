# 装修工程图纸智能识别与工程量计算系统 — 总体架构设计

> **文档版本**: v1.7（§12.7.17 房间↔材料归属突破: 材料表(物料說明PDF)+平面布置图p11房间标签→纯图纸16间房清单188.2㎡; 客厅/茶室65.6≈c法66几何自验; BOQ客厅93=客厅+餐厅+玄关+过厅口径合并坐实）
> **日期**: 2026-09-27
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

> **v1.1 注**: 本节的 2D 元素模型将按 **§十一（2.5D 语义实体模型）** 扩展——
> 墙段/开洞/材料面/房间体 + 拓扑图。Phase 5 数据建模时按 §十一 的实体定义执行（硬约束）。

---

## 五、开发阶段总览

| Phase | 名称 | 内容 | 可运行 | 预计工作量 |
|-------|------|------|--------|-----------|
| **Phase 0** | 代码库审计 + 架构设计 | ✅ 本轮完成 | ✅ | 1 天 |
| **Phase 1** | DWG/DXF/PDF 解析 | CAD/PDF 解析引擎 + 测试 | ✅ 解析API可用 | 2-3 周 |
| **Phase 2** | 统一图纸数据模型 | DrawingUnifiedModel + 数据库迁移（按 §十一 2.5D 实体模型 3D-ready 设计） | ✅ 数据层可用 | 1-2 周 |
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

---

## 十一、2.5D 语义实体模型（三维辅助算量与检验）

> **v1.1 追加** | 定位：不是三维重建，而是**轮廓挤出 + 拓扑关系**的轻量语义实体模型。
> 它是 §四 DrawingUnifiedModel 的 3D-ready 扩展，作为 Phase 5 数据建模的硬约束。

### 11.1 为什么是 2.5D 而不是三维重建

装修算量所需的实体 = 房间多边形 + 墙段×层高 + 墙面开洞 + 材料区域归属 + 天花跌级。
本质是平面轮廓沿 Z 轴挤出并补上拓扑关系，不需要网格、点云或 IFC 全家桶。

技术栈：Shapely（2D 几何）+ 挤出逻辑 + Three.js（前端复核渲染）。
行业对照：广联达/鲁班"BIM 翻模"即此思路；本模型是其轻量化自研版。

### 11.2 实体定义（在 §四 模型基础上增加）

```python
@dataclass
class WallSegment:
    id: str
    base_line: List[tuple]        # 2D 底边（边线，标定后确定）
    height: float                 # 层高/段高
    thickness: Optional[float]    # 墙厚（无 CAD 时可缺失）

@dataclass
class Opening:
    id: str
    wall_segment_id: str          # 拓扑约束：门窗必须挂在墙段上
    kind: str                     # door / window / opening
    width: float
    height: float
    sill: float                   # 离地高
    position_on_wall: float       # 沿墙段位置参数

@dataclass
class MaterialFace:
    id: str                       # 一面墙的一个饰面
    wall_segment_id: str
    side: int                     # 0/1 —— 面归属唯一房间
    room_id: str                  # 使用侧归属（公共墙规则在此自然成立）
    material_code: Optional[str]
    opening_ids: List[str]        # 面积 = 面面积 − Σ开洞

@dataclass
class RoomVolume:
    id: str
    floor_polygon: List[tuple]
    floor_height: float
    ceiling_levels: List[dict]    # 跌级 [{level_z, polygon}]

@dataclass
class TopologyGraph:
    """邻接关系 —— 拓扑校验与三维复核的依据。
    落库复用 §3.1 的 component_relations / geometries 表。"""
    room_walls: Dict[str, List[str]]      # room -> wall segments
    wall_openings: Dict[str, List[str]]   # wall -> openings
    face_rooms: Dict[str, str]            # material face -> owning room
```

### 11.3 拓扑校验规则（喂给审计层，违反者降置信/待人工，不静默修正）

1. **门窗挂墙**：`Opening.wall_segment_id` 必须存在——替代 bbox 邻近法，杀掉门窗误报
2. **面唯一归属**：每个 `MaterialFace` 恰属一个房间——公共墙"使用侧归属"在此自然成立
3. **体积守恒**：Σ(地面面积×层高) 与围合体一致；天棚面积 = 地面水平投影
4. **开洞守恒**：墙面面积 = 面面积 − Σ开洞；开洞不重叠、不出界
5. **闭合性**：房间多边形闭合、墙段成环

### 11.4 复杂造型降级

弧形墙、圆柱、跌级、曲面软膜等直壁挤出处理不了的区域，自动降级🔴待人工，
并输出审计信号（"此区域无法建模，请人工"）——与置信度哲学一致。

### 11.5 三处嵌入路线

| 落点 | 内容 |
|------|------|
| Phase 5 数据期 | 统一模型按本节实体+拓扑设计（3D-ready）；第一步低垂果实：解析层先做"门窗挂墙"2D 拓扑校验（无需真三维） |
| Phase 6 引擎期 | 挤出 + 拓扑校验进引擎；QuantityRuleEngine 输入从 bbox 升级为实体 |
| Phase 9 复核期 | Three.js 三维复核：清单项 ↔ 几何实体高亮联动（溯源从计算式升级到几何） |

### 11.6 边界

- 三维模型**放大**上游识别质量，不修复上游——它不能替代解析精度投入
- 语义问题（灯槽/窗帘盒口径、甲供、活动家具）仍靠规则与用户交互，不靠几何

---

## 十二、修订版 Phase 规划与 Phase 4.5 前置补丁

> **v1.2 追加** | 定位：把 §五 的十阶段与工程实施中已完成的 Phase 1-4（对应本文件外
> 的 方案 A 编号）对齐，并新增 **Phase 4.5 · 2D 拓扑预校验**，作为 §十一 三维辅助算量路线的
> "零成本三维"起手式。

### 12.1 修订版路线图（v1.2 生效）

| 阶段 | 内容 | 相对 §五 的调整 | 状态 |
|------|------|----------------|------|
| **Phase 4** | 确定性异常规则 + LLM 综合审计（语义冲突） | 原 §五 Phase 8 中的 AI 总审计拆前，规则先行 | ✅ 完成 |
| **Phase 4.5** | **2D 拓扑预校验**（门窗挂墙 / 闭合性 / 面积守恒） | **本轮新增**：§11.5 "低垂果实" 提前实现 | 🎯 计划中 |
| **Phase 5a** | PostgreSQL 迁移 + Alembic + 双跑 | 原 §五 Phase 2 DB 部分前移，解除 SQLite 写锁 | ⏳ |
| **Phase 5b** | DXF 解析（ezdxf + libredwg）+ `electrical.dxf` 对账验收 | 原 §五 Phase 1 的 CAD 部分 | ⏳ |
| **Phase 5c** | PDF 解析（PyMuPDF + 可选 OCR）+ §9.1 权威域分工 | 原 §五 Phase 1 的 PDF 部分，从隐含拆出显式 | ⏳ |
| **Phase 5d** | 融合去重 + §9.3 冲突仲裁 + §十一 2.5D 实体落库 | 原 §五 Phase 2 + Phase 3 的融合，按 §十一 硬约束 | ⏳ |
| **Phase 6** | QuantityRuleEngine（Shapely）+ 挤出/拓扑进引擎 | 原 §五 Phase 4 + Phase 6 前段合并 | ⏳ |
| **Phase 7-8** | 图纸关联 Agent → 历史知识库升级 | 同 §五 Phase 6 + Phase 7 | ⏳ |
| **Phase 9** | 复核工作台 + **前端接入 `/api/orchestrator/*`** + 需求确认向导 + Three.js 三维复核 | 原 §五 Phase 9 显式补齐"前端接入编排器"缺口 | ⏳ |
| **Phase 10** | 套价联调打通 + Excel 导出 + 生产部署 | 套价从"未提"变主线上线项 | ⏳ |

### 12.2 Phase 4.5 · 2D 拓扑预校验（详解）

**动机**：§11.3 五条拓扑规则中，有三条可以**纯 2D 完成**——不依赖真三维、不依赖 DXF/PDF 解析。作为 Phase 5 前置补丁，可提前抓 bug 并为 §十一 数据建模接口做回归验证。

**范围（对应 §11.3 规则 1 / 3 / 5）**：

| # | 检查项 | 判据 | 依据 |
|---|--------|------|------|
| 1 | **门窗挂墙 2D 版** | 分部 4 门窗中心到最近房间边界（bbox 边）的距离 ≤ 容差（默认 0.15m） | §11.3(1) |
| 2 | **闭合性** | 分部 1 房间 `data_json.bbox` 反算多边形 `is_valid & is_closed`；退化面积 (<1m²) 判为可疑 | §11.3(5) |
| 3 | **面积守恒** | 同房间地面面积 ≈ 天棚面积（分部 1 vs 分部 3）；偏差 >5% 触发 | §11.3(3) |

**非范围（推到 Phase 5d / Phase 6）**：
- ❌ 挤出渲染、墙面开洞不重叠、MaterialFace 归属、复杂造型降级
- ❌ 真实 `WallSegment` 拓扑（需 DXF 图层信息）

**落点**：`app/orchestrator/audit.py` 新增 `topology_2d_audit(project) -> dict`；
`run_audits()` 追加为第五路（Phase 4 的四路 → Phase 4.5 的五路）。

**输出契约**（与 `reverse_reconciliation()` 一致，只标记不删除）：

```python
{
  'door_not_on_wall': [
    {'m_id': 142, 'label': '客厅-门', 'dist_m': 0.42, 'tol_m': 0.15}
  ],
  'invalid_room_polygons': [
    {'m_id': 87, 'label': '主卧', 'reason': 'not_closed | degenerate | self_intersect'}
  ],
  'area_conservation_violation': [
    {'room': '客厅', 'floor_m2': 23.68, 'ceiling_m2': 24.15, 'delta_pct': 1.98}
  ],
  'hit_count': 7
}
```

**降级规则**：命中项 `confidence_level → low`、`confidence_icon → 🔴`，`remarks` 追加
`【2D拓扑嫌疑·Phase4.5】<原因>`；沿用 Phase 3 反向对账的"留痕不删除、排除合计、待人工"处理。

**验收标准**：
1. 现有 E2E（`tests/test_orchestrator_e2e.py`）保持全绿；
2. 新增 3 个 fixture：门窗悬空 / 房间不闭合 / 地面天棚面积偏差 >5%；
3. 对历史项目回归：Phase 4.5 命中项应 ⊆ 反向对账已标记的幽灵项 ∪ 横向对账已告警项（拓扑检查是子集，不引入新误报源）。

**工作量**：3-5 天（含 fixture 与回归）。

### 12.3 Phase 4.5 的价值链

```
Phase 4 完成 → 现有五路审计（反向/横向/纵向/异常规则/LLM 综合）
                     ↓
Phase 4.5 插入 → 第六路：2D 拓扑校验
                     ├─ 输出喂给 Phase 4 LLM 综合审计（作为新事实来源）
                     ├─ 提前踩坑 §十一 实体接口，Phase 5 建模少返工
                     └─ 前端在 Phase 9 前即可展示嫌疑项（预留字段）
                     ↓
Phase 5 · 数据建模（真三维所需的矢量数据到位）
                     ↓
Phase 6 · 升级到真 2.5D 拓扑（§11.3 完整五条）
```

### 12.4 不做什么（避免范围蔓延）

- 不在 Phase 4.5 引入 `WallSegment / Opening / MaterialFace / RoomVolume` 等 §十一 实体类；仍走 `Measurement.data_json` 的临时结构
- 不引入 Three.js / WebGL 渲染（推到 Phase 9）
- 不做真挤出（无矢量数据前，挤出只会放大误差）

### 12.5 Phase 5 拆分详解（避免"统一模型"一词吃掉真实工作量）

> **背景**：§五 里 "统一模型" 一句将 DB、CAD 解析、PDF 解析、多源融合、§9 仲裁、§十一 实体建模
> 6 件事打包估算 **1-2 周**——实际严重低估。现拆为 **5a/5b/5c/5d** 四个子阶段，每个有独立
> 交付物与验收标准。总计 **4-5 周**。

#### **12.5.1 Phase 5a · PostgreSQL 迁移**（预计 1 周）

| 项 | 内容 |
|----|------|
| 目标 | 从 SQLite 无锁机迁移到 PG；为后续并行写入/回滚提供事务基座 |
| 交付 | 1) `config.py` 支持 `DATABASE_URL` 切换；<br>2) `migrations/` 目录 + Alembic 初始化；<br>3) SQLite→PG 数据导入脚本；<br>4) `orchestrator/concurrency.py` 里 **删除 `DB_WRITE_LOCK`**（SQLite 遗留锁） |
| 验收 | 现有 E2E 在 PG 上全绿；同项目多 Worker 并发写库无锁冲突 |
| 风险 | SQLite 旧数据中的 JSON 列 (`data_json`/`summary_json`) 需转为 `JSONB`；开发/生产双跑窗口需控制 |
| 依赖 | 无（可即刻开工） |

#### **12.5.2 Phase 5b · DXF 解析**（预计 2 周）

| 项 | 内容 |
|----|------|
| 目标 | 把 DWG/DXF 变成 `drawing_elements` 表的结构化几何 |
| 交付 | 1) `requirements.txt` 新增 `ezdxf / shapely`；<br>2) DWG→DXF 转换链路（ODA File Converter Docker 或 libredwg）；<br>3) `app/core/cad_parser/dxf_extractor.py`：九类实体提取（LINE/LWPOLYLINE/ARC/CIRCLE/HATCH/INSERT/DIMENSION/TEXT-MTEXT/LAYER）；<br>4) `drawing_pages` / `drawing_elements` 新表（§3.1）；<br>5) `electrical.dxf` 对照 `outputs/营销中心…表.xlsx` 验收报告 |
| 验收 | 九类实体均可提取；`electrical.dxf` 中灯具/开关/插座图块计数与手算表偏差 <2%（对应 KPI 中"CAD 解析成功率 >95%"） |
| 风险 | DWG 版本兼容性（AutoCAD 2018+）；中文图层名乱码；DIMENSION 实体实测值 override |
| 依赖 | Phase 5a（写入 PG） |

#### **12.5.3 Phase 5c · PDF 解析**（预计 1-1.5 周，**从原"统一模型"一词里显式剖出**）

| 项 | 内容 |
|----|------|
| 目标 | 把矢量 PDF 变成 `drawing_elements` 表的**语义数据**；与 DXF 同表不同行 |
| 交付 | 1) `requirements.txt` 新增 `PyMuPDF / pdfplumber / pytesseract`；<br>2) `app/core/cad_parser/pdf_extractor.py`：文字块 + bbox + 材料号正则 `[A-Z]{2}-\d{2}` + 图例表 + 尺寸标注；<br>3) **§9.1 权威域分工硬落地**：PDF 元素写入时 `source_type='pdf_text'`，`source_authority='semantic_only'`（**禁止参与几何计算**）；<br>4) 扫描版 PDF 分支：pytesseract OCR 后降级 `source_authority='fallback_ocr'`，自动射 🟡 中置信度 |
| 验收 | 材料号 `ST-01`、房间名、图例表行均能提取；对同一份 DXF+PDF，材料号与 DXF TEXT 重合度 ≥ 80% |
| 风险 | 无现成 PDF 验收样本——**需从 `营销中心…dwg` 导出 PDF 作 5c 验收物料**（ODA/QCAD） |
| 依赖 | Phase 5a、5b（同表写入） |

#### **12.5.4 Phase 5d · 融合去重 + §9.3 冲突仲裁 + §十一 实体落库**（预计 1.5 周，**本轮重点拆分**）

> **一句话**：这一项不是"顺手归档"，而是把 §9.2/9.3 的量化阈值变成可运行的规则引擎。

**子项 A — 融合去重（§9.2）**

| 规则 | 实现 | 工作量 |
|------|------|--------|
| 身份键优先级 | CAD handle 优先；无 handle 时 bbox **IoU > 0.8** 视为同一实体 | Shapely `.intersection()/.union()` 封装 + 单测 0.5 天 |
| 多来源归并 | 归并后 `DrawingSource` 列表写入 `drawing_elements.source_meta` (JSONB)，保留全部 `source_file / source_page / source_bbox` | 0.5 天 |
| 只出一条清单项 | 融合层去重 → `Measurement` 不重复插入；审计层"同一区域同一材料重复" KPI 验证 <2% | 0.5 天（与 Phase 4 异常规则中重复检测合并） |

**子项 B — §9.3 冲突仲裁引擎**

| 冲突类型 | 仲裁规则 | 实现要点 |
|---------|---------|---------|
| DXF 实测 vs 标注 | **偏差 >5%** → 以标注为准 + 审计提示 | `abs(polygon.area - dim_measured)/dim_measured > 0.05` 阈值常量；命中则 `confidence='medium'`，备注仲裁依据 |
| 平面 vs 立面宽度 | 以平面为准 | 依赖 Phase 7 图纸关联（本阶段先留接口，不实现） |
| 几何 vs HATCH | **HATCH 只作分界，不算面积** | 提取 `HATCH.pattern_name` 作材料归属，面积一律回退到边界 polygon.area |

工作量：仲裁引擎 **3 天**（含回归）。

**子项 C — §十一 2.5D 实体落库**

| 实体 | 表 | 字段要点 |
|------|-----|---------|
| WallSegment | `walls` 🆕 | `base_line`(GeoJSON LineString) + `height` + `thickness?` |
| Opening | `doors` / `windows` 🆕 | `wall_segment_id` 外键（**拓扑硬约束**）+ `position_on_wall` |
| MaterialFace | `wall_regions` 🆕 | `side` (0/1) + `room_id` 使用侧归属 + `opening_ids` |
| RoomVolume | `rooms` 🆕 | `floor_polygon` + `floor_height` + `ceiling_levels` (JSONB) |
| TopologyGraph | 复用 `component_relations` | 邻接表（room→wall→opening→face→room） |

工作量：**4-5 天**（建表 + Alembic + 写入链路 + 拓扑外键约束）。

**验收标准**（Phase 5d 整体）：
1. `electrical.dxf` 与手算表工程量误差 ≤ 5%（KPI）；
2. 同实体多源归并后 **只出一条 Measurement**（SQL 断言）；
3. 五类冲突仲裁 fixture 全部命中预期降级行为；
4. 无 handle 时 IoU 去重回归：向已知实体集注射人工衍变的“同一墙体三个微变体”，应只保留一条。

**依赖关系**：
```
5a (DB) → 5b (DXF) → 5c (PDF) → 5d (融合 + 仲裁 + 实体)
                         ↑                    ↑
                    PDF验收样←— 本阶段需先导出     │
                                              │
                                     Phase 4.5 拓扑预校验 ←— 接口回归验证
```

#### **12.5.5 为何必须拆开的三个硬理由**

1. **工作量可视化**——5c/5d 各含 1.5 周以上实现量，打包在"统一模型"一词里会被当作“顺手做的事”，导致 Phase 5 实际交付延期；
2. **验收物料不同**——5b 用 DXF，5c 必须先准备 PDF，5d 需要仲裁 fixture；一个阶段一个 gate，不会“交不出东西”；
3. **依赖链清晰**——5d 依赖 5b+5c 均有真实数据才能回归验证；串行交付，不会“写好的仲裁引擎无数据可测”的尴尬。

#### **12.5.6 Phase 5 总计工作量修正**

| 子阶段 | 工期 |
|--------|------|
| 5a | 1 周 |
| 5b | 2 周 |
| 5c | 1-1.5 周 |
| 5d | 1.5 周 |
| **Phase 5 总计** | **5.5-6 周**（§五 原估算 3-4 周，**偏乐观**） |

### 12.6 三维模型输出格式与兼容性决策（v1.2 追加）

> **背景**：为了"平立面算量 + 通用三维初步模型 + 自然语言细化"的闭环能真正落到甲方 / 造价 / BIM 咨询的工作流里，本项目对**外部 3D 交换格式**做出如下决策并绑定到既有 Phase。

#### **12.6.1 主/辅/兜底三档**

| 档位 | 格式 | 版本 | 用途 | 谁消费 |
|------|------|------|------|--------|
| 🥇 **主** | **IFC** | **IFC4 Reference View**（`IFC4X3_RC4` 待稳定后升级） | 语义化三维模型完整导出，含墙/洞/面/空间/材料全部对象 | Revit / ArchiCAD / 广联达 BIMmake / 探索者 / Tekla / BIMsight |
| 🥈 **可视化** | **glTF / GLB** | 2.0 | 复核工作台 Three.js 直接展示，不承载语义 | 浏览器、移动端、微信内置浏览器 |
| 🥉 **兜底** | **DWG 3D** | AutoCAD 2018+ | 只保留几何，服务少数纯 CAD 用户 | AutoCAD / 浩辰 / 天正 |

#### **12.6.2 §十一 五类实体 → IFC 映射（Phase 5d schema 定稿时锁定）**

| 本项目实体 | IFC 元素 | 关系 | 备注 |
|-----------|---------|------|------|
| `WallSegment` | `IfcWallStandardCase` | `IfcRelVoidsElement` → Opening | 含 `HasOpenings` |
| `Opening` | `IfcOpeningElement` | 反向引用回 `IfcWall` | 挂墙通过 `wall_segment_id` 外键 |  |
| `MaterialFace` (墙/顶) | `IfcCovering` | `IfcRelCoversBldgElements` | Cavity/ ceiling / finishing 分类 |
| `MaterialFace` (地面) | `IfcSlab.isType = FLOOR` | `IfcRelContainedInSpatialStructure` | |
| `RoomVolume` | `IfcSpace` | `IfcRelAggregates` → `IfcBuildingStorey` | 层高/标高来自 Storey |
| `TopologyGraph` | `IfcProject` + 上述 Rel 关系网 | | 邻接关系映射为 IFC 关系实体 |

**接口约束**：Phase 5d 的 §11.2 / §12.5.4 子项 C 建表时，每张实体表**必须预留** `ifc_guid CHAR(22)`（IFC GlobalId，base64 编码 128 位）+ `ifc_entity_type VARCHAR(64)` 两列——即使本阶段不写入，也要**建表时就存在**，避免二次迁移。

#### **12.6.3 自然语言微调通道（Phase 9 落地）**

**核心原则**：LLM 不直接生成 IFC 代码，只输出**受限 DSL**；DSL 由项目内规则引擎应用到 2.5D 实体上，然后重新走 §12.6.2 映射导出 IFC。

```
用户输入："客厅层高改成 2.9m"
       ↓
GLM-4.6 生成 ops（含 schema 校验的 JSON）
       ↓
规则引擎：op 白名单校验 → 拓扑合法性校验 → 应用到 §十一 实体
       ↓
QuantityRuleEngine 重算 → BOQ diff（Δ 面积/Δ 体积/新增/删除项）
       ↓
用户在复核工作台看到 diff → 确认 / 回滚（回滚走 §Phase 5a 事务基座）
```

**支持的 op 集合**（白名单，Phase 9 首批）：`set_room_height` / `rename_room` / `split_room` / `merge_rooms` / `add_opening_on_wall` / `delete_opening` / `set_material_face_finish` / `add_fixed_cabinet` / `adjust_wall_thickness`。任何超出白名单的 op 直接拒绝并提示用户改写描述。

#### **12.6.4 效果图作为语义补充源（Phase 9.5 可选）**

- **权威域划分**（扩展 §9.1）：几何 → DXF；材料/做法/装饰层数 → 效果图 + PDF 说明
- 多模态 LLM（GLM-4V）从效果图抽取：吊顶层次 / 墙面造型层数 / 主材类别；结果写入 `MaterialFace.material_hint`（新字段）
- 只做**建议 + 交叉验证**，不覆盖 DXF 几何；冲突时按 §9.3 仲裁规则降级

#### **12.6.5 明确不做什么**

- ❌ **不写 RVT**（Revit 私有；写 RVT 只能靠 Dynamo / Revit API 的 Windows-only 服务，破坏部署）
- ❌ **不用 FBX / OBJ / glTF 作为算量源**（丢语义；glTF 只做可视化输出）
- ❌ **不用 STEP / IGES**（MCAD 系，无建筑拓扑；固定柜等设备构件如确需精细几何，走独立附件路径）
- ❌ **不做真三维重建**（沿用 §11.1 决策：2.5D 挤出 + 拓扑，不上 NeRF/Photogrammetry）

#### **12.6.6 对现有 Phase 的增量影响**

| Phase | 增量 | 附加工作量 |
|-------|------|-----------|
| **5d** | §11.2 建表预留 `ifc_guid` / `ifc_entity_type` 两列；实体写入时分配 UUID（暂不导出） | +0.5 天 |
| **6** | 新增 `app/orchestrator/ifc_exporter.py`（IfcOpenShell） + `gltf_exporter.py`（web-ifc / trimesh） | +1.5 周 |
| **9** | 复核工作台新增 **NL 微调对话框 + DSL 预览 + Diff/回滚 UI** | +2 周（原 Phase 9 内的子项，工作量已含在 Phase 9 估算） |
| **9.5（新）** | 效果图多模态通道（可选，甲方需求驱动再启动） | +1 周 |

**总增量约 3 周**（其中 2 周在 Phase 9 内本就要做，实际净增 1 周），与主干并行推进，不改变 Phase 5 时间表。

#### **12.6.7 依赖新增**

`requirements.txt` 在 Phase 6 追加：
```
ifcopenshell   # IFC 读写，PyPI 直接安装（自带 OCC 二进制）
web-ifc        # Three.js 侧的 IFC 加载器（前端 npm）
trimesh        # glTF 导出
```

Phase 5a 的 PostgreSQL 迁移不受本决策影响，可**即刻开工**。

### 12.7 Phase 5b · DXF 解析设计决策（v1.2 追加，基于 `electrical.dxf` 实测普查）

> 本节结论全部来自对 `electrical.dxf`（23MB，AC1032/AutoCAD 2018，代码页 ANSI_936 GBK）
> 与手算表 `outputs/营销中心…表.xlsx` 的**实测普查**，非纸面推断。

#### **12.7.1 图纸画像（实测）**

| 维度 | 实测结果 |
|------|----------|
| modelspace 实体 | LWPOLYLINE 7943 / **INSERT 4522** / LINE 1921 / TEXT 1391 / ARC 240 / SOLID 199 / HATCH 165 / CIRCLE 84 / 其余少量 |
| 图层 | **177 个**，按专业分：`EQUIP-照明/应急/箱柜/消防/通讯/安防`、`WIRE-*`、`CABLETRAY*`、`0-墙/0-窗`、`C-天花灯具`、`0-开关` |
| INSERT 块名 | 221 种，**大多为乱码/自动生成**（`*U250`、`A$Cdc63680e`、`yld…-2-220`、`dfsdsdggg`），少数带中文 |
| 块内部结构 | 设备块内部实体**基本落在图层 `0`**（BYBLOCK），如 `*U250` = CIRCLE×2+LINE×6 |
| 嵌套块 | **168 / 641** 个块定义内含子 INSERT |
| 导线 | 顶层 LWPOLYLINE，按 `WIRE-照明 656 / XT_WIRE 233 / WIRE-网络 82 …` 分布，**不在块内** |

#### **12.7.2 手算表 = 参考实现（关键洞察）**

手算表 `分层统计` sheet（202 行）本身就是 Phase 5b 要**程序复现的中间表**，其口径为：
```
按 (图纸区域, 类别∈{块计数,线长}, 图层, 块名/线型) → 数量/长度
```
即人工统计**不依赖块名语义**，而是按 `(图层, 块名)` 分组计数 + 按图层求线长。`计量说明` 佐证：
"平面工程量按标题框切分为 23A/…；DWG 经 AutoCAD 2024 转换；缺外部参照与正式图例（消防/安防需复核）"。

#### **12.7.3 核心决策：保持块完整，禁止预打散（EXPLODE）**

**决策**：设备计数遍历**顶层 INSERT 原样**，按 `(区域, 图层, 块名)` 计数；**不在识别前打散图形**。

**依据（三条实测硬证据）**：
1. 设备块内部实体全在图层 `0`，打散后 1 个设备 → 8~73 根散落图元，**可数单位被摧毁**，且反推不出原设备。
2. 要测长度的导线本就是顶层多段线，**无块可打散**，打散对线长统计零收益。
3. 168 个块含嵌套 INSERT，打散会**连锁递归展开**，子碎片归属错乱。
4. 附加副作用：块 `A$Cdc63680e` 内实体在 `P-完成面`、`A$C1d68b013` 在 `0-建筑外轮廓`，打散会**污染按图层分类**的统计。

**"打散提高识别率"的直觉属于栅格化路径**（渲染成图再 OpenCV 找轮廓）；Phase 5b 走**矢量路径**，选它的理由正是块已是 CAD 组织好的语义单元，无需打散。

**唯一例外（外科手术式）**：个别需要世界坐标几何或块内文字的块，用 `insert.virtual_entities()` **临时**展开取用，取完即弃，绝不物理破坏 `doc` 结构。

#### **12.7.4 提取器职责与增量划分**

| 增量 | 范围 | 验收 |
|------|------|------|
| **5b-1 全图计数**（先做） | 顶层 INSERT 按 `(图层,块名)` 计数 + 顶层 `WIRE-*` 多段线按图层求长（/1000 转米）；**暂不切分标题框区域** | 各块名/图层的**全图总量** == 手算表 `分层统计` 对应项**跨区域求和**，偏差 <2% |
| **5b-2 标题框切分** | 识别图框（大 LWPOLYLINE 矩形 / 含"图框"块 / TEXT 区域名）→ 按 `23A/25层…` 分区 | 逐区域行 == `分层统计` |
| **5b-3 图层→清单映射** | 图层/块名 → 20 项清单类别映射表；聚合出 `工程量计算表` | 20 项工程量偏差 <2% |

**说明**：5b-1 故意绕开最难的"标题框切分"，先用可对账的总量验证提取管线正确性，降低首版风险。

#### **12.7.5 落库接口（对齐 §3.1 / Phase 5d）**

- 中间结果写入 `drawing_elements`（`source_type='dxf'`），设备行存 `(layer, block_name, insert_point, count)`，线长行存 `(layer, total_length_m)`。
- 与 OpenCV 栅格管线**同表不同行**，靠 `source_type` + `source_authority` 区分；DXF 矢量行 `source_authority='geometry'`（可信几何，参与算量）。

#### **12.7.6 增量 2 实测结论（v1.2 追加，标题框区域切分）**

图框在 DXF 中的形态已查明：**`TEL_TITLE` 图层上的 A1 幅面（59400×84100mm）闭合矩形多段线**，成对（外框+内框，需按中心去重）；标题文字形如 `"23A层展示区 照明平面图"`，正则 `(\d+[A-Z]?)层.*?(应急照明|配电|照明|插座|火灾报警|弱电)平面图` 提取区域标签。

**验证结果**：
- 12/12 平面框检测与标注均正确，区域集合与手算表 `set` 完全相等（另有 3 个系统图框，非平面，已正确排除）。
- **逐块跨区域总量 37/50 精确命中**（`*U250` 194=194 / `A$C1DDE0375` 59=59 / `L-可调筒灯1` 52=52）；25* 全区域逐行精确。

**两大剩余差异（均为口径问题，不是区域切分 bug → 归 5b-3）**：
1. **人工跨系统二次归类**：同一符号（如某筒灯）人工按"哪个回路供电"拆记到照明/插座两个区域，我按物理图框全归照明——**跨区域总量一致**，仅子分配不同。
2. **人工只 tally 清单相关块**：手算表块总量 956 vs 平面框内总 INSERT 3656；人工只统计映射到 20 项清单的 ~69 种块，其余装饰/参照/图例块未计。

**推论**：区域切分机制已可用；真正达 <2% 验收的关键在 **5b-3 的图例驱动"块名/图层→清单类别"映射表**（需从图框内图例表或系统图读取块→设备类型对应关系）。

#### **12.7.7 装修支路（227 户型）实测结论（v1.2 追加，回归项目主线）**

电气图只验证了**提取管线**，非装修语义。用户补入**广州珠实金融城 227 住宅户型整套装修施工图**（平面/立面/天花/地面/墙身/固装大样 + 空间做法表 + 工程量清单），这是项目核心目标（§十一 墙/房间/门窗 2.5D 实体 → 装修算量）的**首个真实主线物料**。

**图纸获取**：原生 DWG，本机无 ODA/libredwg；用户以 **ODA File Converter 导出 `2018 ASCII DXF`**（AC1032，Unicode 保中文图层名；R12/R9 ASCII DXF 会乱码，禁用）。ezdxf 直接可读。

**05 平面图画像（实测）**：AC1032，modelspace 3143 实体；**英文图层且分层极干净**（异于电气中文图层）——`WALL` 330 / `WINDOW` 110 / `DOOR` 52 / `CEILING` 127 / `floor` 130 / `FIN` 421 / `STAIR` 148；`DIMENSION` 534（定标充裕）。墙=散线段(244 LINE+78 LWPOLYLINE)非闭合；门窗=**Autodesk AEC 库块**（`DorLib2D$*`/`WINLIB2D$*`，自带语义）。

**关键坑：中文以 `\M+5HHHH` GBK 转义存储**，ezdxf 读出乱码、直接匹配中文全落空。已建 `textcodec.decode_acad_text`（`bytes.fromhex(HHHH).decode('gbk')`）还原，实测房间标签（卧室二/主人卧室/客厅茶室/消防电梯…）全部解出。

**清单画像（答案卷）**：`金融城227户型样板房工程量清单.xlsx`，装修 sheet `ZS02-A-精装修工程-227户型清单` **240 个工程量项、76% 为 m² 面积**（地面 71/墙柱面 75/天棚 84/其他 10）；按**分部→房间→项**组织；**228 项带"计算式"**，其中 33 项含真推导式，暴露测量规则（墙面 `宽×净高2.57`引用**立面编号 E-xx**；踢脚线 `墙长累减洞口`引用**平面图**）。→ 印证：**地面/天棚面积走平面图（易，先做）**，**墙柱面走立面图+层高（难，后做）**。

**面积提取（`decoration.net_area_by_layer`）**：净面积**必须 shapely 布尔 union**，不能对多边形求和——同层含重叠/嵌套（实测 floor naive 118㎡ → union 92㎡；CEILING naive 348㎡ → union 69㎡）。房间级多边形面积尺度真实（19.3/14.4/13.4㎡）。

**诚实校准（未达标声明）**：floor union 92㎡ 与 BOQ 单项"找平 93.21"接近 **纯属巧合**——两者量纲不同：BOQ 地面代数和 429.2㎡（每房间找平+石材+木地板+垫层多层累加），且地面饰面按材料被切成多块、房间含多个饰面区、清单含"每增减"正负对、多视图上下堆叠（全图 bbox 22.7×78.8m）。**逐房间/逐材料对账尚未成立。**

**下一步（5b-D2b）**：真正的装修面积对账需**从 `WALL` 层散线段重建房间闭合区域**（连接→闭合→带房间标签的多边形，用 `textcodec` 标签做 point-in-polygon 归属），再以房间净面积为锚对齐 BOQ 各饰面层（处理增减对）。这是 §十一 2.5D 的核心难点，非改改计数可得。已固化 `scripts/phase5b_decoration_recon.py` 为量级诊断工具（明确不声明达标）。

#### **12.7.8 增量 5b-D2b 实测：源数据阻塞（负结果，重要）**

按 §12.7.7 计划执行"从 WALL 重建房间"，实测**该路线在 227 这份 DXF 上不可行**，逐条证据：

1. **墙线碎片化、无法围合**：`WALL` 层 394 条线段，中位长 0.35m、47% 短于 0.3m；`unary_union`+`polygonize` 仅得 45 个碎片面，**房间尺度(5-60㎡)闭合面 = 0**。合并 `WALL+WINDOW+DOOR(+STAIR/FIN/FIXTURE/FURNITURE)` 仍 **0 个房间面**（门窗是 INSERT 块、几何在块内未参与 node；且墙在每洞口断开）。
2. **无 XREF 可救**：`doc.blocks` 中 `is_xref` 块 **0 个**（ODA 已拍平）；全图仅 1 个裸 `WALL` 层，无 `A-WALL` 变体。主墙轮廓**根本不以任何可用线形式存在**。
3. **房间标签极稀**：全平面仅 **9 个 TEXT 房间标签**（<13 间房），MTEXT 房间标签 **0**、块属性房间标签 **0** → 无法可靠给每个房间多边形贴标签。
4. **材料无干净编码**：`floor` 闭合多边形颜色 48/72 均为色号 251（非材料编码）、HATCH 仅 4 个 → 多边形**分不出石材/木地板/瓷砖**。
5. **空间做法表不可读**：`04.空间做法表.dxf` modelspace 空、内容在布局里为 **4 个 OLE2FRAME（内嵌 Excel）**，ezdxf 取不到文字 → 房间→材料→面积 的权威映射**拿不到**。

**结论**：逐房间/逐材料 <2% 对账**被源数据本身卡死**（墙碎片 + 标签稀疏 + 材料无编码 + 做法表 OLE），**非算法可解**。强行写脆弱映射器只会产出假数字，违背"宁可如实报未达标"纪律，故本增量**不产出映射器代码**。

**可行的三条出路（待定方向）**：
- **A 换更干净的源**：向用户索取**完整墙的原始结构图**（墙能围合）、或**做法表的 Excel/文本版**（非 OLE）、或**建筑师 IFC**——任一到位即可解锁逐房间对账。
- **B 视觉路线（契合本项目多 Agent+GLM-4V 设计）**：矢量语义拿不到的（做法表/房间面积），改用**渲染图 + 视觉模型**读取，绕开 DXF 语义缺失。
- **C 只做聚合级校验**：放弃逐房间，退到**整户净地面面积**（floor polygon union）对 BOQ 地面饰面总量（需正确剔除找平/垫层/增减子层）——粗尺度合理性，非 <2%。

**C 实测结果（用户无结构图/视觉模型，选 C；并核查 PDF）**：
- **PDF 无校验价值**：整套 51MB 施工图纸 PDF 经 PyMuPDF 提取，**无任何面积表/工程量**（使用面积/套内/建筑面积 全 0 命中）；p4-7 为**构造做法表=做法规格字典**（地面-1/墙身-5/吊顶-1 的材料层次说明），不含面积。故 PDF **无法充当数值校验目标**。
- **聚合对账仍不可靠**：单层锚点（只取正项）——地面几何 union 92.0㎡ vs BOQ 找平层 183.1㎡(12行)，偏差 −49.8%；天棚几何 union 69.2㎡ vs BOQ 吊顶 196.2㎡(16行)，偏差 −64.7%。几何约为清单的一半。
- **已排除"面积源不全"**：HATCH 在 floor 层仅 4 个、CEILING 6 个（非面积载体）；除 floor/CEILING 外无其他图层含房间尺度闭合多边形。→ **该图纸的 floor/CEILING 层本就只画了部分饰面分区，未铺满整户**，不是提取算法漏算。

**根因综合（§12.7.8 定论）**：227 是**面向施工队的节点详图集**，面积不以"完整、可干净归属的闭合几何"编码；叠加墙碎片化 + 标签稀疏 + 做法表 OLE，**矢量 DXF 提取无法从该文件复现其清单数字**（逐房间与聚合两级皆不可靠）。这是**源数据性质**决定的，非算法可补。

**对系统的启示**：电气图能算准是因工程量=**数块**（INSERT，DXF 编码无损）；装修工程量=**面积**，需图纸层本身铺满闭合区域，或改以**清单/做法表(数据)**为面积源。故装修支路在"面积自动复算"上应：(1) 换用**层域铺满的地面/天花布置图**再验提取；或 (2) 系统定位退回**解析+审计结构化清单**（xlsx 即面积源）+ Phase 4.5 一致性校验（地面≈天棚、门窗挂墙），而非从不含面积的图纸硬抠。

#### **12.7.9 §12.7.8 根因纠正：墙线丢失系 XREF 未绑定（可修复，非死路）**

用户指出"平面图中墙线是闭合的"，复核后**证实用户正确**，§12.7.8 第 2 点"无 XREF 可救"系**误诊**（当时只查 `is_xref` 块=0，漏看了拍平的 `$0$` 空图层壳）。重测铁证：

1. **WALL 层围不出房间是事实，但非源数据死路**：对 `WALL` 394 线做①端点吸附容差 0~50mm ②桥接门洞断头 0.9~1.5m ③buffer 加厚成实心墙带 50~150mm 取内孔——**三种工业级闭合强化全部房间尺度面=0**。裸 `WALL` 层是精装修团队自绘的装饰/隔墙线（308m，非完整建筑轮廓）。
2. **真正的建筑墙在未绑定的 XREF 里**：图层表中存在 `1#$0$S-WALL`、`S-WALL`、`1#$0$PUB_HATCH`、`1#$0$S-PUB-FINE` 等 **`$0$` 外部参照绑定壳**（`1#`=1号楼、`S-WALL`=结构专业墙层标准命名）。但这些壳层**几乎空**——全图 `S-WALL` 相关实体**仅 3 个**，横跨 76m 多视图堆叠，**不可能围合 13 间房**。
3. **结论**：用户在 AutoCAD 看到的闭合墙是**软件实时加载建筑 XREF 渲染**所得；ODA File Converter 转 DXF 时**未把该参照的几何嵌入**，只残留空图层名。故此前"逐房间对账被源数据卡死"应修正为"**被有损导出卡死，可通过重新绑定 XREF 解决**"。

**修复动作（解锁 5b-D2b）**：重新导出 DXF 前在 AutoCAD 内 `XREF` → **Bind 绑定所有外部参照**（或 `-XBIND`）→ 再另存 2018 ASCII DXF（ODA 不主动 bind，故须先在 AutoCAD 内绑定）。重导后 `S-WALL` 携带真实墙线，即可从墙围合房间、跑通逐房间面积对账。**教训**：DWG→DXF 转换后必须先验证"XREF 是否已拍平为几何"（查 `$0$` 壳层实体数、`S-WALL` 等关键层是否非空），否则会把"导出丢图层"误判成"图纸没画"。

> ⚠️ **本节（§12.7.9）"XREF 未绑定导致墙丢失"的结论又被 §12.7.10 推翻**——墙并未丢失，是分析代码漏展开块。

#### **12.7.10 再纠正：墙几何其实完整，之前两节皆因未展开 INSERT 块而误诊**

用户反馈"参照管理器里只有图片、无 DWG 参照可 Bind，但 CAD 里能看到墙"，据此复核，**§12.7.8/§12.7.9 的"墙丢失/需重导"结论全部作废**，根因是**分析代码只遍历 `modelspace()` 顶层实体**：

1. **墙藏在块里**：建筑墙几何以 **INSERT 块引用**形式存在（含已绑定的参照块 `1#$0$S-WALL`、`415-1#...$0$S-WALL`）。顶层遍历时一个 INSERT 只算 1 个实体，故 §12.7.9 误见"S-WALL 仅 3 实体"。改用 `insert.virtual_entities()`（自动应用块变换）递归展开后：**227 平面共 1246 段 / 1293m 墙线**（`WALL` 687m + `S-WALL` 315m + 参照 `S-WALL` 290m）。**墙完全在文件里，DXF 无需重导。**
2. **但围合仍是硬问题（算法而非数据）**：展开块后重跑 polygonize——① 全图墙网吸附 0~20mm、② buffer 加厚成实心墙带 60~120mm 取内孔、③ 按 Y 切单视图带内围合——**房间尺度(5-60㎡)闭合面恒为 0~1**。原因：平面图墙体是**双线**、门洞处线断开，polygonize 得到的是**墙条截面**（175 个细面）而非房间内部。这是 CAD 界公认的双线墙→房间围合难题。
3. **`dxf_healthcheck.py` 同步修正**：加入块展开，判定改为区分"墙线<50m=真缺失(FAIL)"与"墙充足但围不出=算法难题(WARN，无需重导)"。

**5b-D2b 真正的下一步（算法，非换数据）**：从双线墙围合房间需专门处理，候选：(a) **用门块 `DorLib2D`/窗块 `WINLIB2D` 的位置在洞口补封口线**，再 polygonize；(b) 沿墙**中心线骨架化**后按房间拓扑切分；(c) 直接采用设计师已画的 **floor/CEILING 饰面闭合多边形**作房间源（展开块后 floor union 91㎡、含 10 个 3-60㎡面，但覆盖不全、且多视图需先隔离）。推荐先做 (a)——门块自带洞口语义，补缝最干净。

> ⚠️ **方案 (a) 经 §12.7.11 实测证伪**：墙网碎片化，补门缝无法围合。

#### **12.7.11 门窗块分布实测：补封口围合方案不成立（墙网非连通壳）**

按 §12.7.10 推荐先验证方案 (a)，对门/窗块分布做完整普查：

1. **门窗块数据本身干净可用**：共 **33 门块 + 13 窗块**（`DorLib2D`/`WINLIB2D`/`TCHSYS$DOOR2D` 系），**旋转角仅 0°/90°**（轴对齐），**缩放值直接编码洞口尺寸**（如 (900,-200)：x=门宽0.9m、y=墙厚0.2m）。补封口的语义信息充分。
2. **但墙网是碎的（致命）**：展开块后的 1246 段墙做并查集连通分析——全图 **106 个连通分量**；即使最好的单视图带 Y[227860,227880) 也有 **21 个分量，最大分量仅覆盖 10.0×7.9m**（半个户型）。墙线在大量转角/T 型交接处根本没坐标相接，**不是一栋可围合的建筑壳**（装修图惯例：只画装饰涉及的部分墙）。
3. **补封口 + buffer 均失败**：用门窗位置就近连接断头端点（补 34 条）后 polygonize，房间面 2→2（无变化）；将墙线 buffer 加厚 15~70cm 取内孔，**合理厚度全为 0 个房间空洞，70cm（会把一切糊在一起）也仅 1 个**。因为要补的不只是 46 个门缝，而是 106 个分量间 pervasive 的缝隙。
4. **饰面层也不能兼平（方案 c 同样不成立）**：floor/CEILING 闭合多边形集中在单一视图带 Y[227780,227820)；floor 仅 13 个面、union **52.4㎡**（远小于整户）；CEILING 多边形**严重重叠**（naive 342㎡ → union 仅 64㎡）。两者都**未铺满整户**，只能做部分房间的饰面抽检。

**定论（推翻 §12.7.10 的方案 a）**：从这份装修平面**自动提取逐房间几何不可行**——墙网非连通壳 + 饰面多边形不全覆盖 + 房间标签仅 9 个 TEXT（即使几何能围也无法可靠命名）。这是**源数据性质**（装饰图不画完整建筑壳）决定，非算法可补。

**装修支路的务实定位（与 §12.7.8 结论(2) 一致，但理由已纠正）**：以 **BOQ xlsx 为面积权威源**（已含逐房间/逐材料工程量 + 计算式），系统价值在于 **解析 + 审计 + 交叉校验结构化清单**，而非从不含完整面积的图纸硬抠几何。几何提取退到**聚合级一致性校验**（Phase 4.5：门窗挂墙、地面≈天棚守恒）与**饰面多边形对部分房间的抽检**。

> ⚠️ **§12.7.11 的"只能退回 BOQ"结论被 §12.7.12 推翻**：生产环境无 BOQ 可审计，按图算量才是目的。用户指出应改用**地面铺装图**（PDF P12）作面积源——实测可行。

#### **12.7.12 地面铺装图（PDF P12）材料区面积提取：按图算量的可行面积源**

用户纠正：以后要按图算量，没有 BOQ 给你审计。据此换数据源——不再从 05 平面 DXF 抠墙，改用 **PDF《广州珠实金融城住宅项目--227户型施工图纸.pdf》第 23 页 = P12 地面铺装图（1:100 A3）**。

**为什么铺装图能成、05 平面 DXF 不能**：铺装图的墙是**渲染后的实心/双线混合几何**，配合材料图案线一起 polygonize 时，图案线恰好**帮助封闭**了材料区边界；且整页矢量密度极高（30011 路径），远比 DXF 碎墙完整。

**标定**：1 pt = 25.4/72 mm(纸) × 100(比例) = **0.03528 m(实)**；1 pt² ≈ 1.244e-3 ㎡。

**方法（已跑通，脚本 `/tmp/pdf_regions*.py`）**：
1. 隔离主平面 bbox（pt 215–805 × 135–705），排除两个 1:30 拼花大样与图框；
2. 取该 bbox 内**全部 LINE+RECT 矢量段**（21137 段），snap=1.5pt 后端点吸附 → `polygonize(unary_union)`；
3. 过滤：面积 3–70㎡、最小边 ≥0.9m、长宽比 ≤4.5（去墙缝细条与区外噪声）。

**结果（稳定）**：跨 snap 0.5~3.0pt 恒得 **12 个材料区、合计 ~125.8㎡**（合理：227 户型净铺装面）。逐区尺寸真实（如 66.2㎡ 客餐厅玄关整片石材、7.2㎡ 入口拼花、4.5/4.0㎡ 人字纹木地板卧室、3.3㎡ 等）。

**能力边界（诚实记录）**：
- ✅ **可靠提取"区域 + 面积"**——按图算量的面积源成立。
- ❌ **无法从该 PDF 自动贴材料标签**：材料码 ST-01/WF-01/TL-02 是**矢量描边**（页文本层仅 807 字，不含这些码），且材料靠**图案**非颜色区分。区域→材料的归属需 (i) 图案纹理分类、或 (ii) 半自动人工点选、或 (iii) 找到铺装图的 DXF 源（当前 18 个 DXF 缺此图）。
- ⚠️ **开敞区会合并**：66㎡ 那坨是客厅+餐厅+玄关连续石材——作为"石材量"正确，作为"逐房间"则合并了。铺装算量本就以材料区为单元，此特性可接受。
- ⚠️ 部分区域漏检/多含（阳台、小卫生间），需 bbox 精修与规则清理。

**5b-D2b 修正后的下一步**：把铺装图材料区提取**产品化**——(1) 材料→区域归属：优先争取 P12 的 DXF 源（HATCH 自带材料层/图案名），否则做图案纹理分类或人工点选；(2) bbox 自适应隔离（不写死坐标）；(3) 面积标定改用图内标注尺寸反算校验（当前 1:100 假设需核对）；(4) 与 05 平面 DXF 的门窗/饰面做交叉一致性校验。

---

### 12.7.13 完整覆盖清单 vs BOQ 对账（v1.3，227 户型 · 权威基准回归）

> 触发：用户要求「跑成一个完整覆盖的清单对比 BOQ」。**关键澄清**：此前误把该户当 ~125㎡，实为 **227 户型（建筑面积 227㎡，净地面 ~183㎡）**——这正解释了 §12.7.11/12 里 DXF 92㎡、PDF 125㎡ 为何都被用户判为「覆盖不全、只是一部分」。

**BOQ 权威目标**（`金融城227户型样板房工程量清单.xlsx` · `ZS02-A-精装修工程-227户型清单`，取每房间 `地面水泥砂浆找平层`(ZS02-A-001) 面积＝该房间地面）：

| # | 房间 | 地面㎡ | # | 房间 | 地面㎡ |
|---|---|---|---|---|---|
| 1 | 客厅(客餐厅+玄关) | **93.21** | 7 | 卧室二 | 13.41 |
| 2 | 厨房 | 11.09 | 8 | 收纳间 | 2.59 |
| 3 | 储物间(安全室) | 3.07 | 9 | 主卫 | 8.92 |
| 4 | 阳台 | 5.61 | 10 | 浴室一 | 3.90 |
| 5 | 主卧 | 21.71 | 11 | 浴室二 | 3.82 |
| 6 | 卧室一 | 10.86 | 12 | 浴室三 | 4.95 |

合计 **183.14 ㎡**（12 房间）。**客厅一区 93.21 = 全屋地面 51%**。

**对账结论（实测，非纸面）**：

1. **DXF `floor` 层闭合多段线（模型空间顶层，不递归进块）＝ 精确的「除客厅外所有房间」**：union **91.97㎡** vs BOQ 非客厅合计 **89.93㎡ → 差 +2.3%**。逐小间命中良好：厨房 11.02(-1%)、阳台 5.92(+6%)、储物间 2.95、收纳间 2.72(+5%)、浴室一 3.64(-7%)。**教训重申**：递归进块会混入多份图纸拷贝＋0.63㎡单块地砖噪声，把 union 从 92 拖到 85/89 的假值；**必须只取 msp 顶层**（与 `decoration.py` 一致）。
2. **缺口＝客厅 93.21㎡**：floor 层无对应闭合区、无 floor HATCH（最大 floor HATCH 仅 5.1㎡）。它在图上只有墙线边界＋铺贴分格线，未作为闭合填充给出 → 是唯一未攻克的围合点（墙线碎、门洞、多拷贝，polygonize 失败，同 §12.7.11）。
3. **算术闭环**：DXF floor 小房间(92) ＋ 客厅开敞(93) ≈ 185 ≈ BOQ 183（±1%）。**说明「完整覆盖」= 两半拼合**，任一半单独都不完整——用户判断成立。
4. **天花板层不可用作地面替身**：`CEILING` 有 85㎡ 大区，但为嵌套吊顶（naive 348 / union 69），非干净房间分解。
5. **PDF P12 polygonize**：125.8㎡，仍局部（客厅餐厅合并仅 66 < 93，且漏若干卧室）。
6. **逐房间自动命名仍不可行**：DXF 无房间文字标签，材料码 ST/WF/CT 在 PDF/DXF 均为矢量描边；面积最近匹配会错配（主卧21.71↔19.31、卧室二13.41 缺失被贪心错分）。**区域↔房间↔材料**三元绑定需 HATCH 图案名 / 纹理分类 / 人工点选，尚缺自动依据。

**修正后的下一步（收窄为单点）**：唯一实质缺口是把 **客厅 93.21㎡ 从墙线围合出来**。可选路径：(a) 隔离单一铺装视图的墙线（先解决多拷贝/视口定位）再 polygonize；(b) 用「整户外壳（外墙闭合面）− floor 小房间并集 − 墙厚」反解开敞区；(c) 退而用 PDF P12 客厅区补面积并标注为「近似·待核」。逐房间**命名**与**材料归属**是另一条独立线，不宜与「完整面积覆盖」混做。

---

### 12.7.14 a/b/c 三法盲测 bake-off：无 BOQ 输入下的完整地面覆盖对比（v1.4）

> **方法论约束（用户明确）**：生产环境无 BOQ，故 a/b/c **均只喂图纸几何、算完再各自对 BOQ**；BOQ 是留出考卷，**绝不参与面积计算**。判别证据：b 闭运算盲算返回 0（墙不封）时**如实给 0**，不回填 BOQ 的 93.21。

三法各自**盲算**整户地面覆盖（目标 BOQ **183.14㎡ / 12 房间，客厅占 51%**）：

| 法 | 数据源/做法 | 盲算合计 | 覆盖率 | 开敞客厅? | 房间级可用区 |
|---|---|---|---|---|---|
| **a** | DXF 结构墙层 polygonize | **~0**（仅 36.6㎡ 双线夹缝碎面，8–60㎡ 房间面=0） | ~0–20% | ❌ | 0（围不出房间）|
| **b** | DXF 外壳 − floor 房间 | **92.0**（=floor 层小房间）| 50% | ❌（外壳隔离失败:得到 1233㎡=整层楼板多户）| 11 小房间 |
| **c** | PDF P12 矢量 polygonize | **130.8**（snap 1.0–3.0 稳定 121–131）| **71%** | ✅ 66.2㎡ | 12 区 |

**结论**：
1. **c 明显最优**：唯一能围出**开敞客厅**（66㎡）者，覆盖 71% ≫ b 50% ≫ a 基本失败。**根因**：PDF 渲染把双线墙合并成实心笔画、能在门洞处封住；DXF 原始墙线碎且留门洞，polygonize/flood-fill 全漏（§12.7.11 根因复现）。
2. **b 的价值在精度不在完整**：floor 层闭合区是设计师**地面饰面边界意图**，对已捕获小房间 ±数%（厨房-1%/阳台+6%/收纳+5%），比 c 的"从线面反推"更可信；但它天生不含开敞区。
3. **a 判死刑**：这份 DXF 墙层围不出任何 8–60㎡ 房间面，也无单户外壳 → 纯几何 polygonize 在本文件不可用。
4. **重要发现——BOQ 与图纸对客厅口径不一致**：BOQ「客厅」=93.21，但图上开敞连续区无论 DXF(无) 还是 PDF(仅 66) 都**达不到 93**。差的 ~27㎡ 极可能是 BOQ 把玄关/走道/门槛等**并入"客厅"清单项**的口径差，而非算漏。**这是真实差异，应按图为准披露**（图=66）。

**建议生产路线（无 BOQ 成立）**：**b 供小房间精确值 + c 供开敞区**，两者按位置去重合并 → 整户地面最佳估计 ≈ b∪c；剩余客厅 66(图) vs 93(BOQ) 差异作为**审计发现**列出。下一步：把 b∪c 合并逻辑产品化（区域几何求交去重），并按图口径给出客厅的"待核"标注。

---

### 12.7.15 b∪c 几何合并尝试与配准验证（v1.5）——**未通过验证，不采信**

> 目标：把 b（DXF floor 房间）与 c（PDF P12 区域）刚性配准后求并，得一个不依赖 BOQ 的完整覆盖清单。

**做法**：PDF pt → 米（PT2M）+ y 翻转；以“平移投票（RANSAC）+ 8 二面体”粗配准，再用 9 对匹配房间质心做最小二乘相似变换精配准。

**验证结果（诚实）**：
1. **尺度标定得到验证**（有用副产物）：反解 s(b/c)=**1.017** → PDF 真实比例 ≈ **1:98**，与 1:100 假设仅差 −3% 面积。**§12.7.12 的 PT2M 标定成立**。
2. **但配准不收敛**：b 房间质心落入 c 仅 **4–5/11**，配准残差中位 ~0.9m；且 **b∪c 从 172.8→180.0 虚涨而质量反降**（含入率不升反降）→ 典型错位重复计数。叠加图目视：多个蓝房间处无对应红面，两图内部房间对不上（疑存在非 0/90/180/270 的旋转或两图内容不完全一致）。
3. **结论：拒绝将几何 b∪c 作为经验证的完整覆盖数**（与不拿 BOQ 凑客厅同一原则：不拿不可信的叠加数字交差）。

**改而采用“按角色不重叠拼合”（可辩护、不需叠加）**：b 的闭合小房间（85.2）与 c 的**开敞客厅**（~66）天然不相交（b 无客厅、只取 c 的开敞部分）→
> **整户地面（仅凭图、无 BOQ、无叠加）≈ 85.2 + 66 ≈ 151 ㎡ = BOQ 183.14 的 82%**。

**残余 32 ㎡ 差异定位**：主要是客厅口径（图 66 vs BOQ 93.21，差 ~27）+ b 漏个别小间（如浴室三）。**锁定结论：按图客厅=66，BOQ 客厅=93 的 ~27 差为真实口径差异（BOQ 将玄关/走道/门槛并入“客厅”项），应披露而非掩盖。**

**若要真正打通几何合并（待办）**：需两视图角点/轴网控制点配准（非靠房间 centroid 最近邻），或确认 PDF 与 DXF 为同一坐标参考。当前不阻塞结论。（→ 已于 §12.7.16 推进并得出决定性结论：该配准病态、不可行。）

---

### 12.7.16 控制点鲁棒配准攻坚（v1.6）——**A 方案：几何配准被证明为病态，弃用**

> 用户选定 A：换控制点（非最近邻）再攻配准。采用 RANSAC 相似变换 + 剔除开敞客厅离群对 + 尺度先验≈1。

**尝试与结果**：
1. 平移投票 + 8 二面体：b 房间质心落入 c = 5/11，b∪c=172.8（含入率低）。
2. 9 对最近邻最小二乘相似变换精配准：落入降至 4/11，b∪c 虚涨至 180.0（质量反降）。
3. **RANSAC 相似变换（控制点=去客厅后的房间质心）**：仅 **3/11 内点**，且回落到假旋转 −107° → **两组控制点间不存在一致相似变换**。

**病态根因（面积签名铁证）**：
- **b（DXF floor）按房间边界拆分**：`19.3/14.4/13.4/11.0/5.9/…`
- **c（PDF 地面铺装图）按铺材贴片拆分**：`67.2/12.7/7.4/7.3/7.1/4.6/…`——**无 21.7/13.4/11.1 等房间尺寸**；主卧(21.7)在 c 里被材料缝拆成 3 块 ~7。
- 两者对地面的**拆分语义不同** → 房间↔房间无一一对应 → 任何基于房间控制点的刚性/相似配准必然失败。**结论：可信的 b∪c 几何叠加在此数据上做不到，正式弃用。**

**有价值的副产物（均不碰 BOQ）**：
- **尺度标定验证**：RANSAC 反解 |a|=**1.007** → PDF 真实比例≈**1:100**（§12.7.12 的 PT2M 成立）。
- **单一源 c 即最全覆盖**：铺材面（≥1㎡、互不重叠）**面积和 ≈ 158 ㎡ = BOQ 183.14 的 86%**，优于任何合并尝试且无需配准。

**修正生产结论（取代 §12.7.14/15 的“b∪c 叠加”建议）**：**不要叠加 DXF-房间与 PDF-铺材（语义不同）**。按用途各取单一一致源：
- **整户地面总量** → 用 **c 铺装图**（≥1㎡ 面求和 ≈158㎡，86%）；
- **逐房间面积** → 用 **b floor 层**（小房间 ±数%，但结构性缺客厅）；
- **客厅口径** → 图 67 vs BOQ 93.21 的 ~26㎡ 差为真实口径差异（BOQ 并入玄关/走道），**披露不掩盖**。

房间↔材料归属仍需文字标签/HATCH 图案名（独立线，见后续）。（→ 已于 §12.7.17 突破：标签在平面布置图，材料表在物料說明 PDF。）

---

### 12.7.17 房间↔材料归属：从**材料表/平面布置图**突破（v1.7）

> 用户提示：除 HATCH 外可查**材料表**（文件夹内）。验证成立，并推翻 §12.7.14 “图纸无房间标签”的判断。

**来源定位**：
- `04.空间做法表.dxf`：“材料表”布局**只有 4 个图签**（标“见图(A3)”），正文不在 DXF 文字。
- `227户型 物料表/物料說明rev-04.pdf` p1 **概要表**：材料**代号→名称→使用部位**（部位已含房间名）。
- `227户型 物料表/大板图rev-05.pdf`：每页一个代号的样板详情（非标注平面）。
- **施工图纸.pdf p11 平面布置图（1:100）**：**房间名+面积标在各房间位置上**——这是缺失的房间命名源。

**方法**：p11 用 `get_text("words")` 取带坐标的词，将“NN.N”面积 token 就近匹配房间名（全≤ 13pt，可靠）；材料表用代号→部位建立房间→材料。

**图纸自标的 16 间房清单（非 BOQ）**：客厅/茶室 65.6 | 餐厅 16.3 | 主人卧室 14.9 | 卧室二 12.4 | 中厨 11.6 | 主人衣帽间 11.2 | 卧室一 10.8 | 主人浴室 9.8 | 玄关 7.3 | 浴室三 5.6 | 生活阳台 5.6 | 浴室一 4.7 | 浴室二 4.5 | 收纳间 3.3 | 储物间 2.8 | 过厅 1.8。**合计 188.2㎡**（vs BOQ 183.14，+2.8%）。

**两个关键验证**：
1. **几何被图纸自标独立佐证**：客厅/茶室 **65.6（p11 标注）≈ c 法算的开敞客厅 66** → 之前靠几何围出的客厅面积得到确认（未用 BOQ）。
2. **客厅口径差被图纸坐实**：BOQ“客厅”93.21 ≈ 图纸 客厅/茶室65.6 + 餐厅16.3 + 玄关7.3 + 过厅1.8 ≈ **91–94**。→ BOQ 将餐厅/玄关/过厅**并入“客厅”清单项**，为真实口径差异。

**房间→地面材料（据材料表，纯图纸）**：
| 材料 | 代号 | 房间（图纸面积） |
|---|---|---|
| 石材 | ST01/ST02 | 客厅/茶室 + 餐厅 ≈ 81.9㎡ |
| 石材 | ST03 | 玄关 7.3㎡ |
| 木地板 | WF01 | 主人卧室 14.9㎡ |
| 木地板 | WF02 | 卧室一 + 卧室二 ≈ 23.2㎡ |
| 瓷砖/岩板 | TL-01/ST05 | 中厨、主人浴室、浴室一/二/三 |
| 瓷砖 | TL-02 | 生活阳台 5.6㎡ |

**生产启示**：房间命名/材料归属**可行且权威**，靠的是**平面布置图的房间标签 + 材料表**（均为设计文件，非 BOQ）；下一步可将 p11 标签按坐标 point-in-polygon 落到 b/c 区域，输出“区域→房间名→材料→面积”的完整清单。墙面/天花材料在各立面页按名称标注（p28+），可同样抽取。


