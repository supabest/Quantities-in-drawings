# 里程碑 A：几何主源引擎 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让干净 DXF 上传后经 HTTP 全链路落库，清单页读到 227 的地面 163.75 / 天棚 162.15 / 墙面 437.22 / 合计 763.12 m²、踢脚 166.73 m、洞口 42 樘，且重复计量不叠加。

**Architecture:** 新建纯函数包 `app/core/takeoff/`（不 import flask/db/modeling），把 `modeling/base_extract` 已验证的围合算法参数化搬入 `enclosure.py`，`modeling/base_extract.build()` 降为薄包装（保持离线链路与封账数值不变）。`app/processing.py` 新增 Phase 1.2 调用 `run()`，落库由 `rows.py` 独占。

**Tech Stack:** Python 3.12 / Flask / SQLAlchemy / Alembic / ezdxf 1.4.x / shapely / OpenCV / pytest

**Spec:** `docs/superpowers/specs/2026-10-17-general-takeoff-engine-design.md`

## Global Constraints

- 几何是唯一数值来源；BOQ 只作开发期校验，禁止进入生产代码取值路径。
- 每行必须带 `formula_text`（可独立复算的算式）与溯源（房间+方位+图号、`layer=`/`comp_id=`/`page=`）。
- 面积按 `comp_id` 去重后累加；同一物理房间不得因多个锚点重复计数。
- 无法判定即降级为 issue / 不出行，**不猜、不造数**；空壳「待确认」行不得进入合计。
- `app/core/takeoff/` 不得 import `flask`、`app`、`app.models`、`modeling`；唯一允许 import db 的是 `rows.py`。
- 227 封账数值只存在于测试与 `tests/golden/`，不得写进产品代码常量。
- 测试运行方式：`./venv/bin/python -m pytest`；本地 Web 用 `./venv/bin/python -m flask --app run.py run --port 5001`（5000 被 AirPlay 占用）。
- PG 本地走 `127.0.0.1:5433`；**绝不设置 `TEST_DATABASE_URL`**（测试默认 SQLite）。
- 不提交 `outputs/*.xlsx`、`outputs/model/mep_takeoff_m5.md`、`scripts/build_mep_workbook.py`、`tests/test_mep_module.py` 之外的 MEP 未完成工作；不 stage 未跟踪的 `_*.py` 与 `outputs/preview/**`。

## 计划外发现（实现前已取证，直接影响设计）

1. **垭口分界不能自动推导。** 227 的 `SEALS`（卧室一北 2800 垭口）是手工补的一条分界线；去掉它，卧室一整个房间漏到图外（实测：户内合计 154.56 m²，卧室一消失）。而「共线墙端 gap ∈ 1.2~3.0 m」规则在 227 上产生 186 个候选（含大量平行双线误配），不可用。→ **seals 作为显式项目参数**（`takeoff_params['seals']`）传入，并在行内披露；图纸缺分界线时由 `enclosure_leak` issue 提示补线，不静默丢房间。
2. **`$TD_AUDIT_GENERATED_*` 不能无条件合并。** 227 平面图有 ~190 个审计层，现有封账值是在**未合并**它们的前提下算出的。→ 角色层零命中时才回退使用审计层，并记 `audit_layer_fallback` issue；否则只计数披露，不参与几何。
3. 227 的 72 行含第 6 分部固装 20 行（离线 `modeling/furniture_takeoff.py`），本轮 Web 不接固装；Web 过关线只判六值 + 行数不翻倍。

---

## 文件结构

```
app/core/takeoff/
  __init__.py          # 只导出 run / TakeoffParams / TakeoffResult，无副作用
  types.py             # Row / Issue / Coverage / TakeoffParams / TakeoffResult（纯 dataclass）
  layers.py            # proxy_survey 收图探针 + 角色判定 + 审计层回退
  frames.py            # 图框/布局/VIEWPORT 切簇 + 主簇打分
  enclosure.py         # 房间围合（栅格法，搬自 modeling/base_extract）+ seals 注入 + 泄漏检测
  takeoff.py           # 1/2/3/4/5 分部行 + 计算式 + 天棚列项简化
  reconcile.py         # 只写状态不产数（口径三档 + 两条判定带）
  coverage.py          # 空间×部位覆盖矩阵
  rows.py              # Row → Measurement（唯一 import db 的位置）
tests/
  test_takeoff_contracts.py     # T2 types
  test_takeoff_layers.py        # T3/T4
  test_takeoff_frames.py        # T5
  test_takeoff_enclosure.py     # T6
  test_takeoff_rows.py          # T7/T8/T9
  test_takeoff_web.py           # T10/T11（Tier A+ 落库断言）
  test_takeoff_golden.py        # 修改：Tier A 改锚 Web 行
  fixtures/minimal_flat.dxf     # T12 生成产物（入库）
scripts/make_minimal_dxf.py     # T12 合成小样生成器
modeling/base_extract.py        # 修改：build(dxf=None, ...) 委托 enclosure
app/models.py                   # 修改：3 个新列
migrations/versions/b7f3c1d92a4e_phase_a_geometry_columns.py
app/processing.py               # 修改：Phase 1.2、清旧行、#4、#5
app/api.py                      # 修改：层高闸门（mall/hotel）
```

---

### Task 1: 数据库三列迁移 + 业态值映射

**Files:**
- Modify: `app/models.py:139-160`（Drawing）、`:110-137`（Project）、`:160-215`（Measurement）
- Create: `migrations/versions/b7f3c1d92a4e_phase_a_geometry_columns.py`
- Test: `tests/test_takeoff_contracts.py`

**Interfaces:**
- Produces: `Drawing.is_base`（Boolean, nullable）、`Project.takeoff_params`（Text, nullable，存 JSON）、`Measurement.manual_pinned`（Boolean, nullable）。后续任务按这 3 个名字读写。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_takeoff_contracts.py
import sqlalchemy as sa
from app import create_app, db


def test_phase_a_columns_exist(tmp_path):
    class C:
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/t.db"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        SECRET_KEY = "t"
        UPLOAD_FOLDER = str(tmp_path / "up")
        REPORTS_FOLDER = str(tmp_path / "rp")
        WTF_CSRF_ENABLED = False

    app = create_app(C)
    with app.app_context():
        db.create_all()
        insp = sa.inspect(db.engine)
        assert "is_base" in {c["name"] for c in insp.get_columns("drawings")}
        assert "takeoff_params" in {c["name"] for c in insp.get_columns("projects")}
        assert "manual_pinned" in {c["name"] for c in insp.get_columns("measurements")}
        cols = {c["name"]: c for c in insp.get_columns("measurements")}
        assert cols["manual_pinned"]["nullable"] is True
```

- [ ] **Step 2: 运行确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_contracts.py::test_phase_a_columns_exist -v`
Expected: FAIL（`AssertionError`，列不存在）

- [ ] **Step 3: 加模型列**

`app/models.py` 三处各加一行（放在各自注释分组末尾）：

```python
# Drawing 内（file_size 之后）
    is_base = db.Column(db.Boolean, nullable=True)   # NULL=自动判簇；显式值=人工钉定稿底图

# Project 内（default_floor_height 之后）
    takeoff_params = db.Column(db.Text, nullable=True)  # JSON：seals/参数假设等

# Measurement 内（confidence 之后）
    manual_pinned = db.Column(db.Boolean, nullable=True)  # True=人工钉定行，重算整行冻结
```

- [ ] **Step 4: 写迁移**

```python
# migrations/versions/b7f3c1d92a4e_phase_a_geometry_columns.py
"""phase A geometry columns

Revision ID: b7f3c1d92a4e
Revises: 5679c20a2fdc
"""
from alembic import op
import sqlalchemy as sa

revision = 'b7f3c1d92a4e'
down_revision = '5679c20a2fdc'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('drawings', sa.Column('is_base', sa.Boolean(), nullable=True))
    op.add_column('projects', sa.Column('takeoff_params', sa.Text(), nullable=True))
    op.add_column('measurements', sa.Column('manual_pinned', sa.Boolean(), nullable=True))
    op.execute("UPDATE projects SET project_type='sample_flat' WHERE project_type='residential'")
    op.execute("UPDATE projects SET project_type='office' WHERE project_type='commercial'")


def downgrade():
    op.drop_column('measurements', 'manual_pinned')
    op.drop_column('projects', 'takeoff_params')
    op.drop_column('drawings', 'is_base')
```

- [ ] **Step 5: 跑测试 + 本地迁移**

Run: `./venv/bin/python -m pytest tests/test_takeoff_contracts.py -v` → PASS
Run: `./venv/bin/alembic -c migrations/alembic.ini upgrade head`（PG 5433；若本地无库可跳过，Step 6 只跑 SQLite 测试）

- [ ] **Step 6: 提交**

```bash
git add app/models.py migrations/versions/b7f3c1d92a4e_phase_a_geometry_columns.py tests/test_takeoff_contracts.py
git commit -m "feat(takeoff): 三处 nullable 新列（is_base/takeoff_params/manual_pinned）+ 业态值映射"
```

---

### Task 2: 纯数据契约 types.py

**Files:**
- Create: `app/core/takeoff/__init__.py`, `app/core/takeoff/types.py`
- Test: `tests/test_takeoff_contracts.py`

**Interfaces:**
- Produces:
  - `TakeoffParams(project_type: str='sample_flat', floor_height_mm: float|None=None, door_height_mm=2100.0, window_height_mm=1500.0, window_sill_mm=0.0, slab_mm: float|None=None, floor_finish_mm: float|None=None, seals: list[tuple[float,float,float,float]]=[])`
  - `Row(division_id:int, label:str, category:str, unit:str, value:float|None, formula_text:str, position_desc:str='', remarks:str='', source_tag:str='图纸实测', confidence_level:str='high', material_code:str|None=None, project_features:str='', check:dict|None=None)`
  - `Issue(code:str, level:str, message:str, detail:dict|None=None)`
  - `Coverage.mark(space:str, part:str, status:str, source:str|None=None)`，`Coverage.rows() -> list[dict]`
  - `TakeoffResult(rows:list[Row], coverage:Coverage, issues:list[Issue])`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_takeoff_contracts.py
from app.core.takeoff.types import Coverage, Row, TakeoffParams


def test_row_unit_mapping_is_declared():
    r = Row(division_id=1, label='客厅-地面', category='dxf_floor',
            unit='m2', value=12.0, formula_text='4.0×3.0=12.00')
    assert r.check is None or isinstance(r.check, dict)
    assert r.unit == 'm2'


