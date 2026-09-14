# 多 Agent 工程量计算系统架构设计

> **文档版本**: v1.0
> **日期**: 2026-08-09

---

## 一、架构理念

**核心原则：AI 负责理解，程序负责计算。**

```
┌─────────────────────────────────────────────────┐
│              Orchestrator (Agent 0)              │
│  任务拆解 · 规模判断 · 调度 · 汇总 · 重试       │
└──────────┬──────────────────────────────────────┘
           │
    ┌──────┼──────────────────────────┐
    │      │                          │
    ▼      ▼                          ▼
┌───────┐ ┌──────────┐ ┌──────────────────────────┐
│Agent1 │ │Agent 2   │ │Agent 3: 空间理解          │
│CAD    │ │PDF视觉   │ │房间/区域/墙/门窗/地面/天花│
│解析   │ │语义解析  │ └──────────────────────────┘
└───┬───┘ └────┬─────┘            │
    │          │                   │
    └──────────┼───────────────────┘
               │
               ▼
    ┌──────────────────────┐
    │DrawingUnifiedModel   │  ← 多源融合
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │Agent 4: 图纸关联      │
    │平面↔立面↔天花↔大样   │
    └──────────┬───────────┘
               │
    ┌──────────┴──────────┐
    │                     │
    ▼                     ▼
┌─────────────┐  ┌──────────────────┐
│小项目:      │  │大项目:            │
│直接计算     │  │Orchestrator        │
│             │  │  ├─ Worker A区     │
│             │  │  ├─ Worker B区     │
│             │  │  ├─ Worker C区     │
│             │  │  └─ Worker D区     │
└──────┬──────┘  └────────┬─────────┘
       │                  │
       └────────┬─────────┘
                ▼
    ┌──────────────────────┐
    │QuantityRuleEngine    │  ← 确定性计算
    │（所有 Worker 共用）   │
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │三层审计体系           │
    │1. 几何审计           │
    │2. 图纸交叉审计       │
    │3. 历史项目漏项审计   │
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │QuantityAuditAgent    │  ← 最终AI总审计
    └──────────┬───────────┘
               │
               ▼
         最终BOQ清单
```

---

## 二、Agent 角色定义

### Agent 0: Orchestrator

**职责：不直接处理图纸，只负责调度和编排。**

```python
class Orchestrator:
    """主调度器 — 图纸任务拆解、Worker调度、结果汇总。

    能力：
    - 判断项目规模（小/中/大）
    - 图纸任务拆解
    - 区域划分（大项目）
    - Agent/Worker 调度与并发控制
    - 任务重试与异常恢复
    - 审计调度
    """

    def process_project(self, project: Project) -> TaskResult:
        # Step 1: 规模判断
        scale = self._assess_project_scale(project)
        plan = self._create_execution_plan(project, scale)

        # Step 2: 执行
        if scale == 'small':
            # 小项目：串行，直接计算
            return self._execute_sequential(plan)
        elif scale == 'medium':
            # 中型：图纸级并行
            return self._execute_parallel_by_drawing(plan)
        else:
            # 大型：区域 + 专业并行
            return self._execute_parallel_by_zone(plan)

    def _assess_project_scale(self, project) -> str:
        drawing_count = project.drawings.count()
        if drawing_count <= 3:
            return 'small'
        elif drawing_count <= 15:
            return 'medium'
        else:
            return 'large'
```

**并发控制：**

```python
CONCURRENCY_LIMITS = {
    'small': {'drawing_workers': 1, 'specialty_workers': 1},
    'medium': {'drawing_workers': 4, 'specialty_workers': 2},
    'large': {'drawing_workers': 8, 'specialty_workers': 4},
}

# 全局并发上限
GLOBAL_MAX_CONCURRENT_AGENTS = 12
GLOBAL_TIMEOUT_PER_AGENT = 300  # 秒
GLOBAL_RETRY_LIMIT = 3
```

### Agent 1: CAD 解析 Agent

