# 历史项目工程量知识库设计

> **文档版本**: v1.0
> **日期**: 2026-08-09

---

## 一、设计目标

建立项目类型感知的历史工程量知识库，核心能力：

1. **漏项检查** — 与历史同类项目对比，发现可能遗漏的工程量项
2. **项目类型识别** — 根据图纸特征自动判断项目类型
3. **经验规则提取** — 从历史数据中学习常见构件/材料/组合
4. **合理性校验** — 墙地比、灯具密度、开关插座密度等指标

**重要原则：历史数据只能用于"检查是否漏项"，不能直接"因为历史项目有，所以当前项目自动加"。必须回到当前图纸确认。**

---

## 二、数据库设计

### 2.1 历史项目表（已有，需扩展）

```sql
-- 当前已有: historical_projects (id, name, project_type, file_path, item_count, summary_json, user_id, project_id)

-- 扩展字段:
ALTER TABLE historical_projects ADD COLUMN building_type TEXT;       -- 建筑类型
ALTER TABLE historical_projects ADD COLUMN total_floor_area REAL;    -- 总建筑面积
ALTER TABLE historical_projects ADD COLUMN room_count INTEGER;       -- 房间数
ALTER TABLE historical_projects ADD COLUMN floor_count INTEGER;      -- 层数
ALTER TABLE historical_projects ADD COLUMN standard_version TEXT;    -- 清单规范版本
ALTER TABLE historical_projects ADD COLUMN imported_at TIMESTAMP;    -- 导入时间
ALTER TABLE historical_projects ADD COLUMN is_template BOOLEAN DEFAULT FALSE; -- 是否模板
```

### 2.2 历史清单标准化项表（新增）

```sql
CREATE TABLE historical_quantity_items (
    id INTEGER PRIMARY KEY,
    historical_project_id INTEGER NOT NULL REFERENCES historical_projects(id),

    -- 标准化特征（9 维度）
    division_id INTEGER NOT NULL,              -- 分部 (1-8)
    category TEXT,                             -- 大类 (room/wall/door/window/fixture/baseboard/ceiling/plumbing/electrical)
    material_type TEXT,                        -- 材料类型 (天然石材/瓷砖/木饰面/...)
    material_code TEXT,                        -- 材料编号 (ST-01, CT-02, ...)
    thickness TEXT,                            -- 厚度 (20mm, 18mm, ...)
    finish TEXT,                               -- 工艺 (湿贴/干挂/乳胶漆/...)
    room_type TEXT,                            -- 房间类型 (卫生间/客厅/厨房/...)
    room_area_category TEXT,                   -- 房间面积档 (S<5/5≤S<15/15≤S<30/S≥30)
    unit TEXT NOT NULL DEFAULT 'm²',           -- 单位

    -- 工程量
    quantity REAL NOT NULL,
    quantity_per_area REAL,                    -- 单位面积工程量

    -- 元数据
    original_label TEXT,                       -- 原始清单名称
    original_features TEXT,                    -- 原始项目特征描述
    source_confidence TEXT,                    -- 历史清单本身的置信度

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_hqi_division ON historical_quantity_items(historical_project_id, division_id);
CREATE INDEX idx_hqi_material ON historical_quantity_items(material_code);
CREATE INDEX idx_hqi_room_type ON historical_quantity_items(room_type);
```

### 2.3 项目类型模板表（新增）