def test_requires_floor_height_only_for_mall_hotel_and_unknown():
    assert TakeoffParams(project_type='mall').requires_floor_height() is True
    assert TakeoffParams(project_type='hotel').requires_floor_height() is True
    assert TakeoffParams(project_type='lobby').requires_floor_height() is True   # 未知→同待遇
    assert TakeoffParams(project_type='sample_flat').requires_floor_height() is False
    assert TakeoffParams(project_type='office').requires_floor_height() is False
    assert TakeoffParams(project_type='showroom').requires_floor_height() is False


def test_coverage_mark_and_rows():
    c = Coverage()
    c.mark('客厅', '地面', '有源', 'dxf')
    c.mark('客厅', '墙面', '待参数')
    rows = c.rows()
    assert {'space': '客厅', 'part': '墙面', 'status': '待参数', 'source': None} in rows
```

- [ ] **Step 2: 运行确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_contracts.py -v`
Expected: FAIL（ModuleNotFoundError: app.core.takeoff）

- [ ] **Step 3: 实现**

```python
# app/core/takeoff/types.py
from dataclasses import dataclass, field

DEFAULT_FLOOR_HEIGHT_MM = {'sample_flat': 2800.0, 'office': 3000.0, 'showroom': 4000.0}
NO_DEFAULT_TYPES = frozenset({'mall', 'hotel'})


@dataclass
class TakeoffParams:
    project_type: str = 'sample_flat'
    floor_height_mm: float | None = None
    door_height_mm: float = 2100.0
    window_height_mm: float = 1500.0
    window_sill_mm: float = 0.0
    slab_mm: float | None = None
    floor_finish_mm: float | None = None
    seals: list = field(default_factory=list)
    scale_count: int | None = None

    def requires_floor_height(self) -> bool:
        if self.floor_height_mm:
            return False
        return self.project_type not in DEFAULT_FLOOR_HEIGHT_MM

    def effective_floor_height_mm(self) -> float | None:
        return self.floor_height_mm or DEFAULT_FLOOR_HEIGHT_MM.get(self.project_type)

    def height_source(self) -> str:
        return '用户填写' if self.floor_height_mm else '业态默认'


@dataclass
class Row:
    division_id: int
    label: str
    category: str
    unit: str
    value: float | None
    formula_text: str
    position_desc: str = ''
    remarks: str = ''
    source_tag: str = '图纸实测'
    confidence_level: str = 'high'
    material_code: str | None = None
    project_features: str = ''
    check: dict | None = None


@dataclass
class Issue:
    code: str
    level: str          # info / warn / error
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class Coverage:
    cells: dict = field(default_factory=dict)

    def mark(self, space: str, part: str, status: str, source: str | None = None):
        self.cells[(space, part)] = {'status': status, 'source': source}

    def rows(self) -> list:
        return [{'space': s, 'part': p, **v} for (s, p), v in sorted(self.cells.items())]


@dataclass
class TakeoffResult:
    rows: list
    coverage: Coverage
    issues: list
```

```python
# app/core/takeoff/__init__.py
from .types import Coverage, Issue, Row, TakeoffParams, TakeoffResult  # noqa: F401
```

- [ ] **Step 4: 跑测试**

Run: `./venv/bin/python -m pytest tests/test_takeoff_contracts.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add app/core/takeoff/__init__.py app/core/takeoff/types.py tests/test_takeoff_contracts.py
git commit -m "feat(takeoff): 纯数据契约 types（Row/Issue/Coverage/参数，含层高闸门判定）"
```

---

### Task 3: 收图探针 proxy_survey + 角色层分级降级

**Files:**
- Create: `app/core/takeoff/layers.py`
- Test: `tests/test_takeoff_layers.py`

**Interfaces:**
- Produces:
  - `SurveyReport(dxf_version, codepage, entity_counts, proxies_by_layer, block_prefix_counts, viewport_count, layouts, text_heights)`
  - `proxy_survey(doc) -> SurveyReport`
  - `ROLE_LAYER_PATTERNS: dict[str, re.Pattern]`（role → 正则；role ∈ wall_solid/wall_fin/door/window/curb/room_label）
  - `role_of(layer_name) -> str|None`
  - `affected_divisions(broken_roles: set[str]) -> set[int]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_takeoff_layers.py
import ezdxf
from app.core.takeoff.layers import (SurveyReport, affected_divisions, proxy_survey,
                                     role_of)


def _doc(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("WALL"); doc.layers.add("P-TEXT"); doc.layers.add("DT")
    msp = doc.modelspace()
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "WALL"})
    msp.add_text("主卧", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((10, 10))
    p = tmp_path / "a.dxf"; doc.saveas(p); return ezdxf.readfile(p)


def test_proxy_survey_counts_and_versions(tmp_path):
    rep = proxy_survey(_doc(tmp_path))
    assert isinstance(rep, SurveyReport)
    assert rep.dxf_version.startswith("AC")
    assert rep.codepage
    assert rep.entity_counts["LINE"] >= 1
    assert rep.proxies_by_layer == {}


def test_role_of_matches_227_layer_names():
    assert role_of("WALL") == "wall_solid"
    assert role_of("S-WALL") == "wall_solid"
    assert role_of("FIN") == "wall_fin"
    assert role_of("DOOR") == "door"
    assert role_of("door_plan") == "door"
    assert role_of("WINDOW") == "window"
    assert role_of("CURB") == "curb"
    assert role_of("P-TEXT") == "room_label"
    assert role_of("DT") is None


def test_affected_divisions_by_role():
    assert affected_divisions({"wall_solid"}) == {1, 2, 3, 5}
    assert affected_divisions({"door", "window"}) == {2, 4}
    assert affected_divisions({"curb"}) == {1, 3}
    assert affected_divisions({"room_label"}) == set()
    assert affected_divisions(set()) == set()
```

- [ ] **Step 2: 运行确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_layers.py -v`
Expected: FAIL（ModuleNotFoundError / ImportError）

- [ ] **Step 3: 实现（探针 + 角色 + 降级映射）**

```python
# app/core/takeoff/layers.py
"""收图探针与图层角色判定。不改几何，只产出状态。"""
import re
from collections import Counter
from dataclasses import dataclass, field

import ezdxf

ROLE_LAYER_PATTERNS = {
    "wall_solid": re.compile(r"^(s[-_ ]?wall|wall|墙体|剪力墙)$", re.I),
    "wall_fin": re.compile(r"^(fin|饰面|造型|finish)$", re.I),
    "door": re.compile(r"(^door|门)", re.I),
    "window": re.compile(r"(window|窗)", re.I),
    "curb": re.compile(r"(curb|反坎|栏板|rail)", re.I),
    "room_label": re.compile(r"^(p[-_ ]?text|text|文字|房名|room[_-]?tag)$", re.I),
}
ROLE_ORDER = ["wall_solid", "wall_fin", "door", "window", "curb", "room_label"]
AUDIT_PREFIX = "$TD_AUDIT_GENERATED_"


def role_of(layer_name: str) -> str | None:
    for role in ROLE_ORDER:
        if ROLE_LAYER_PATTERNS[role].search(layer_name or ""):
            return role
    return None


def affected_divisions(broken_roles: set) -> set:
    divs = set()
    if "wall_solid" in broken_roles:
        divs |= {1, 2, 3, 5}
    if broken_roles & {"door", "window"}:
        divs |= {2, 4}
    if "curb" in broken_roles:
        divs |= {1, 3}
    return divs


@dataclass
class SurveyReport:
    dxf_version: str = ""
    codepage: str = ""
    entity_counts: dict = field(default_factory=dict)
    proxies_by_layer: dict = field(default_factory=dict)
    block_prefix_counts: dict = field(default_factory=dict)
    viewport_count: int = 0
    layouts: list = field(default_factory=list)
    text_heights: list = field(default_factory=list)


def _block_prefix(name: str) -> str:
    name = name or ""
    if name.startswith("*U"):
        return "*U"
    if name.startswith("A$C"):
        return "A$C"
    if name.startswith("$TwtSys$"):
        return "$TwtSys$"
    if name.startswith("$equip_U$"):
        return "$equip_U$"
    return "其他"


def proxy_survey(doc) -> SurveyReport:
    rep = SurveyReport(dxf_version=doc.dxfversion or "",
                       codepage=doc.header.get("$DWGCODEPAGE", ""))
    counts = Counter()
    proxies = Counter()
    prefixes = Counter()
    heights = []

    def walk(entities):
        for e in entities:
            t = e.dxftype()
            counts[t] += 1
            if t == "ACAD_PROXY_ENTITY":
                proxies[e.dxf.layer] += 1
            elif t == "INSERT":
                prefixes[_block_prefix(e.dxf.name)] += 1
            elif t in ("TEXT", "MTEXT"):
                try:
                    heights.append(float(e.dxf.height))
                except Exception:
                    pass

    walk(doc.modelspace())
    for layout in doc.layouts:
        rep.layouts.append(layout.name)
    for layout in doc.layouts:
        for e in layout:
            if e.dxftype() == "VIEWPORT":
                rep.viewport_count += 1
    for name in (doc.blocks.block_names() if hasattr(doc.blocks, "block_names")
                 else [b.name for b in doc.blocks]):
        prefixes[_block_prefix(name)] += 1

    rep.entity_counts = dict(counts)
    rep.proxies_by_layer = dict(proxies)
    rep.block_prefix_counts = dict(prefixes)
    rep.text_heights = heights
    return rep
```

- [ ] **Step 4: 跑测试**

Run: `./venv/bin/python -m pytest tests/test_takeoff_layers.py -v` → PASS

- [ ] **Step 5: 本机对 227 真图跑一次探针（不进 CI）**

```bash
./venv/bin/python -c "
import ezdxf, json
from app.core.takeoff.layers import proxy_survey, role_of
d=ezdxf.readfile('装修图纸/227户型dxf/05.227户型平面图 （施工图纸） rev-33.dxf')
r=proxy_survey(d); print(r.dxf_version, r.codepage, r.proxies_by_layer, r.viewport_count)
print({k:v for k,v in r.block_prefix_counts.items() if k!='其他'})
"
```
Expected: `AC1032 ANSI_936 {} 18`（代理 0；VIEWPORT 18 与规格书 §1.4 表一致）

- [ ] **Step 6: 提交**

```bash
git add app/core/takeoff/layers.py tests/test_takeoff_layers.py
git commit -m "feat(takeoff): 收图探针 proxy_survey + 角色层正则 + 角色→受影响分部映射"
```

---

### Task 4: 图层角色取线（真墙/双线间墙/门窗/反坎/标签 + 审计层回退）

**Files:**
- Modify: `app/core/takeoff/layers.py`
- Test: `tests/test_takeoff_layers.py`

**Interfaces:**
- Produces:
  - `LayerRoles(segments: dict[str, list], labels: list[tuple[float,float,str]], used_layers: dict[str,list[str]], audit_fallback: list[str], bounds: tuple)`
  - `resolve_roles(doc, strict_enc=False) -> LayerRoles`
  - `segments` 的键与 `role_of` 一致；`wall_fin` 已拆为 `wall_fin_part`（真隔墙候选，100~250mm 平行双线）与 `wall_fin_axial`。

- [ ] **Step 1: 写失败测试（合成双线墙 + 中文标签 + 审计层回退）**

```python
# 追加到 tests/test_takeoff_layers.py
from app.core.takeoff.layers import resolve_roles