```python
class CADParsingAgent:
    """Agent 1: DWG/DXF → 结构化几何数据。

    输入: Drawing (DWG/DXF file)
    输出: CADGeometryData

    不调用 LLM — 纯确定性解析。
    """

    def parse(self, drawing: Drawing) -> CADGeometryData:
        parser = CADParser(drawing.file_path)

        return CADGeometryData(
            drawing_id=drawing.id,
            lines=parser.extract_lines(),           # ×1 → LINE
            polylines=parser.extract_polylines(),   # ×2 → LWPOLYLINE (房间边界)
            arcs=parser.extract_arcs(),             # ×3 → ARC
            circles=parser.extract_circles(),       # ×4 → CIRCLE
            hatches=parser.extract_hatches(),       # ×5 → HATCH (填充/材料区域)
            blocks=parser.extract_blocks(),         # ×6 → INSERT (图块/符号)
            dimensions=parser.extract_dimensions(), # ×7 → DIMENSION
            texts=parser.extract_texts(),           # ×8 → TEXT/MTEXT
            layers=parser.extract_layers(),         # ×9 → LAYER
            metadata=parser.extract_metadata(),     # 版本/单位/范围
        )
```

### Agent 2: PDF 视觉语义解析 Agent

```python
class PDFSemanticParsingAgent:
    """Agent 2: PDF/扫描图 → 语义信息。

    输入: Drawing (PDF/image file)
    输出: PDFSemanticData

    可选择性调用 LLM/VLM 进行复杂文字理解。
    """

    def parse(self, drawing: Drawing) -> PDFSemanticData:
        extractor = PDFExtractor(drawing.file_path)

        return PDFSemanticData(
            drawing_id=drawing.id,
            text_blocks=extractor.extract_text_blocks(),     # 文字块+位置
            images=extractor.extract_images(),               # 嵌入图片
            material_codes=extractor.extract_material_codes(), # ST-01, CT-02...
            room_names=extractor.extract_room_names(),       # 房间名称
            legends=extractor.extract_legend_tables(),       # 图例表
            dimensions_marked=extractor.extract_annotated_dimensions(), # 标注尺寸
            design_notes=extractor.extract_design_notes(),   # 设计说明
            title_block=extractor.extract_title_block(),     # 图框信息
        )

    def _llm_assisted_parse(self, image: Image, prompt: str) -> dict:
        """仅对复杂场景使用 LLM：
        - 理解模糊的设计说明文字
        - 判断填充图案对应的材料
        - 识别未知图例符号
        """
        # 调用 LLM/VLM (Claude/GPT-4o)
        pass
```

### Agent 3: 空间理解 Agent

```python
class SpatialUnderstandingAgent:
    """Agent 3: 融合 CAD 几何 + PDF 语义 → 空间构件。

    输入: CADGeometryData + PDFSemanticData (融合后的 DrawingUnifiedModel)
    输出: SpatialModel

    核心任务：
    - 房间/区域识别与命名
    - 墙体识别与长度计算
    - 门窗识别与尺寸
    - 地面/天花区域划分
    - 材料区域边界确定

    AI 角色：
    - 判断不确定的房间边界的归属
    - 理解复杂的空间嵌套关系
    - 纠正 PDF 文字与 CAD 几何的错误匹配
    """

    def understand(self, unified: DrawingUnifiedModel) -> SpatialModel:
        rooms = self._identify_rooms(unified)
        walls = self._identify_walls(unified, rooms)
        doors_windows = self._identify_openings(unified, walls)
        floor_regions = self._identify_floor_regions(unified, rooms)
        ceiling_regions = self._identify_ceiling_regions(unified, rooms)
        cabinets = self._identify_cabinets(unified)

        return SpatialModel(
            rooms=rooms,
            walls=walls,
            doors=doors_windows['doors'],
            windows=doors_windows['windows'],
            floor_regions=floor_regions,
            ceiling_regions=ceiling_regions,
            cabinets=cabinets,
        )
```

### Agent 4: 图纸关联 Agent