```sql
CREATE TABLE historical_project_templates (
    id INTEGER PRIMARY KEY,
    project_type TEXT NOT NULL UNIQUE,         -- residential/commercial/lobby/showroom/hotel/office/...
    template_name TEXT NOT NULL,               -- 显示名称

    -- 统计特征（从历史数据聚合）
    total_projects INTEGER DEFAULT 0,          -- 样本量
    avg_floor_area REAL,                       -- 平均建筑面积
    avg_room_count REAL,                       -- 平均房间数

    -- 分部特征
    division_distribution JSON,                -- 各分部占总工程量的比例
    common_materials JSON,                     -- 高频材料清单
    common_items JSON,                         -- 高频清单项
    common_rooms JSON,                         -- 常见房间类型
    high_freq_missing JSON,                    -- 高频漏项清单

    -- 合理性指标
    wall_floor_ratio_range JSON,               -- 墙地比范围 {"min": 2.0, "max": 3.5, "median": 2.8}
    light_density_range JSON,                  -- 灯具密度
    socket_density_range JSON,                 -- 插座密度
    baseboard_ratio_range JSON,                -- 踢脚线/周长比
    waterproof_ratio_range JSON,               -- 防水面积/地面面积比

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 2.4 漏项规则表（新增）

```sql
CREATE TABLE historical_completeness_rules (
    id INTEGER PRIMARY KEY,
    rule_name TEXT NOT NULL,
    project_type TEXT,                         -- 适用项目类型（NULL = 通用）
    division_id INTEGER,                       -- 适用分部

    -- 触发条件（JSON）
    trigger_conditions JSON,                   -- 例: {"has_bathroom": true, "wall_material_includes": ["石材"]}
    -- 建议检查项
    suggested_items JSON,                      -- 例: [{"name": "卫生间-防水层", "unit": "m²", "division": 2}]

    -- 规则元数据
    confidence REAL DEFAULT 0.7,               -- 规则置信度
    evidence_count INTEGER DEFAULT 0,          -- 历史支持案例数
    false_positive_rate REAL,                  -- 误报率

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 三、标准化特征体系

### 3.1 特征维度（9 维）

| 维度 | 示例值 | 提取方法 |
|------|--------|---------|
| 分部 | 1-楼地面, 2-墙柱面... | 关键词映射 + DIVISION_NAMES |
| 材料类型 | 天然石材, 瓷砖, 木饰面, 乳胶漆... | 正则匹配 + 材料关键词表 |
| 材料编号 | ST-01, CT-02, WD-01... | 正则 `[A-Z]{2}-\d{2}` |
| 厚度 | 20mm, 18mm, 1.5mm... | 正则 `\d+\.?\d*\s*(mm|厚)` |
| 工艺 | 湿贴, 干挂, 乳胶漆, 烤漆... | 关键词匹配 |
| 房间类型 | 卫生间, 客厅, 厨房, 公共区域... | 标签提取 + 面积推断 |
| 面积档 | S<5, 5≤S<15, 15≤S<30, S≥30 | 面积统计分档 |
| 部位 | 部位描述 | 标签解析 |
| 单位 | m², m, 樘, 个, 套 | 直接提取 |

### 3.2 标准化流程

```python
def standardize_historical_item(raw_item: dict) -> HistoricalQuantityItem:
    """将原始清单项标准化为 9 维特征向量。"""

    label = raw_item.get('name', '')
    features = raw_item.get('features', '')
    room = raw_item.get('position', '')

    return HistoricalQuantityItem(
        division_id=_infer_division(label),
        category=_infer_category(label),
        material_type=_infer_material_type(label + features),
        material_code=_extract_material_code(label),
        thickness=_extract_thickness(features),
        finish=_infer_finish(features),
        room_type=_infer_room_type(room),
        room_area_category=_infer_area_category(raw_item.get('area', 0) or raw_item.get('quantity', 0)),
        unit=raw_item.get('unit', 'm²'),
        quantity=float(raw_item.get('quantity', 0)),
        original_label=label,
        original_features=features,
    )
```

### 3.3 相似度计算

```python
def item_similarity(item_a: dict, item_b: dict) -> float:
    """计算两个标准化清单项的相似度 (0-1)。

    权重分配：
    - 材料编号完全匹配: +0.30
    - 材料类型匹配: +0.20
    - 房间类型匹配: +0.15
    - 分部匹配: +0.15
    - 工艺匹配: +0.10
    - 厚度匹配: +0.10
    """
    score = 0.0

    # 核心维度（高权重）
    if item_a.get('material_code') == item_b.get('material_code') and item_a['material_code']:
        score += 0.30
    elif item_a.get('material_type') == item_b.get('material_type'):
        score += 0.20

    if item_a.get('room_type') == item_b.get('room_type'):
        score += 0.15

    if item_a.get('division_id') == item_b.get('division_id'):
        score += 0.15

    if item_a.get('finish') == item_b.get('finish'):
        score += 0.10

    if item_a.get('thickness') == item_b.get('thickness') and item_a['thickness']:
        score += 0.10

    return score
```

---

## 四、项目类型模板

### 4.1 模板结构

每种项目类型维护一个模板，记录该类型的典型特征：

```json
// 样板房模板示例
{
  "project_type": "residential",
  "template_name": "住宅/样板房",
  "total_projects": 45,
  "avg_floor_area": 112.5,
  "avg_room_count": 6.3,
  "division_distribution": {
    "1": {"ratio": 0.10, "common_items": ["瓷砖地面", "石材地面", "木地板"]},
    "2": {"ratio": 0.30, "common_items": ["乳胶漆墙面", "木饰面墙面", "瓷砖墙面", "石材墙面"]},
    "3": {"ratio": 0.08, "common_items": ["平顶乳胶漆", "轻钢龙骨石膏板"]},
    "4": {"ratio": 0.12, "common_items": ["木门", "铝合金窗"]},
    "5": {"ratio": 0.05, "common_items": ["木踢脚线", "瓷砖踢脚线"]},
    "6": {"ratio": 0.15, "common_items": ["厨柜", "衣柜", "台盆柜"]},
    "7": {"ratio": 0.05, "common_items": ["面盆龙头", "马桶", "淋浴花洒", "地漏"]},
    "8": {"ratio": 0.15, "common_items": ["开关插座", "灯具", "配电箱"]}
  },
  "common_materials": ["乳胶漆", "瓷砖", "石材", "木饰面", "木地板"],
  "high_freq_missing": [
    {"name": "卫生间防水层", "division": 2, "freq": 0.35},
    {"name": "地面找平层", "division": 1, "freq": 0.28},
    {"name": "窗台石", "division": 2, "freq": 0.22},
    {"name": "门槛石", "division": 1, "freq": 0.18},
    {"name": "浴霸/暖风机预留", "division": 8, "freq": 0.15}
  ],
  "wall_floor_ratio_range": {"min": 2.0, "max": 3.0, "median": 2.5}
}
```

### 4.2 模板初始化

系统内置 4 种项目类型模板（来自已有 `PROJECT_TYPES`），后续从历史数据中自动更新：

```python
DEFAULT_TEMPLATES = {
    'residential': '住宅/样板房（层高 2.80m）',
    'commercial': '商业/办公（层高 3.00m）',
    'lobby': '大堂/门厅（层高 4.50m）',
    'showroom': '售楼部/展厅（层高 4.00m）',
}
```

每次导入新的历史项目清单后，系统自动更新对应类型的模板统计数据。

---

## 五、HistoricalCompletenessAgent（漏项检查 Agent）设计

### 5.1 工作流程

```
当前项目 BOQ
      ↓
Step 1: 判断项目类型
      ↓ (根据 project.project_type 或自动识别)
Step 2: 加载对应历史项目模板
      ↓
Step 3: 遍历模板的 8 分部，逐项对比
      ↓
Step 4: 发现当前项目缺失的常见项
      ↓
Step 5: 检查当前图纸中是否有对应构件的证据
      ↓ (回到当前图纸确认！)
Step 6: 生成漏项报告
      ↓
  ┌───有图纸证据 → 标记为 "建议补充，置信度中"
  ├───无图纸证据 → 标记为 "疑似漏项，待确认"
  └───已存在 → 通过
```

### 5.2 核心实现

```python
class HistoricalCompletenessAgent:
    """历史项目完整性检查 Agent。

    原则：历史数据只用于提示漏项，不自动添加工程量。
    所有建议必须回到当前图纸验证。
    """

    def __init__(self, project_id: int):
        self.project = Project.query.get(project_id)
        self.template = self._load_template(self.project.project_type)
        self.current_items = self._load_current_boq()
        self.rooms = self._load_rooms()

    def check_completeness(self) -> CompletenessReport:
        """执行完整性检查，返回漏项报告。"""
        suggestions = []
        warnings = []

        # 1. 分部分项对比
        for division_id in range(1, 9):
            expected = self.template.get_common_items(division_id)
            current = self._get_current_in_division(division_id)

            for expected_item in expected:
                if not self._item_exists(expected_item, current):
                    # 检查当前图纸是否有证据
                    evidence = self._check_drawing_evidence(expected_item)
                    suggestions.append(MissingItemSuggestion(
                        item=expected_item,
                        division_id=division_id,
                        has_drawing_evidence=evidence.exists,
                        evidence_details=evidence.details,
                        confidence='medium' if evidence.exists else 'low',
                    ))

        # 2. 高频漏项检查
        for missing in self.template.high_freq_missing:
            if not self._item_exists_by_name(missing['name'], self.current_items):
                warnings.append(missing)

        # 3. 合理性指标检查
        reasonableness = self._check_reasonableness()

        return CompletenessReport(
            suggestions=suggestions,
            high_freq_warnings=warnings,
            reasonableness=reasonableness,
        )

    def _check_drawing_evidence(self, item) -> EvidenceResult:
        """在当前项目图纸中搜索构件证据。

        返回：EvidenceResult(exists=bool, details=str)
        """
        # 搜索已识别的图元中是否有相关线索：
        # - 文字中是否有材料名称
        # - 是否有对应类型的几何区域
        # - PDF 文字中是否有提及
        # - 大样图中是否有相关做法
        pass
```

### 5.3 漏项建议的输出格式

```json
{
  "completeness_report": {
    "project_type": "residential",
    "template_matched": "住宅/样板房 (45 个历史样本)",
    "total_suggestions": 5,
    "suggestions": [
      {
        "item": "卫生间-防水层",
        "division": "2-墙柱面工程",
        "confidence": "medium",
        "reason": "85% 的样板房项目包含此项目，当前图纸在卫生间墙面文字中提及'防水'",
        "drawing_evidence": "PDF 页 3 文字块: '墙面防水层上翻300mm'",
        "action": "建议补充到第 2 分部，置信度 🟡 中"
      },
      {
        "item": "门槛石",
        "division": "1-楼地面工程",
        "confidence": "low",
        "reason": "70% 的样板房在卫生间/厨房入口设置门槛石，但当前图纸未明确标注",
        "drawing_evidence": null,
        "action": "疑似漏项，请现场确认或咨询设计师"
      }
    ],
    "high_freq_warnings": [
      {"name": "窗台石", "frequency": 0.22, "recommendation": "检查所有窗洞是否需要窗台石"}
    ],
    "reasonableness": {
      "wall_floor_ratio": {"current": 2.1, "expected_range": [2.0, 3.0], "status": "OK"},
      "light_density": {"current": 0.08, "expected_range": [0.10, 0.20], "status": "LOW", "warning": "灯具密度偏低，可能遗漏"}
    }
  }
}
```

---

## 六、历史清单导入流程

### 6.1 导入流程

```
Excel 文件
    ↓
parse_historical_excel()  [已有，需扩展]
    ↓
标准化特征提取  [新增]
    ↓
历史项目入库:
  ├── historical_projects (汇总)
  └── historical_quantity_items (逐项特征)
    ↓
更新项目类型模板  [新增]
    ↓
返回导入结果
```

### 6.2 支持的导入格式

| 格式 | 支持 | 说明 |
|------|------|------|
| 本系统导出 Excel | ✅ 原生 | 11 列标准格式 |
| 通用 BOQ Excel | ✅ 自动识别 | 灵活列头检测 |
| CSV | 🆕 计划 | Phase 8 添加 |
| JSON API | 🆕 计划 | 供套价系统对接 |

---

## 七、与已有 comparison.py 的关系

| 现有功能 | 升级方向 |
|---------|---------|
| `parse_historical_excel()` | 保留 + 增加标准化特征提取 |
| `compare_with_historical()` | 升级为 `HistoricalCompletenessAgent.check_completeness()` |
| `auto_fill_missing()` | ⚠️ 降低权限：不再直接自动补项，改为生成建议列表待人工确认 |
| `REASONABLENESS_RANGES` | 迁移到 `historical_project_templates` 表中，按项目类型动态更新 |
| 字符串匹配 `_items_match()` | 升级为标准化特征向量相似度计算 |

---

## 八、实施计划

| 阶段 | 任务 | 依赖 |
|------|------|------|
| Phase 7a | 新增 4 张数据库表 + 迁移 | Phase 0 完成 |
| Phase 7b | 标准化特征提取器 | Phase 7a |
| Phase 7c | 项目类型模板初始化 + 自动更新 | Phase 7b |
| Phase 7d | HistoricalCompletenessAgent 实现 | Phase 7c + Phase 3（图纸理解） |
| Phase 7e | 漏项报告 UI 集成 | Phase 7d + Phase 9（审核工作台） |