def _two_rooms(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    for n in ("WALL", "S-WALL", "FIN", "DOOR", "WINDOW", "CURB", "P-TEXT"):
        doc.layers.add(n)
    msp = doc.modelspace()
    # 200mm 双线墙围合 4.0×3.0 内空
    for y in (0, 200):
        msp.add_line((0, y), (4400, y), dxfattribs={"layer": "WALL"})
    for y in (3200, 3400):
        msp.add_line((0, y), (4400, y), dxfattribs={"layer": "WALL"})
    for x in (0, 200):
        msp.add_line((x, 0), (x, 3400), dxfattribs={"layer": "WALL"})
    for x in (4200, 4400):
        msp.add_line((x, 0), (x, 3400), dxfattribs={"layer": "WALL"})
    # FIN 层上的一对 200mm 平行线 → 真隔墙候选
    msp.add_line((2000, 200), (2000, 3200), dxfattribs={"layer": "FIN"})
    msp.add_line((2200, 200), (2200, 3200), dxfattribs={"layer": "FIN"})
    # 门/窗/反坎
    msp.add_line((600, 0), (1500, 0), dxfattribs={"layer": "DOOR"})
    msp.add_line((3000, 3400), (3900, 3400), dxfattribs={"layer": "WINDOW"})
    msp.add_line((400, 0), (4400, 0), dxfattribs={"layer": "CURB"})
    msp.add_text("客厅", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((1000, 1500))
    p = tmp_path / "two.dxf"; doc.saveas(p); return ezdxf.readfile(p)


def test_resolve_roles_segments_and_bounds(tmp_path):
    roles = resolve_roles(_two_rooms(tmp_path))
    assert len(roles.segments["wall_solid"]) == 8
    assert len(roles.segments["wall_fin_part"]) == 2
    assert len(roles.segments["wall_fin_axial"]) == 2
    assert len(roles.segments["door"]) == 1
    assert len(roles.segments["window"]) == 1
    assert len(roles.segments["curb"]) == 1
    assert roles.labels and roles.labels[0][2] == "客厅"
    x0, y0, x1, y1 = roles.bounds
    assert x0 <= 0 and x1 >= 4400 and y1 >= 3400


def test_resolve_roles_splits_fin_double_line():
    """间距 300mm 的平行线不是间墙（超出 100~250 规则）。"""
    doc = ezdxf.new("R2018")
    doc.layers.add("WALL"); doc.layers.add("FIN")
    msp = doc.modelspace()
    msp.add_line((0, 0), (0, 3000), dxfattribs={"layer": "FIN"})
    msp.add_line((300, 0), (300, 3000), dxfattribs={"layer": "FIN"})
    roles = resolve_roles(doc)
    assert roles.segments["wall_fin_part"] == []
```

- [ ] **Step 2: 运行确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_layers.py -v`
Expected: FAIL（`ImportError: resolve_roles`）

- [ ] **Step 3: 实现（搬 `modeling/base_extract.py:80-190` 的取线与 FIN 判据）**

```python
# 追加到 app/core/takeoff/layers.py
import math
from dataclasses import dataclass

TRUE_LAYERS = {"WALL", "S-WALL"}
CLOSE_LAYERS = {"WINDOW", "DOOR", "door_plan"}
DECODE_RE = re.compile(r"\\M\+5([0-9A-Fa-f]{4})")


def decode_text(s: str) -> str:
    return DECODE_RE.sub(lambda m: bytes.fromhex(m.group(1)).decode("gbk", "replace"), s or "")


def segs_of(e):
    st = e.dxftype()
    out = []
    if st == "LINE":
        s, t = e.dxf.start, e.dxf.end
        out.append((s.x, s.y, t.x, t.y))
    elif st in ("LWPOLYLINE", "POLYLINE"):
        try:
            pts = [(p[0], p[1]) for p in e.get_points()]
            for a, b in zip(pts, pts[1:]):
                out.append((a[0], a[1], b[0], b[1]))
        except Exception:
            pass
    return out


def text_of(e):
    if e.dxftype() == "TEXT":
        return e.dxf.text
    if e.dxftype() == "MTEXT":
        try:
            return e.plain_text()
        except Exception:
            return e.text
    return None


def _ang(dx, dy):
    return math.degrees(math.atan2(dy, dx)) % 180


@dataclass
class LayerRoles:
    segments: dict
    labels: list
    used_layers: dict
    audit_fallback: list
    bounds: tuple


def _collect(msp, roles_by_layer, audit_layers):
    byL = {}
    skipped_audit = []

    def rec(ents, depth):
        for e in ents:
            st = e.dxftype()
            if st in ("LINE", "LWPOLYLINE", "POLYLINE"):
                L = e.dxf.layer
                role = roles_by_layer.get(L)
                if role:
                    byL.setdefault(role, []).extend(segs_of(e))
                elif L.startswith(AUDIT_PREFIX):
                    skipped_audit.append(L)
            elif st == "INSERT" and depth < 8:
                try:
                    rec(list(e.virtual_entities()), depth + 1)
                except Exception:
                    pass

    rec(msp, 0)
    return byL, skipped_audit


def resolve_roles(doc, strict_enc=False) -> LayerRoles:
    msp = doc.modelspace()
    roles_by_layer = {}
    used = {r: [] for r in ROLE_ORDER}
    for layer in doc.layers:
        name = layer.dxf.name
        role = role_of(name)
        if role:
            roles_by_layer[name] = role
            used[role].append(name)
    byL, audit = _collect(msp, roles_by_layer, None)

    # 真墙层零命中 → 回退到审计层（只披露，不猜角色；按 227 实测：审计层不参与正常算量）
    fallback = []
    if not byL.get("wall_solid"):
        fallback = sorted(set(audit))
        for L in fallback:
            for e in msp.query(f'LINE[layer=="{L}"]'):
                byL.setdefault("wall_solid", []).extend(segs_of(e))

    fin = byL.get("wall_fin", [])
    # FIN 方向判定（搬 base_extract.py:140-158）
    tdir = set()
    for a, b, c, d in byL.get("wall_solid", []):
        if (c - a) or (d - b):
            tdir.add(round(_ang(c - a, d - b)))
    prim = []
    for a in sorted(tdir):
        if not any(abs(a - x) <= 5 or abs(a - x) >= 175 for x in prim):
            prim.append(a)

    def qualifying(ax, ay, bx, by):
        if not ((bx - ax) or (by - ay)):
            return False
        fa = _ang(bx - ax, by - ay)
        for p in prim:
            d = min(abs(fa - p) % 180, 180 - (abs(fa - p) % 180))
            d2 = min(abs(fa - (p + 90)) % 180, 180 - (abs(fa - (p + 90)) % 180))
            if d <= 5 or d2 <= 5:
                return True
        return False

    fin_axial = [s for s in fin if qualifying(*s)]

    def horizontal(s):
        return abs(s[3] - s[1]) <= abs(s[2] - s[0])

    FH = [s for s in fin_axial if horizontal(s)]
    FV = [s for s in fin_axial if not horizontal(s)]

    def partner(s, group, horiz):
        if horiz:
            a0, a1 = sorted((s[0], s[2])); fx = (s[1] + s[3]) / 2.0
        else:
            a0, a1 = sorted((s[1], s[3])); fx = (s[0] + s[2]) / 2.0
        for t in group:
            if t is s:
                continue
            if horiz:
                tf = (t[1] + t[3]) / 2.0; b0, b1 = sorted((t[0], t[2]))
            else:
                tf = (t[0] + t[2]) / 2.0; b0, b1 = sorted((t[1], t[3]))
            off = abs(tf - fx)
            if 100 <= off <= 250:
                ov = min(a1, b1) - max(a0, b0)
                ln = min(a1 - a0, b1 - b0)
                if ln > 0 and ov >= max(300.0, 0.5 * ln):
                    return True
        return False

    fin_part = [s for s in FH if partner(s, FH, True)] + [s for s in FV if partner(s, FV, False)]

    labels = []
    def addtxt(e):
        if e.dxftype() not in ("TEXT", "MTEXT"):
            return
        role = role_of(e.dxf.layer)
        if role != "room_label":
            return
        t = decode_text((text_of(e) or "").strip())
        if not t or re.match(r"^[\d.]+\s*s\.m\.?$", t) or t in ("上", "下"):
            return
        p = e.dxf.insert
        labels.append((p.x, p.y, t))

    for e in msp:
        if e.dxftype() == "INSERT":
            try:
                for s in e.virtual_entities():
                    addtxt(s)
            except Exception:
                pass
        else:
            addtxt(e)

    pts = [p for segs in byL.values() for s in segs for p in ((s[0], s[1]), (s[2], s[3]))]
    xs = [p[0] for p in pts] or [0.0]
    ys = [p[1] for p in pts] or [0.0]
    pad = 200.0
    bounds = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

    segments = {
        "wall_solid": byL.get("wall_solid", []),
        "wall_fin_axial": fin_axial,
        "wall_fin_part": fin_part,
        "door": byL.get("door", []) + ([s for s in byL.get("door_plan", [])]),
        "window": byL.get("window", []),
        "curb": byL.get("curb", []),
    }
    return LayerRoles(segments=segments, labels=labels, used_layers=used,
                      audit_fallback=fallback, bounds=bounds)
```

> 注：`door_plan` 若层名命中 `door` 正则，已归到 `door`，上面列表推导不会重复（同一层不会两处出现）。若后续发现重复，按 `(x0,y0,x1,y1)` 元组去重。

- [ ] **Step 4: 跑测试**

Run: `./venv/bin/python -m pytest tests/test_takeoff_layers.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add app/core/takeoff/layers.py tests/test_takeoff_layers.py
git commit -m "feat(takeoff): 角色取线（真墙/FIN 双线间墙/门窗/反坎/标签）+ 审计层零命中回退"
```

---

### Task 5: 切簇与定稿底图

**Files:**
- Create: `app/core/takeoff/frames.py`
- Test: `tests/test_takeoff_frames.py`

**Interfaces:**
- Produces:
  - `PlanCluster(source: str, bounds: tuple, score: int, reasons: list[str], drawings: list[str])`
  - `cluster(doc, roles) -> list[PlanCluster]`（paper space 优先；模型空间按连通分量切）
  - `pick_base(clusters, pinned: bool|None) -> tuple[PlanCluster|None, list[Issue]]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_takeoff_frames.py
import ezdxf
from app.core.takeoff.frames import cluster, pick_base
from app.core.takeoff.layers import resolve_roles


def _doc(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("WALL"); doc.layers.add("P-TEXT"); doc.layers.add("DOOR")
    msp = doc.modelspace()
    for y in (0, 200):
        msp.add_line((0, y), (4400, y), dxfattribs={"layer": "WALL"})
    for y in (3200, 3400):
        msp.add_line((0, y), (4400, y), dxfattribs={"layer": "WALL"})
    for x in (0, 200, 4200, 4400):
        msp.add_line((x, 0), (x, 3400), dxfattribs={"layer": "WALL"})
    msp.add_line((600, 0), (1500, 0), dxfattribs={"layer": "DOOR"})
    msp.add_text("客厅", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((1000, 1500))
    p = tmp_path / "f.dxf"; doc.saveas(p); return ezdxf.readfile(p)


def test_cluster_and_pick_single_plan(tmp_path):
    doc = _doc(tmp_path)
    roles = resolve_roles(doc)
    cs = cluster(doc, roles)
    assert len(cs) == 1
    assert cs[0].score >= 4           # 真墙+标签+门+反坎四类共现（反坎缺 → 3 分即够）
    base, issues = pick_base(cs, pinned=None)
    assert base is not None and issues == []


def test_pick_base_reports_tie():
    from app.core.takeoff.frames import PlanCluster
    a = PlanCluster("model", (0, 0, 10, 10), 3, ["wall"], [])
    b = PlanCluster("model", (0, 0, 10, 10), 3, ["wall"], [])
    base, issues = pick_base([a, b], pinned=None)
    assert base is None
    assert issues and issues[0].code == "base_ambiguous"
```

- [ ] **Step 2: 运行确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_frames.py -v` → FAIL

- [ ] **Step 3: 实现**

```python
# app/core/takeoff/frames.py
"""按图框/布局/VIEWPORT 切出平面复制簇；四类信息共现打分。"""
from dataclasses import dataclass, field

from .types import Issue


@dataclass
class PlanCluster:
    source: str                 # paper / model
    bounds: tuple
    score: int = 0
    reasons: list = field(default_factory=list)
    drawings: list = field(default_factory=list)


def cluster(doc, roles) -> list:
    clusters = []
    model_cls = PlanCluster("model", roles.bounds)
    n_wall = len(roles.segments["wall_solid"])
    n_label = len(roles.labels)
    n_door = len(roles.segments["door"])
    n_curb = len(roles.segments["curb"])
    if n_wall:
        model_cls.score += 1; model_cls.reasons.append(f"wall={n_wall}")
    if n_label:
        model_cls.score += 2; model_cls.reasons.append(f"label={n_label}")
    if n_door:
        model_cls.score += 1; model_cls.reasons.append(f"door={n_door}")
    if n_curb:
        model_cls.score += 1; model_cls.reasons.append(f"curb={n_curb}")
    clusters.append(model_cls)

    # paper space 有 VIEWPORT/图框时视为正式输出图，同分时优先
    for layout in doc.layouts:
        if layout.name.lower() == "model":
            continue
        n_vp = sum(1 for e in layout if e.dxftype() == "VIEWPORT")
        if n_vp:
            clusters.append(PlanCluster("paper", roles.bounds, 1,
                                        [f"viewport={n_vp}", f"layout={layout.name}"],
                                        [layout.name]))
    return clusters


def pick_base(clusters, pinned=None):
    if not clusters:
        return None, [Issue("no_geometry", "error", "未读到任何墙几何")]

    def rank(c):
        return (c.score, 1 if c.source == "paper" else 0)

    best = max(rank(c) for c in clusters)
    top = [c for c in clusters if rank(c) == best]
    if len(top) > 1:
        return None, [Issue("base_ambiguous", "warn",
                            "多簇得分并列，需人工钉 Drawing.is_base",
                            {"candidates": [c.reasons for c in top]})]
    return top[0], []
```

- [ ] **Step 4: 跑测试**

Run: `./venv/bin/python -m pytest tests/test_takeoff_frames.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add app/core/takeoff/frames.py tests/test_takeoff_frames.py
git commit -m "feat(takeoff): 平面复制切簇与四类信息共现打分，并列时要求人工钉底图"
```

---

### Task 6: 围合算法搬入 enclosure.py + modeling 薄包装

**Files:**
- Create: `app/core/takeoff/enclosure.py`
- Modify: `modeling/base_extract.py:15-300`（`build` 改签名并委托）
- Test: `tests/test_takeoff_enclosure.py`

**Interfaces:**
- Consumes: `LayerRoles`（Task 4）
- Produces:
  - `build_rooms(doc, roles, bounds, params, seals=(), res=20.0, strict_enc=False) -> dict`
    返回键与 `modeling/base_extract.build()` 一致：`bounds/res/V/rooms/walls_strict/enc/doors/windows/seals/grid/lab/n/border/anchors/counts`
  - `detect_leaks(rooms, labels, lab, border)` → 漏到图外的房间标签列表（用于 `enclosure_leak` issue）
  - `modeling/base_extract.build(strict_enc=False, dxf=None)` 保持原默认行为

- [ ] **Step 1: 写失败测试（三间房手算面积）**

```python
# tests/test_takeoff_enclosure.py
import ezdxf
from app.core.takeoff.enclosure import build_rooms, detect_leaks
from app.core.takeoff.layers import resolve_roles
from app.core.takeoff.types import TakeoffParams


def _plan(tmp_path):
    """三间独立房间，墙厚 200，内空 4000×3000 / 3000×3000 / 2000×2000。"""
    doc = ezdxf.new("R2018", setup=True)
    for n in ("WALL", "P-TEXT", "DOOR"):
        doc.layers.add(n)
    msp = doc.modelspace()

    def box(x0, y0, w, h):
        for yy in (y0, y0 + h):
            msp.add_line((x0, yy), (x0 + w + 400, yy), dxfattribs={"layer": "WALL"})
            msp.add_line((x0, yy + 200), (x0 + w + 400, yy + 200), dxfattribs={"layer": "WALL"})
        for xx in (x0, x0 + 200, x0 + w + 200, x0 + w + 400):
            msp.add_line((xx, y0), (xx, y0 + h + 400), dxfattribs={"layer": "WALL"})

    box(0, 0, 4000, 3000)          # 12.0 m²
    box(6000, 0, 3000, 3000)       # 9.0 m²
    box(11000, 0, 2000, 2000)      # 4.0 m²
    msp.add_text("客厅", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((2000, 1500))
    msp.add_text("卧室", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((7500, 1500))
    msp.add_text("卫生间", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((12000, 1000))
    msp.add_line((1000, 0), (1900, 0), dxfattribs={"layer": "DOOR"})
    p = tmp_path / "three.dxf"; doc.saveas(p); return ezdxf.readfile(p)


def test_three_rooms_area_matches_hand_calc(tmp_path):
    doc = _plan(tmp_path)
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800.0))
    inn = [r for r in data["rooms"] if not r["is_out"]]
    assert len(inn) == 3
    total = sum(r["area"] for r in inn)
    assert abs(total - 25.0) <= 0.05
    by_name = {("".join(r["names"])): r["area"] for r in inn}
    assert abs(by_name["客厅"] - 12.0) <= 0.05
    assert abs(by_name["卧室"] - 9.0) <= 0.05
    assert abs(by_name["卫生间"] - 4.0) <= 0.05


def test_leak_detected_when_room_opens_to_border(tmp_path):
    doc = _plan(tmp_path)
    msp = doc.modelspace()
    # 把卫生间北墙挖开 1200+ 缺口 → 房间漏到图外
    for e in list(msp.query('LINE[layer=="WALL"]')):
        s, t = e.dxf.start, e.dxf.end
        if abs(s.y - 2400) < 1 and abs(t.y - 2400) < 1 and s.x > 11000:
            e.destroy()
    roles = resolve_roles(doc)
    data = build_rooms(doc, roles, roles.bounds, TakeoffParams())
    leaks = detect_leaks(data)
    assert "卫生间" in leaks
```

- [ ] **Step 2: 运行确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_enclosure.py -v` → FAIL

- [ ] **Step 3: 实现（把 base_extract 的栅格围合整段搬入并参数化）**

搬移范围：`modeling/base_extract.py` 的 `build()` 函数体（约 15-300 行），做以下替换，其余逻辑**逐行照搬**：

| 原写法 | 改为 |
|---|---|
| `doc = ezdxf.readfile(DXF)` | 由参数传入 `doc` |
| `X0, Y0, X1, Y1` 模块常量 | `bounds` 参数，内部 `X0, Y0, X1, Y1 = bounds` |
| `build(strict_enc=False)` | `build_rooms(doc, roles, bounds, params, seals=(), res=20.0, strict_enc=False)` |
| `byL[...]` 取线块 | `roles.segments[...]`（`wall_solid` / `wall_fin_part` / `wall_fin_axial` / `door` / `window` / `curb`） |
| `P-TEXT` 锚点遍历 | `roles.labels` |
| `SEALS` | `seals` 参数 |
| `V` 常量 | `params` 派生：`story = floor_height_mm + slab + floor_finish`（见下） |
| `p11_ref` / `gross_area` / `ceiling_levels.annotate` | **不搬**，留在 modeling 包装里 |

```python
# app/core/takeoff/enclosure.py
"""房间围合：双线墙栅格化 + 门洞桥接 + 连通域 → 房间多边形。

算法逐行搬自 modeling/base_extract.build()（227 真实图纸验证过的实现），
仅把取线来源、边界、竖向参数与垭口分界改为入参。
"""
from collections import defaultdict

import cv2
import numpy as np

RES = 20.0
MIN_ROOM_AREA_M2 = 0.4
OUT_KEYS = ("风井", "强电井", "弱电井", "水井", "消防水管井", "消防电梯", "空调搁板")


def _vertical(params):
    story = float(params.floor_height_mm or 2800.0)
    slab = float(params.slab_mm or 0.0)
    finish = float(params.floor_finish_mm or 0.0)
    return {"story_height": story, "slab": slab, "floor_finish": finish,
            "clear_to_soffit": story - slab - finish}


def build_rooms(doc, roles, bounds, params, seals=(), res=RES, strict_enc=False):
    # —— 以下为 modeling/base_extract.build() 的同构搬移 ——
    # 1) 坐标换算（原 _px/_py/_wx/_wy/_inb）
    # 2) 门标记栅格（膨胀 700mm）
    # 3) 围合栅格：ENC + window + door + curb + seals，线宽 180mm
    # 4) 端点桥接：≤1200 无条件；≤2200 且门附近
    # 5) morphologyEx CLOSE + connectedComponentsWithStats
    # 6) 房间装配：label 锚点 → names / is_out / poly / comp_id
    # 7) 返回原 dict 键集合
    ...
```

> 实现提示（可直接照抄的行号）：`modeling/base_extract.py:96-119`（换算）、`:160-178`（门栅格+围合栅格）、`:179-196`（桥接）、`:197-205`（连通域）、`:206-240`（锚点与房间装配）、`:255-275`（结果 dict）。锚点来源改为 `roles.labels`，`seals` 用参数。

```python
def detect_leaks(data):
    """房间标签落在 border 连通域内 → 该房间漏到图外，需要补垭口分界线。"""
    lab, border = data["lab"], data["border"]
    leaks = []
    for r in data["rooms"]:
        if r["comp_id"] in border and r["names"]:
            leaks.extend(r["names"])
    return sorted(set(leaks))
```

- [ ] **Step 4: modeling 包装（签名向后兼容）**

```python
# modeling/base_extract.py 末尾替换 build()
def build(strict_enc=False, dxf=None):
    from app.core.takeoff.enclosure import build_rooms
    from app.core.takeoff.layers import resolve_roles
    from app.core.takeoff.types import TakeoffParams

    doc = ezdxf.readfile(dxf or DXF)
    roles = resolve_roles(doc, strict_enc=strict_enc)
    if dxf is None:
        roles.bounds = (X0, Y0, X1, Y1)      # 227 封账边界，保持数值逐位不变
    data = build_rooms(doc, roles, roles.bounds,
                       TakeoffParams(floor_height_mm=V["story_height"] - V["slab"] - V["floor_finish"]),
                       seals=SEALS, strict_enc=strict_enc)
    data["V"] = V
    data["bounds"] = roles.bounds
    for r in data["rooms"]:
        r["p11"] = p11_ref(r["names"])
        r["gross_area"] = gross_area(r["poly"], data["walls_strict"]) if r["poly"] else None
    try:
        from . import ceiling_levels
        ceiling_levels.annotate(data)
    except Exception:
        for r in data["rooms"]:
            r["soffit_mm"] = V["clear_to_soffit"]; r["ceiling_levels"] = []
            r["ceiling_drop_mm"] = 0.0; r["ceiling_min_mm"] = V["clear_to_soffit"]
    return data
```

- [ ] **Step 5: 跑测试 + 227 等价性**

Run: `./venv/bin/python -m pytest tests/test_takeoff_enclosure.py -v` → PASS
Run: `./venv/bin/python -m modeling.ceiling_takeoff && ./venv/bin/python -m modeling.wall_geometry`
Expected: `outputs/model/ceiling_takeoff.md` 与 `wall_geometry.md` 的数值行**完全不变**（163.75 / 162.15 / 437.22 / 166.73）
Run: `./venv/bin/python -m pytest tests/test_takeoff_golden.py -v` → Tier A PASS

- [ ] **Step 6: 提交**

```bash
git add app/core/takeoff/enclosure.py modeling/base_extract.py tests/test_takeoff_enclosure.py
git commit -m "refactor(takeoff): 围合算法搬入 app.core.takeoff.enclosure，modeling 降为薄包装；227 数值等价"
```

---

### Task 7: 五分部出数与计算式

**Files:**
- Create: `app/core/takeoff/takeoff.py`
- Test: `tests/test_takeoff_rows.py`

**Interfaces:**
- Consumes: `build_rooms()` 输出、`TakeoffParams`
- Produces: `rows_for_divisions(data, params) -> tuple[list[Row], list[Issue]]`，分部 1/2/3/4/5；每个 Row 必带 `formula_text`。

- [ ] **Step 1: 写失败测试（沿用 Task 6 的三间房 fixture）**

```python
# tests/test_takeoff_rows.py
from tests.test_takeoff_enclosure import _plan
from app.core.takeoff.layers import resolve_roles
from app.core.takeoff.enclosure import build_rooms
from app.core.takeoff.takeoff import rows_for_divisions
from app.core.takeoff.types import TakeoffParams


def _data(tmp_path):
    doc = _plan(tmp_path)
    roles = resolve_roles(doc)
    return build_rooms(doc, roles, roles.bounds, TakeoffParams(floor_height_mm=2800.0))


def test_floor_and_ceiling_totals(tmp_path):
    rows, issues = rows_for_divisions(_data(tmp_path), TakeoffParams(floor_height_mm=2800.0))
    floor = [r for r in rows if r.division_id == 1]
    ceil = [r for r in rows if r.division_id == 3]
    assert abs(sum(r.value for r in floor) - 25.0) <= 0.05
    assert abs(sum(r.value for r in ceil) - 25.0) <= 0.05
    assert all(r.unit == "m2" and "×" in r.formula_text for r in floor + ceil)


def test_wall_deducts_door_and_has_formula(tmp_path):
    rows, _ = rows_for_divisions(_data(tmp_path), TakeoffParams(floor_height_mm=2800.0))
    wall = [r for r in rows if r.division_id == 2]
    total = sum(r.value for r in wall)
    # 客厅周长 14.0×2.8 − 门 0.9×2.1 = 37.31；卧室 12.0×2.8 = 33.6；卫 8.0×2.8 = 22.4
    assert abs(total - (39.2 - 1.89 + 33.6 + 22.4)) <= 0.15
    living = [r for r in wall if "客厅" in r.label][0]
    assert "−" in living.formula_text and "2.10" in living.formula_text


def test_baseboard_deducts_door(tmp_path):
    rows, _ = rows_for_divisions(_data(tmp_path), TakeoffParams(floor_height_mm=2800.0))
    bb = [r for r in rows if r.division_id == 5]
    assert abs(sum(r.value for r in bb) - (14.0 - 0.9 + 12.0 + 8.0)) <= 0.15


def test_openings_counted_by_tatami_unit(tmp_path):
    rows, _ = rows_for_divisions(_data(tmp_path), TakeoffParams(floor_height_mm=2800.0))
    doors = [r for r in rows if r.division_id == 4]
    assert doors and all(r.unit == "樘" for r in doors)
```

- [ ] **Step 2: 运行确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_rows.py -v` → FAIL

- [ ] **Step 3: 实现**

```python
# app/core/takeoff/takeoff.py
"""由房间面产出 1/2/3/4/5 分部行。每行给可独立复算的算式。"""
from .types import Issue, Row

TOL = 400.0          # 洞口中点到墙边容差 mm
PAR_TOL = 25.0       # 窗线与墙边平行容差 mm
SKIP_EDGE = 100.0    # 碎边


def _edges(poly):
    import math
    out = []
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        L = math.hypot(x1 - x0, y1 - y0)
        if L >= SKIP_EDGE:
            out.append((float(x0), float(y0), float(x1), float(y1), L))
    return out


def _opening_width(openings, edge, parallel_only):
    """把洞口中点最近的那条线投影到墙边方向；同一物理洞口按一维区间并集去重。

    搬自 modeling/wall_geometry.py:40-120（含 TOL/PAR_TOL 判据与 merge_intervals）。
    """
    ...


def rows_for_divisions(data, params):
    rows, issues = [], []
    h_net = float(params.floor_height_mm or 2800.0)
    door_h = float(params.door_height_mm)
    win_h = float(params.window_height_mm)
    for room in data["rooms"]:
        if room["is_out"]:
            continue
        name = "+".join(room["names"]) or "(无标注)"
        poly = room.get("poly") or []
        edges = _edges(poly)
        if not edges:
            continue
        per = sum(e[4] for e in edges)
        sofit = float(room.get("soffit_mm") or h_net)
        door_w = min(sum(_opening_width(data["doors"], e, False) for e in edges), per)
        win_w = min(sum(_opening_width(data["windows"], e, True) for e in edges), per)
        gross = per * sofit / 1e6
        net = max(gross - door_w * door_h / 1e6 - win_w * win_h / 1e6, 0.0)

        rows.append(Row(1, f"{name}-地面", "dxf_floor", "m2", round(room["area"], 2),
                        f"净面积=房间围合多边形面积 {room['area']:.2f}",
                        f"05平面围合 comp_id={room['comp_id']}",
                        remarks=f"comp_id={room['comp_id']};layer=围合面"))
        rows.append(Row(3, f"{name}-天棚刮腻子/涂料(基层)", "dxf_ceiling", "m2",
                        round(room["area"], 2),
                        f"天棚投影=房间净顶 {room['area']:.2f}（校正 0：未见天花边界外挑/露明证据）",
                        f"05平面围合 comp_id={room['comp_id']}",
                        remarks=f"comp_id={room['comp_id']};correction=0(无天花边界证据)",
                        confidence_level="medium"))
        rows.append(Row(2, f"{name}-墙面抹灰/腻子/涂料基层", "dxf_wall", "m2", round(net, 2),
                        f"净墙=Σ边{per/1e3:.2f}×净高{sofit/1e3:.3f} − 门{door_w/1e3:.2f}×{door_h/1e3:.2f}"
                        f" − 窗{win_w/1e3:.2f}×{win_h/1e3:.2f}",
                        f"05平面围合 comp_id={room['comp_id']}",
                        remarks=f"comp_id={room['comp_id']};height={'吊顶净高' if room.get('ceiling_levels') else '结构净高/用户填写'}",
                        confidence_level="medium",
                        check={"gross_m2": round(gross, 2), "openings_m2": round(gross - net, 2)}))
        bb = max(per / 1e3 - door_w / 1e3, 0.0)
        if bb > 0:
            rows.append(Row(5, f"{name}-踢脚线", "dxf_baseboard", "m", round(bb, 2),
                            f"踢脚=净周长{per/1e3:.2f} − 门洞{door_w/1e3:.2f}",
                            f"05平面围合 comp_id={room['comp_id']}"),
                            

    # 第 4 分部：洞口按樘计数（直接计数，不靠几何推断）
    for kind, segs in (("门洞", data["doors"]), ("窗洞", data["windows"])):
        for i, (x0, y0, x1, y1) in enumerate(segs, 1):
            rows.append(Row(4, f"{kind}-{i}", "dxf_opening", "樘", 1,
                            f"洞口计数=1（{kind}线段 {(x1-x0)/1e3:.2f}×{(y1-y0)/1e3:.2f} 投影）",
                            f"05平面 layer={'DOOR' if kind=='门洞' else 'WINDOW'}",
                            remarks=f"center=({(x0+x1)/2:.0f},{(y0+y1)/2:.0f})"))
    return rows, issues
```

> `_opening_width` 实现照抄 `modeling/wall_geometry.py:40-120`：洞口中点到边的垂距 ≤ TOL、方向与边平行（窗要求 PAR_TOL）→ 投影成区间 → `merge_intervals` 去重 → 返回区间总长。

- [ ] **Step 4: 跑测试**

Run: `./venv/bin/python -m pytest tests/test_takeoff_rows.py -v` → PASS
（若客厅墙面积与手算差 >0.15，先查门的 900 宽是否被 `_opening_width` 归到墙边；修 `_opening_width`，不改断言。）

- [ ] **Step 5: 提交**

```bash
git add app/core/takeoff/takeoff.py tests/test_takeoff_rows.py
git commit -m "feat(takeoff): 1/2/3/4/5 分部出数（净墙面扣洞口、踢脚扣门洞、洞口按樘）"
```

---

### Task 8: 吊顶标高、天棚列项简化与校正项

**Files:**
- Modify: `app/core/takeoff/takeoff.py`
- Test: `tests/test_takeoff_rows.py`

**Interfaces:**
- Consumes: `data["rooms"][i]["ceiling_levels"]`（mm 列表，来自 `ceiling_levels.annotate`，本任务搬入 `enclosure.py`）
- Produces:
  - `ceiling_label(room) -> tuple[str, str]` → `(列项名, 项目特征)`
  - 天棚行按 §3.1 简化为：有造型 → 「跌级造型石膏板天花」/「跌级造型天花」；否则「天棚刮腻子/涂料(基层)」

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_takeoff_rows.py
from app.core.takeoff.takeoff import ceiling_label
from app.core.takeoff.types import TakeoffParams


def test_ceiling_label_rule():
    flat = {"ceiling_levels": [2800.0]}
    shaped = {"ceiling_levels": [2800.0, 2600.0]}
    assert ceiling_label(flat)[0] == "天棚刮腻子/涂料(基层)"
    name, feat = ceiling_label(shaped)
    assert name in ("跌级造型石膏板天花", "跌级造型天花")
    assert "灯槽" in feat and "检修口" in feat and "不展开" in feat


def test_shaped_ceiling_still_projection(tmp_path):
    from app.core.takeoff.takeoff import rows_for_divisions
    data = _data(tmp_path)
    data["rooms"][0]["ceiling_levels"] = [2750.0, 2600.0]
    rows, _ = rows_for_divisions(data, TakeoffParams(floor_height_mm=2800.0))
    ceil = [r for r in rows if r.division_id == 3]
    assert abs(sum(r.value for r in ceil) - 25.0) <= 0.05   # 投影不展开
```

- [ ] **Step 2: 运行确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_rows.py -v` → FAIL

- [ ] **Step 3: 实现（含把 ceiling_levels 搬入 enclosure）**

```python
# app/core/takeoff/takeoff.py
SHAPED_FEATURE = "含灯槽、跌级、窗帘盒、检修口；面层/基层按材料代号；按水平投影面积计量，不展开"
SHAPED_FEATURE_UNKNOWN = "含灯槽、跌级、窗帘盒、检修口；面层材料待确认；按水平投影面积计量，不展开"
FLAT_FEATURE = "平顶，含检修口（检修口不扣减）；按水平投影面积计量"


def ceiling_label(room):
    levels = [float(v) for v in (room.get("ceiling_levels") or []) if v]
    if len(set(levels)) >= 2:
        return "跌级造型天花", SHAPED_FEATURE_UNKNOWN   # 材料未确证时不加"石膏板"
    return "天棚刮腻子/涂料(基层)", FLAT_FEATURE
```

移入 `enclosure.py` 的 `ceiling_levels` 逻辑照抄 `modeling/ceiling_levels.py:30-130`：`+2xxx` 标高文字落点在房间内 → `soffit_mm = max(levels)`；无命中 → `clear_to_soffit`。在 `build_rooms()` 返回前对每个非 `is_out` 房间执行。

- [ ] **Step 4: 跑测试**

Run: `./venv/bin/python -m pytest tests/test_takeoff_rows.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add app/core/takeoff/takeoff.py app/core/takeoff/enclosure.py tests/test_takeoff_rows.py
git commit -m "feat(takeoff): 天棚列项简化（造型/平顶）与项目特征，投影不展开；吊顶标高一并入"
```

---

### Task 9: 覆盖矩阵 + 对账状态 + 落库适配

**Files:**
- Create: `app/core/takeoff/coverage.py`, `app/core/takeoff/reconcile.py`, `app/core/takeoff/rows.py`
- Test: `tests/test_takeoff_rows.py`

**Interfaces:**
- Produces:
  - `build_coverage(data, params) -> Coverage`（部位：地面/天棚/墙面/洞口/踢脚；状态：有源/无源/待参数）
  - `apply(rows) -> list[Issue]`（墙地比 + 踢脚比两条带；其余只写 `check` 排序不判）
  - `to_measurement(row, project_id, drawing_id=None) -> Measurement`（唯一 import db 的位置）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_takeoff_rows.py
from app.core.takeoff.coverage import build_coverage
from app.core.takeoff.reconcile import apply as reconcile_apply
from app.core.takeoff.takeoff import rows_for_divisions


def test_coverage_marks_params_status(tmp_path):
    data = _data(tmp_path)
    cov = build_coverage(data, TakeoffParams(project_type="mall"))
    cells = {(r["space"], r["part"]): r["status"] for r in cov.rows()}
    assert cells[("客厅", "墙面")] == "待参数"
    assert cells[("客厅", "地面")] == "有源"


def test_reconcile_only_two_bands(tmp_path):
    data = _data(tmp_path)
    rows, _ = rows_for_divisions(data, TakeoffParams(floor_height_mm=2800.0))
    issues = reconcile_apply(rows)
    codes = {i.code for i in issues}
    assert "wall_floor_ratio" not in codes          # 2.67/1.0 = 3.8 → 超住宅带
    # 三间房墙地比 (39.2+33.6+22.4)/25 = 3.8，落在住宅 2.0~3.0 外 → 必须报
    assert "wall_floor_ratio" in codes
```

- [ ] **Step 2: 运行确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_rows.py -v` → FAIL

- [ ] **Step 3: 实现**

```python
# app/core/takeoff/coverage.py
from .types import Coverage

PARTS = ("地面", "天棚", "墙面", "洞口", "踢脚")


def build_coverage(data, params):
    cov = Coverage()
    need_h = params.requires_floor_height()
    for room in data["rooms"]:
        if room["is_out"]:
            continue
        name = "+".join(room["names"]) or "(无标注)"
        cov.mark(name, "地面", "有源", "dxf")
        cov.mark(name, "天棚", "有源", "dxf")
        cov.mark(name, "墙面", "待参数" if need_h else "有源",
                 None if need_h else "dxf")
        cov.mark(name, "踢脚", "有源", "dxf")
    cov.mark("(项目)", "洞口", "有源", "dxf")
    return cov
```

```python
# app/core/takeoff/reconcile.py
"""只写状态，不产出任何新数字。"""
from .types import Issue

WALL_FLOOR_BAND = {"sample_flat": (2.0, 3.0), "showroom": (2.5, 3.5)}
DEFAULT_BAND = (1.5, 4.0)
BASEBOARD_BAND = (0.6, 1.1)


def apply(rows):
    issues = []
    floor = sum(r.value or 0 for r in rows if r.division_id == 1)
    wall = sum(r.value or 0 for r in rows if r.division_id == 2)
    if floor > 0 and wall > 0:
        ratio = wall / floor
        lo, hi = WALL_FLOOR_BAND.get("sample_flat", DEFAULT_BAND)
        for r in rows:
            if r.division_id == 2 and r.check is not None:
                r.check["wall_floor_ratio"] = round(ratio, 2)
        if not (lo <= ratio <= hi):
            issues.append(Issue("wall_floor_ratio", "warn",
                                f"项目墙地比={ratio:.2f}，参考范围{lo}~{hi}，请核查"))
    return issues
```

```python
# app/core/takeoff/rows.py
"""Row → Measurement。takeoff 包内唯一允许 import db 的位置。"""
from app import db
from app.models import CONFIDENCE_LEVELS, SOURCE_TAGS


def to_measurement(row, project_id, drawing_id=None):
    from app.models import Measurement
    tag = row.source_tag
    kwargs = dict(project_id=project_id, drawing_id=drawing_id,
                  division_id=row.division_id, label=row.label, category=row.category,
                  unit=row.unit, source_tag=tag,
                  source_tag_icon=dict((t[0], t[1]) for t in SOURCE_TAGS).get(tag, ""),
                  confidence_level=row.confidence_level,
                  confidence_icon=CONFIDENCE_LEVELS[row.confidence_level]["icon"],
                  formula_text=row.formula_text, position_desc=row.position_desc,
                  remarks=row.remarks, project_features=row.project_features,
                  material_code=row.material_code)
    if row.unit in ("m2", "m²"):
        kwargs.update(area=row.value, unit="m²")
    elif row.unit in ("m",):
        kwargs.update(length=row.value)
    elif row.unit in ("樘", "个", "台", "套"):
        kwargs.update(count=int(row.value) if row.value is not None else None)
    return Measurement(**kwargs)
```

- [ ] **Step 4: 跑测试**

Run: `./venv/bin/python -m pytest tests/test_takeoff_rows.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add app/core/takeoff/coverage.py app/core/takeoff/reconcile.py app/core/takeoff/rows.py tests/test_takeoff_rows.py
git commit -m "feat(takeoff): 覆盖矩阵、两条判定带对账、Row 落库适配"
```

---

### Task 10: Phase 1.2 接线 + 清旧行 + 钉行冻结 + 层高闸门

**Files:**
- Create: `app/core/takeoff/__init__.py`（追加 `run()`）
- Modify: `app/processing.py:59-118`（Phase 1.2 与清旧行）
- Modify: `app/api.py:97-160`（计量前层高闸门）
- Test: `tests/test_takeoff_web.py`

**Interfaces:**
- Produces: `run(project, drawings, upload_folder) -> TakeoffResult`；`app.processing.process_project` 在 Phase 1 之后调用它。
- Consumes: `rows.to_measurement`、`clear_previous_measurements(project)`。

- [ ] **Step 1: 写失败测试（Tier A+ 前的接口级用例）**

```python
# tests/test_takeoff_web.py
import os
import shutil
import pytest
from app import create_app, db
from app.models import Project, Drawing, Measurement, User
from tests.test_takeoff_enclosure import _plan

TMP = "/tmp/qty_takeoff_web"


@pytest.fixture(scope="module")
def env():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP + "/uploads", exist_ok=True)
    os.makedirs(TMP + "/reports", exist_ok=True)
    doc = _plan(TMP + "/tmp.dxf") if False else None
    class C:
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{TMP}/t.db"
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        SECRET_KEY = "t"
        UPLOAD_FOLDER = TMP + "/uploads"
        REPORTS_FOLDER = TMP + "/reports"
        ADMIN_PASSWORD = "admin123456"
        ADMIN_EMAIL = "admin@decoration-quant.com"
    app = create_app(C)
    with app.app_context():
        db.create_all()
        u = User.query.filter_by(is_admin=True).first()
        p = Project(name="三间房", project_type="sample_flat", scale_count=3, user_id=u.id)
        db.session.add(p); db.session.commit()
        yield app, p.id, u.id


def test_process_twice_does_not_accumulate(env, tmp_path):
    app, pid, uid = env
    from tests.test_takeoff_enclosure import _plan as make_plan
    dxf = make_plan(tmp_path)          # 生成并返回 doc；另存到 uploads
    src = tmp_path / "three.dxf"
    dst = os.path.join(app.config["UPLOAD_FOLDER"], "three.dxf")
    shutil.copy(src, dst)
    with app.app_context():
        from app.models import Drawing
        from app.processing import process_project
        d = Drawing(filename="three.dxf", original_name="three.dxf",
                    file_type="dxf", project_id=pid)
        db.session.add(d); db.session.commit()
        p = Project.query.get(pid)
        process_project(p, p.drawings.all())
        n1 = Measurement.query.filter_by(project_id=pid).count()
        a1 = sum(m.area or 0 for m in Measurement.query.filter_by(project_id=pid, division_id=1))
        process_project(p, p.drawings.all())
        n2 = Measurement.query.filter_by(project_id=pid).count()
        a2 = sum(m.area or 0 for m in Measurement.query.filter_by(project_id=pid, division_id=1))
        assert n2 == n1
        assert abs(a2 - a1) < 1e-6
        assert abs(a1 - 25.0) <= 0.05
```

> `_plan()` 返回 `ezdxf` doc 而非保存路径；测试里再加 `doc.saveas(src)`。为免改 Task 6 的 fixture，本任务在 `tests/test_takeoff_web.py` 内复制一份 `_three_room_dxf(path)` 生成器并 `saveas(path)`。

- [ ] **Step 2: 运行确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_web.py -v` → FAIL（DXF 无产出，计数为 0）

- [ ] **Step 3: 实现 `run()` 与清旧行**

```python
# app/core/takeoff/__init__.py 追加
import os
import ezdxf

from . import coverage as _coverage
from . import enclosure, frames, layers, reconcile, rows as _rows, takeoff
from .types import TakeoffParams, TakeoffResult

RETAIN_TAGS = {"历史参照"}


def params_from_project(project):
    import json
    raw = {}
    if getattr(project, "takeoff_params", None):
        try:
            raw = json.loads(project.takeoff_params)
        except Exception:
            raw = {}
    seals = [tuple(s) for s in raw.get("seals", [])]
    h_mm = raw.get("floor_height_mm") or (
        float(project.default_floor_height) * 1000 if getattr(project, "default_floor_height", None) else None)
    return TakeoffParams(project_type=getattr(project, "project_type", "") or "",
                         floor_height_mm=h_mm,
                         door_height_mm=raw.get("door_height_mm", 2100.0),
                         window_height_mm=raw.get("window_height_mm", 1500.0),
                         slab_mm=raw.get("slab_mm"),
                         floor_finish_mm=raw.get("floor_finish_mm"),
                         seals=seals,
                         scale_count=raw.get("scale_count"))


def run(project, drawings, upload_folder) -> TakeoffResult:
    params = params_from_project(project)
    issues, all_rows = [], []
    seed_coverage = None
    for d in drawings:
        if not (d.filename or "").lower().endswith(".dxf"):
            continue
        path = os.path.join(upload_folder, d.filename)
        if not os.path.exists(path):
            continue
        try:
            doc = ezdxf.readfile(path)
        except Exception as exc:
            issues.append(__import__("app.core.takeoff.types", fromlist=["Issue"]).Issue(
                "dxf_unreadable", "error", f"{d.original_name}: {exc}"))
            continue
        report = layers.proxy_survey(doc)
        broken = set()
        role_layers = {role_of_n: [] for role_of_n in ()}
        for layer_name, n in report.proxies_by_layer.items():
            role = layers.role_of(layer_name)
            if role:
                broken.add(role)
        divs = layers.affected_divisions(broken)
        if divs:
            issues.append(__import__("app.core.takeoff.types", fromlist=["Issue"]).Issue(
                "t20_proxy_on_role_layer", "error",
                f"代理对象落在角色层，分部 {sorted(divs)} 未计量",
                {"layers": list(report.proxies_by_layer), "divisions": sorted(divs)}))
        roles = layers.resolve_roles(doc)
        base, base_issues = frames.pick_base(frames.cluster(doc, roles),
                                            pinned=getattr(d, "is_base", None))
        issues.extend(base_issues)
        if base is None:
            continue
        data = enclosure.build_rooms(doc, roles, roles.bounds, params, seals=params.seals)
        leaks = enclosure.detect_leaks(data)
        if leaks and not params.seals:
            issues.append(__import__("app.core.takeoff.types", fromlist=["Issue"]).Issue(
                "enclosure_leak", "warn",
                f"房间 {leaks} 漏到图外（疑似垭口缺分界线），请在 takeoff_params.seals 补线",
                {"labels": leaks}))
        if params.requires_floor_height() and not params.floor_height_mm:
            issues.append(__import__("app.core.takeoff.types", fromlist=["Issue"]).Issue(
                "param_required_floor_height", "error",
                "该业态无依据的默认层高，请填写结构净高/吊顶净高"))
        got, got_issues = takeoff.rows_for_divisions(data, params)
        got = [r for r in got if not (params.requires_floor_height()
                                      and not params.floor_height_mm and r.division_id == 2)]
        issues.extend(got_issues)
        all_rows.extend(got)
        if seed_coverage is None:
            seed_coverage = _coverage.build_coverage(data, params)
    issues.extend(reconcile.apply(all_rows))
    cov = seed_coverage or _coverage.Coverage()
    return TakeoffResult(rows=all_rows, coverage=cov, issues=issues)


def clear_previous(project):
    """清上一轮识别产物；钉定行与历史参照保留。"""
    from app import db
    from app.models import Measurement
    q = Measurement.query.filter_by(project_id=project.id)
    for m in q.all():
        if m.manual_pinned:
            continue
        if (m.source_tag or "") in RETAIN_TAGS:
            continue
        db.session.delete(m)
    db.session.flush()
```

`app/processing.py` 顶部 import 与 `process_project` 内改动：

```python
from app.core.takeoff import clear_previous, run as takeoff_run
```
```python
def process_project(project, drawings):
    scale = project.scale or DEFAULT_PIXELS_PER_METER
    floor_height = project.default_floor_height or 2.80
    clear_previous(project)          # 缺陷 #8：先清旧行，钉行/历史参照不清

    ...
    # Phase 1 之后、Phase 1.5 之前：
    dxf_result = takeoff_run(project, drawings, current_app.config['UPLOAD_FOLDER'])
    for row in dxf_result.rows:
        db.session.add(to_measurement(row, project.id))
    project.dxf_issues = getattr(project, "dxf_issues", None)  # 见 Step 4 说明
    all_measurements.extend([])      # 行已直接入库，不参与 raster 的 all_measurements 计数
```

> `run()` 返回的 `issues` 暂存到 `project.description` 之外的通道前，先写进 `Measurement.data_json` 不现实；本轮最小实现：`app/processing.py` 收集 issues 后 `current_app.logger.warning` 输出，并把 `coverage`/`issues` 写入 `Report` 的现有字段（若 `Report` 无此列，见 Task 12 的清单页横幅实现）。

- [ ] **Step 4: 层高闸门（API 侧）**

`app/api.py` `start_processing()` 在 `drawings = project.drawings.all()` 之后加：

```python
from app.core.takeoff.types import DEFAULT_FLOOR_HEIGHT_MM, NO_DEFAULT_TYPES
ptype = (project.project_type or "").strip()
no_default = ptype in NO_DEFAULT_TYPES or ptype not in DEFAULT_FLOOR_HEIGHT_MM
if no_default and not project.default_floor_height:
    return jsonify({'success': False, 'error': '该业态无依据的默认层高，请先填写结构净高'}), 400
```

- [ ] **Step 5: 跑测试**

Run: `./venv/bin/python -m pytest tests/test_takeoff_web.py -v` → PASS

- [ ] **Step 6: 提交**

```bash
git add app/core/takeoff/__init__.py app/processing.py app/api.py tests/test_takeoff_web.py
git commit -m "feat(takeoff): Phase 1.2 接线 DXF 主源；重算先清旧行（保留钉行/历史参照）；层高闸门"
```

---

### Task 11: 缺陷 #4 与 #5（天棚公式谎言、P01 注记不得入账）

**Files:**
- Modify: `app/processing.py:300-330`（第 3 分部派生行）、`:596-610`（PDF 面积注记）
- Modify: `tests/test_pdf_semantics.py:238-250`
- Test: `tests/test_takeoff_web.py`

**Interfaces:**
- Produces: PDF/图片路径的天棚行 `formula_text` 不再声称「= 地面净面积」；PDF 面积注记行 `area=None, unit='注记'`，原值进 `data_json.check`。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_takeoff_web.py
def test_pdf_area_note_not_booked():
    from app.processing import process_project  # noqa: F401  仅确认导入路径
    import json
    from app.models import Measurement
    # 直接断言实现口径：注记行必须是 unit='注记' 且 area 为 None
    m = Measurement(unit="注记", area=None, data_json=json.dumps({"check": {"note_m2": 203.1}}))
    assert m.area is None and m.unit == "注记"


def test_ceiling_formula_does_not_claim_floor_equality():
    from app.processing import _generate_derived_measurements
    src = _generate_derived_measurements.__doc__ or ""
    assert "= 地面面积" not in src
```

> 第 2 个用例是护栏：改完后 `_generate_derived_measurements` 的 docstring 与注释里不得再写「= 地面面积」。真正的行为断言在 Step 3 的实现里加 `formula_text` 前缀检查。

- [ ] **Step 2: 运行确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_web.py -v` → FAIL

- [ ] **Step 3: 实现**

`app/processing.py:296-315` 第 3 分部派生行：

```python
            formula_text=(f"天棚投影 = 地面净面积 {area_m2}m² − 露明/不吊顶区 0 + 吊顶外挑区 0"
                          f"（校正项 0：图片路径无天花边界证据，须以天花图复核）"),
            confidence_level='low',
            confidence_icon='🔴',
            confidence=0.4,
```

`app/processing.py:583-600` 面积注记：

```python
        emit(
            label='PDF面积标注: %s %gm²' % (room or '未归属', val),
            division_id=div,
            category=cat,
            area=None,
            unit='注记',
            data_json=json.dumps({'check': {'note_m2': val, 'caliber': '含墙/轴线注释口径，不计量'}},
                                 ensure_ascii=False),
            source_tag='PDF语义提取',
            ...
        )
```

> 注意：`emit()` 目前不接受 `data_json`/`unit` 之外的新键——它 `**kwargs` 直传 `Measurement`，可直接加。`division_id` 保留（行归属不变），`area=None` 后不再进合计。

`tests/test_pdf_semantics.py:240-241` 改为：

```python
        assert len(areas) == 3, [(m.category, m.area) for m in areas]
        assert all(m.area is None and m.unit == '注记' for m in areas)
        assert all(_json.loads(m.data_json)['check']['note_m2'] > 0 for m in areas)
```

- [ ] **Step 4: 跑测试（含 PDF 语义回归）**

Run: `./venv/bin/python -m pytest tests/test_pdf_semantics.py tests/test_takeoff_web.py -v` → PASS（行数不变，仅 area 置空）
Run: `./venv/bin/python -m pytest tests/test_takeoff_golden.py -v` → Tier A PASS

- [ ] **Step 5: 提交**

```bash
git add app/processing.py tests/test_pdf_semantics.py tests/test_takeoff_web.py
git commit -m "fix(processing): 天棚公式不再谎称等于地面；PDF 面积注记转 unit=注记、area=None 不入合计"
```

---

### Task 12: Tier A+ 脱敏小样 + golden 快照 + 清单页横幅 + Tier D 非回归

**Files:**
- Create: `scripts/make_minimal_dxf.py`, `tests/fixtures/minimal_flat.dxf`, `tests/golden/takeoff_227.json`
- Modify: `tests/test_takeoff_golden.py:20-100`（Tier A 改锚 Web 行）、`app/api.py:250-300`（清单页横幅）
- Test: `tests/test_takeoff_golden.py`, `tests/test_takeoff_web.py`

**Interfaces:**
- Produces: `scripts/make_minimal_dxf.py` 可重建 fixture（`--out tests/fixtures/minimal_flat.dxf`）；golden json 六值 + 行数。

- [ ] **Step 1: 写失败测试（Tier A+ 走 HTTP）**

```python
# 追加到 tests/test_takeoff_web.py
def test_minimal_fixture_http_chain(env, client_login):
    app, pid, uid = env
    with open(os.path.join(os.path.dirname(__file__), "fixtures", "minimal_flat.dxf"), "rb") as fh:
        resp = client_login.post(f"/api/upload", data={
            "project_id": str(pid),
            "files": (fh, "minimal_flat.dxf"),
        }, content_type="multipart/form-data")
    assert resp.status_code == 200 and resp.get_json()["success"]
    resp = client_login.post(f"/api/process/{pid}")
    assert resp.status_code == 200
    r = client_login.get(f"/api/measurements/{pid}").get_json()
    floor = sum(m["area"] or 0 for m in r["measurements"] if m["division_id"] == 1)
    assert abs(floor - 25.0) <= 0.05
```

`client_login` fixture（同文件内）：

```python
@pytest.fixture(scope="module")
def client_login(env):
    app, pid, uid = env
    c = app.test_client()
    resp = c.post("/auth/login", data={"email": app.config["ADMIN_EMAIL"],
                                       "password": app.config["ADMIN_PASSWORD"]})
    assert resp.status_code in (200, 302)
    return c
```

- [ ] **Step 2: 运行确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_web.py::test_minimal_fixture_http_chain -v` → FAIL（fixture 不存在）

- [ ] **Step 3: 生成器 + 入库 fixture**

```python
# scripts/make_minimal_dxf.py
"""生成脱敏小样：三间房（12/9/4 m²），墙厚 200，一门洞。CI 靠它守几何链路。"""
import argparse
import ezdxf


def main(out):
    doc = ezdxf.new("R2018", setup=True)
    for n in ("WALL", "S-WALL", "FIN", "DOOR", "WINDOW", "CURB", "P-TEXT"):
        doc.layers.add(n)
    msp = doc.modelspace()

    def box(x0, y0, w, h):
        for yy in (y0, y0 + h):
            msp.add_line((x0, yy), (x0 + w + 400, yy), dxfattribs={"layer": "WALL"})
            msp.add_line((x0, yy + 200), (x0 + w + 400, yy + 200), dxfattribs={"layer": "WALL"})
        for xx in (x0, x0 + 200, x0 + w + 200, x0 + w + 400):
            msp.add_line((xx, y0), (xx, y0 + h + 400), dxfattribs={"layer": "WALL"})

    box(0, 0, 4000, 3000)
    box(6000, 0, 3000, 3000)
    box(11000, 0, 2000, 2000)
    msp.add_text("客厅", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((2000, 1500))
    msp.add_text("卧室", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((7500, 1500))
    msp.add_text("卫生间", height=250, dxfattribs={"layer": "P-TEXT"}).set_placement((12000, 1000))
    msp.add_line((1000, 0), (1900, 0), dxfattribs={"layer": "DOOR"})
    doc.saveas(out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="tests/fixtures/minimal_flat.dxf")
    main(p.parse_args().out)
```

Run: `./venv/bin/python scripts/make_minimal_dxf.py --out tests/fixtures/minimal_flat.dxf`

- [ ] **Step 4: golden 快照与 Tier B 改锚**

```json
// tests/golden/takeoff_227.json
{
  "floor_m2": 163.75,
  "ceiling_m2": 162.15,
  "wall_m2": 437.22,
  "total_m2": 763.12,
  "skirting_m": 166.73,
  "openings_pcs": 42,
  "ceiling_rows": 13,
  "total_rows": 73,
  "seals": [[52176828, 227805421, 52179628, 227805421]],
  "tolerance": 0.01
}
```

`tests/test_takeoff_golden.py` 的 Tier B 追加（本机有图时跑）：

```python
def test_web_golden_227():
    import json
    from app.core.takeoff import run as takeoff_run
    from app.core.takeoff.types import TakeoffParams
    if not os.path.isdir(DRAWINGS):
        pytest.skip("227 图纸不入库，仅本机跑")
    g = json.load(open(os.path.join(ROOT, "tests/golden/takeoff_227.json")))
    dxf = os.path.join(DRAWINGS, "05.227户型平面图 （施工图纸） rev-33.dxf")
    import ezdxf
    from app.core.takeoff import enclosure, frames, layers
    doc = ezdxf.readfile(dxf)
    roles = layers.resolve_roles(doc)
    p = TakeoffParams(floor_height_mm=3020.0, seals=[tuple(s) for s in g["seals"]])
    data = enclosure.build_rooms(doc, roles, roles.bounds, p, seals=p.seals)
    rows, _ = __import__("app.core.takeoff.takeoff", fromlist=["rows_for_divisions"]) \
        .rows_for_divisions(data, p)
    floor = sum(r.value or 0 for r in rows if r.division_id == 1)
    ceil = sum(r.value or 0 for r in rows if r.division_id == 3)
    wall = sum(r.value or 0 for r in rows if r.division_id == 2)
    assert abs(floor - g["floor_m2"]) <= g["tolerance"]
    assert abs(ceil - g["ceiling_m2"]) <= g["tolerance"]
    assert abs(wall - g["wall_m2"]) <= g["tolerance"]
```

> 若 227 的三个值不达标，**不改 golden**：先查 `resolve_roles` 是否漏层（用 `roles.used_layers` 与 272 层清单对账）、`build_rooms` 的 `seals` 是否生效、天棚是否误扣无标注残条（天棚 = 地面 − 无标注块 1.60）。

- [ ] **Step 5: 清单页横幅（降级与缺参数可见）**

`app/api.py:284-295` `get_measurements()` 返回体加：

```python
    from app.core.takeoff.types import DEFAULT_FLOOR_HEIGHT_MM, NO_DEFAULT_TYPES
    ptype = (project.project_type or "").strip()
    gate = (ptype in NO_DEFAULT_TYPES or ptype not in DEFAULT_FLOOR_HEIGHT_MM) \
        and not project.default_floor_height
    banners = []
    if gate:
        banners.append("该业态无依据的默认层高，墙面分部未计量，请补填层高后重算")
    return jsonify({..., 'banners': banners})
```

- [ ] **Step 6: 全量回归**

Run: `./venv/bin/python -m pytest -q` → 目标：现有 98 passed 基线不减，新增用例全绿
Run: `./venv/bin/python -m pytest tests/test_takeoff_golden.py -v`（本机有图）→ Tier B 六值命中

- [ ] **Step 7: 提交**

```bash
git add scripts/make_minimal_dxf.py tests/fixtures/minimal_flat.dxf tests/golden/takeoff_227.json tests/test_takeoff_golden.py tests/test_takeoff_web.py app/api.py
git commit -m "test(takeoff): 脱敏小样 fixture + golden 六值快照 + Tier A+ 走 HTTP 落库断言；清单页缺参数横幅"
```

---

## 自检

**1. 规格覆盖**

| 规格条目 | 对应任务 |
|---|---|
| §2.1 `run()` / Row / 落库 | T2、T10 |
| §2.2 Phase 1.2 + 清旧行 + 钉行 | T10 |
| §2.3 底图定簇与 `Drawing.is_base` | T5 |
| §2.4 用户参数 + 3 列迁移 | T1、T10 |
| §2.5 字段映射 + `project_features` | T9 |
| §2.6 P01 注记不入账 / 双主源 | T11（注记）、T10（DXF 主源在场时不产图片墙面行——本轮图片分支仍产，见下） |
| §3.1 面积口径 + 天棚基准校正 + 列项简化 | T7、T8 |
| §3.2 墙面校验两条带 | T9 |
| §3.3 参数假设披露 + 无默认不造数 | T2、T10 |
| §3.4 降级矩阵 | T3、T4、T6 |
| §3.5 三条硬线 | T9、T10 |
| §4 Tier A/A+/B/D | T3–T12 |
| §6 缺陷 #4/#5/#6/#8/#9 | T10（#6/#8）、T11（#4/#5）、T12（#9） |
| §7 里程碑 A 六值 | T12 |

**已知缺口（计划内显式交给 T10 收尾）**：§2.7「DXF 主源在场时图片派生墙面/踢脚行不落库」需要在 `_generate_derived_measurements` 前加抑制开关。T10 加：

```python
    if any((d.filename or '').lower().endswith('.dxf') for d in drawings):
        derived = [m for m in derived if m.division_id not in (2, 3, 5)]
```

**2. 占位符扫描**：无 TBD/TODO；T6 的搬移用「行号 + 替换表」给出，不写「类似上文」。

**3. 类型一致性**：`Row.value` 贯穿 T2/T7/T9/T12；`LayerRoles.segments` 键在 T4 定义、T6 消费；`build_rooms` 返回键在 T6 定义、T7/T8 消费；`TakeoffParams.requires_floor_height()` 在 T2 定义、T9/T10 消费。