```python
class DrawingRelationAgent:
    """Agent 4: 建立平面↔立面↔天花↔大样之间的关联。

    输入: List[DrawingUnifiedModel] (同一项目的全部图纸)
    输出: ComponentRelations

    关联依据：
    1. 相同图纸编号（S-03 → 立面 S-03 → 大样 S-03 Detail）
    2. 相同材料编号（ST-01 同时出现在平面铺装图和材料表）
    3. 相同区域名称
    4. 相同尺寸标注

    AI 角色（重点使用）：
    - 理解图纸编号命名规则
    - 判断跨图纸的同一构件
    - 发现图纸间的矛盾
    """

    def relate(self, drawings: List[DrawingUnifiedModel]) -> ComponentRelations:
        relations = []

        for drawing_a in drawings:
            for drawing_b in drawings:
                if drawing_a.id == drawing_b.id:
                    continue

                # 基于编号的关联
                if self._same_drawing_number(drawing_a, drawing_b):
                    relations.append(self._create_relation(drawing_a, drawing_b))

                # 基于材料编号的关联
                shared_codes = self._shared_material_codes(drawing_a, drawing_b)
                for code in shared_codes:
                    relations.append(self._create_material_relation(
                        drawing_a, drawing_b, code))

                # 基于区域的关联
                shared_rooms = self._shared_rooms(drawing_a, drawing_b)
                for room in shared_rooms:
                    relations.append(self._create_room_relation(
                        drawing_a, drawing_b, room))

        return ComponentRelations(relations=relations)
```

---

## 三、Worker 并行机制

### 3.1 三级并行

```
Level 1: 图纸级并行
  DWG-A ──Worker──→ 解析结果A
  PDF-B ──Worker──→ 解析结果B    } 同时进行
  DXF-C ──Worker──→ 解析结果C

Level 2: 区域级并行（大项目）
  A区 Worker ──→ A区 BOQ
  B区 Worker ──→ B区 BOQ        } 同时进行
  C区 Worker ──→ C区 BOQ

Level 3: 专业任务级并行
  地面 Worker ──→ 第1分部 BOQ
  墙面 Worker ──→ 第2分部 BOQ   } 同时进行
  天棚 Worker ──→ 第3分部 BOQ
  ...
```

### 3.2 Worker 实现

```python
class ZoneWorker:
    """区域级 Worker — 负责一个区域的完整算量流程。

    每个 ZoneWorker 内部：
    1. 使用统一的 QuantityRuleEngine（不自定义规则）
    2. 使用统一的 SourceTracker（记录所有来源）
    3. 输出标准 Measurement 列表
    """

    def __init__(self, zone_id: str, spatial_model: SpatialModel, rule_engine: QuantityRuleEngine):
        self.zone_id = zone_id
        self.spatial = spatial_model
        self.rule_engine = rule_engine  # 共用规则引擎

    def run(self) -> List[Measurement]:
        """执行本区域的算量任务。"""
        measurements = []

        try:
            # 按专业任务依次计算
            for specialty in ['flooring', 'wall', 'ceiling', 'openings', 'baseboard', 'cabinets']:
                task = self._create_task(specialty)
                result = self.rule_engine.calculate(task)
                measurements.extend(result)
        except Exception as e:
            # 一个区域失败不影响其他区域
            logger.error(f"Zone {self.zone_id} failed: {e}", exc_info=True)
            return []  # 返回空列表，等待重试

        return measurements


class SpecialtyWorker:
    """专业级 Worker — 负责一个专业分部的算量。"""

    def __init__(self, division_id: int, rule_engine: QuantityRuleEngine):
        self.division_id = division_id
        self.rule_engine = rule_engine

    def run(self, spatial: SpatialModel) -> DivisionResult:
        """执行单个分部的算量。"""
        rules = self.rule_engine.get_rules(self.division_id)
        return self.rule_engine.apply(spatial, rules)
```

---

## 四、QuantityRuleEngine（独立规则引擎）

```python
class QuantityRuleEngine:
    """工程量计算规则引擎 — 确定性计算，AI 不可修改。

    独立模块，所有 Worker 共用同一实例。
    不支持 Worker 自定义计算规则。
    """

    DIVISION_RULES = {
        1: FlooringRules(),      # 楼地面：m²，按 Polygon 面积
        2: WallRules(),          # 墙柱面：m²，净面积扣门窗洞口
        3: CeilingRules(),       # 天棚：m²，水平投影面积
        4: OpeningRules(),       # 门窗：樘/m²，宽×高×数量
        5: BaseboardRules(),     # 踢脚线：m，周长-门洞-柜体
        6: CabinetRules(),       # 固定柜台：m²/m/个，分类计算
        7: PlumbingRules(),      # 给排水：套/个，末端点统计
        8: ElectricalRules(),    # 电气：台/个，点位统计
    }

    def calculate(self, task: CalculationTask) -> List[Measurement]:
        """执行计算任务，返回 Measurement 列表。"""
        rules = self.DIVISION_RULES[task.division_id]
        return rules.calculate(task.spatial_model, task.parameters)


class FlooringRules:
    """第 1 分部：楼地面工程量计算规则。

    原则：
    - 不同材质 HATCH 区域分别计算
    - 用 Polygon 净面积（不是 HATCH 面积）
    - 与标注尺寸交叉核对，偏差 >5% 以标注为准
    """

    def calculate(self, spatial: SpatialModel, params: dict) -> List[Measurement]:
        results = []
        for region in spatial.floor_regions:
            polygon = region.geometry  # Shapely Polygon
            area = polygon.area       # 精确几何面积

            # 与标注尺寸交叉核对
            annotated_dim = region.annotated_area  # 从 DIMENSION 实体读取
            if annotated_dim and abs(area - annotated_dim) / annotated_dim > 0.05:
                area = annotated_dim  # 以标注为准
                confidence = 'medium'
            else:
                confidence = 'high'

            results.append(Measurement(
                division_id=1,
                label=f"{region.room_name}-{region.material_code}地面",
                area=round(area, 2),
                unit='m²',
                source_tag='图纸实测',
                confidence_level=confidence,
                formula_text=f"{region.room_name} 地面净面积 = {round(area, 2)}m²",
                project_features=f"{region.material_code} {region.material_type}",
                material_code=region.material_code,
            ))
        return results
```

---

## 五、审计 Agent

```python
class QuantityAuditAgent:
    """最终 AI 总审计 Agent。

    在所有 Worker 完成后运行。

    检查项：
    1. 重复计算（同一区域同一材料出现两次）
    2. 漏项（对比历史模板 + 8 分部覆盖度）
    3. 异常尺寸（门 0.3m 宽？房间 0.5m²？）
    4. 异常面积（墙地比异常？）
    5. 材料冲突（同一墙面出现两种矛盾材料？）
    6. 平面/立面冲突（平面标注 ST-01，立面标注 CT-02？）
    7. 大样缺失（柜体无大样编号引用？）
    8. 低置信度集中（>30% 项目为低置信度？）
    9. 历史项目疑似漏项（对比同类型历史 BOQ）

    使用 LLM 进行综合判断，但所有判断基于确定性数据。
    """

    def audit(self, measurements: List[Measurement],
              spatial: SpatialModel,
              relations: ComponentRelations,
              historical_report: CompletenessReport) -> AuditReport:

        findings = []

        # 1. 几何审计（确定性）
        findings.extend(self._geometric_audit(measurements, spatial))

        # 2. 图纸交叉审计（规则 + LLM 辅助）
        findings.extend(self._cross_drawing_audit(measurements, relations))

        # 3. 历史项目审计（规则 + 相似度）
        findings.extend(self._historical_audit(measurements, historical_report))

        # 4. LLM 综合判断（仅用于复杂冲突）
        findings.extend(self._llm_audit(findings, measurements))

        return AuditReport(findings=findings)
```

---

## 六、任务执行流程

### 6.1 完整流程

```python
def process_project_full(project_id: int):
    """完整的项目算量流程 — 由 Orchestrator 编排。"""

    # 0. 初始化
    project = Project.query.get(project_id)
    orchestrator = Orchestrator()
    rule_engine = QuantityRuleEngine()

    # 1. 图纸解析（并行）
    cad_agent = CADParsingAgent()
    pdf_agent = PDFSemanticParsingAgent()

    drawings = project.drawings.all()
    parsed_data = []

    for drawing in drawings:
        if drawing.file_type in ('dwg', 'dxf'):
            parsed_data.append(cad_agent.parse(drawing))
        elif drawing.file_type == 'pdf':
            parsed_data.append(pdf_agent.parse(drawing))
        else:
            # 图片文件：OpenCV + PDF 风格处理
            parsed_data.append(pdf_agent.parse(drawing))

    # 2. CAD + PDF 融合
    unified_models = DrawingUnifiedModel.merge(parsed_data)

    # 3. 图纸类型识别 + 图纸关联
    type_classifier = DrawingTypeClassifier()
    for model in unified_models:
        model.drawing_type = type_classifier.classify(model)

    relation_agent = DrawingRelationAgent()
    relations = relation_agent.relate(unified_models)

    # 4. 空间理解
    spatial_agent = SpatialUnderstandingAgent()
    spatial_models = []
    for model in unified_models:
        spatial_models.append(spatial_agent.understand(model))

    # 5. 算量执行（根据规模选择策略）
    merged_spatial = SpatialModel.merge(spatial_models)

    if orchestrator.scale == 'large':
        # 区域划分 + 并行 Worker
        zones = orchestrator.divide_zones(merged_spatial)
        workers = [ZoneWorker(z, merged_spatial, rule_engine) for z in zones]
        measurements = parallel_execute(workers, limit=8)
    else:
        # 直接计算
        measurements = rule_engine.calculate_all(merged_spatial)

    # 6. 历史项目漏项检查
    hist_agent = HistoricalCompletenessAgent(project_id)
    completeness_report = hist_agent.check_completeness()

    # 7. 三层审计
    audit_agent = QuantityAuditAgent()
    audit_report = audit_agent.audit(
        measurements, merged_spatial, relations, completeness_report
    )

    # 8. 置信度评估
    confidence_engine = ConfidenceEngine()
    for m in measurements:
        m.confidence_score, m.confidence_level, m.confidence_reasons = \
            confidence_engine.evaluate(m, merged_spatial, audit_report)

    # 9. 生成最终 BOQ
    return FinalBOQ(
        project_id=project_id,
        measurements=measurements,
        audit_report=audit_report,
        completeness_report=completeness_report,
    )
```

---

## 七、并发与容错

### 7.1 并发策略

```python
class ConcurrencyManager:
    """全局并发管理器。

    确保：
    - 不超过全局 LLM 调用上限
    - 不超过系统资源上限
    - 一个 Worker 失败不影响其他 Worker
    """

    def __init__(self):
        self.max_workers = 12
        self.active_workers = 0
        self.task_queue = asyncio.Queue()
        self.semaphore = asyncio.Semaphore(self.max_workers)

    async def execute_worker(self, worker: Worker, task: Task) -> Optional[Result]:
        async with self.semaphore:
            try:
                result = await asyncio.wait_for(
                    worker.run(task),
                    timeout=GLOBAL_TIMEOUT_PER_AGENT
                )
                return result
            except asyncio.TimeoutError:
                logger.error(f"Worker {worker.name} timeout on task {task.id}")
                return None
            except Exception as e:
                logger.error(f"Worker {worker.name} failed: {e}", exc_info=True)
                return None

    async def execute_all(self, tasks: List[Tuple[Worker, Task]]) -> List[Result]:
        """并发执行所有任务，部分失败不阻塞整体。"""
        coros = [self.execute_worker(w, t) for w, t in tasks]
        results = await asyncio.gather(*coros, return_exceptions=True)
        return [r for r in results if r is not None and not isinstance(r, Exception)]
```

### 7.2 任务状态追踪

```python
class TaskStatus(Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    RETRYING = 'retrying'
    PARTIAL = 'partial'  # 部分成功（Worker 级容错）

class TaskTracker:
    """任务状态追踪器 — 支持断点续传。"""

    def __init__(self, project_id: int):
        self.project_id = project_id
        self.tasks: Dict[str, TaskStatus] = {}
        self.results: Dict[str, Any] = {}

    def save_state(self):
        """持久化任务状态到数据库。"""
        state = {
            'project_id': self.project_id,
            'tasks': {k: v.value for k, v in self.tasks.items()},
            'completed_at': datetime.utcnow().isoformat(),
        }
        # 存入 quantity_tasks 表

    def resume(self) -> List[str]:
        """恢复中断的任务，返回未完成的任务 ID 列表。"""
        return [tid for tid, status in self.tasks.items()
                if status in (TaskStatus.PENDING, TaskStatus.RETRYING)]
```

---

## 八、AI 调用边界

### 8.1 AI 可以做的

| 任务 | Agent | 原因 |
|------|-------|------|
| 理解图纸编号命名规则 | Agent 4 | 规则多样，需语义理解 |
| 判断图纸类型（平面/立面/天花/大样） | Agent 4 | 需综合判断文字+图形特征 |
| 理解模糊的设计说明 | Agent 2 | 自然语言理解 |
| 识别未知图例符号 | Agent 2 | 视觉理解 |
| 判断房间功能（结合文字+面积+位置） | Agent 3 | 多因素综合判断 |
| 图纸间构件关联 | Agent 4 | 需要语义匹配 |
| 综合审计（异常发现） | AuditAgent | 需要全局视角 |
| 低置信度项的人工审核建议 | AuditAgent | 需要解释和推理 |

### 8.2 AI 不能做的

| 任务 | 为什么 | 取代方案 |
|------|--------|---------|
| 计算面积 | 几何计算，必须精确 | Shapely Polygon.area |
| 计算周长 | 几何计算 | Shapely Polygon.length |
| 计算墙体长度 | 累加线段 | CAD LINE 长度累加 |
| 扣除门窗洞口 | 简单减法 | 规则引擎 |
| 材料编码提取 | 正则匹配更精确 | 正则 `[A-Z]{2}-\d{2}` |
| 图纸比例尺换算 | 简单数学 | 尺寸标注比对 |
| 填充面积计算 | 偏差大，不准 | CAD HATCH 边界 Polygon 面积 |

### 8.3 AI 调用成本控制

```python
class AICallBudget:
    """AI 调用预算控制器。

    原则：
    - 优先使用确定性算法
    - 仅在确定性方法无法解决时调用 LLM
    - 批量调用，减少 API 往返
    - 缓存相同类型的查询结果
    """

    def __init__(self):
        self.max_calls_per_project = 50  # 每个项目最多 50 次 LLM 调用
        self.calls_made = 0
        self.cache = {}

    def should_call_llm(self, task_type: str) -> bool:
        if self.calls_made >= self.max_calls_per_project:
            return False
        if task_type in LLM_EXEMPT_TASKS:  # 几何计算等
            return False
        return True

    def call_llm(self, prompt: str, task_type: str) -> Optional[str]:
        if not self.should_call_llm(task_type):
            return None
        if prompt in self.cache:
            return self.cache[prompt]

        self.calls_made += 1
        result = llm_client.chat(prompt)
        self.cache[prompt] = result
        return result
```

---

## 九、与现有代码的关系

| 现有模块 | 对应新架构 |
|---------|-----------|
| `processing.py:_detect_rooms()` | CADParsingAgent + SpatialUnderstandingAgent |
| `processing.py:_detect_doors_and_windows()` | CADParsingAgent (CAD 数据) + OpeningRules |
| `processing.py:_generate_derived_measurements()` | QuantityRuleEngine (第 2/3/5/6 分部) |
| `processing.py:_generate_hidden_items()` | HistoricalCompletenessAgent + QuantityRuleEngine (第 7/8 分部) |
| `comparison.py:compare_with_historical()` | HistoricalCompletenessAgent |
| `comparison.py:auto_fill_missing()` | HistoricalCompletenessAgent (降级为建议，不自动补) |
| `api.py:_check_reasonableness()` | 迁移到 QuantityRuleEngine + 模板合理性指标 |
| `api.py:_generate_boq_summary()` | 保留在 API 层 |
