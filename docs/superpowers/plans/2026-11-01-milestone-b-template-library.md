# 里程碑 B：样板房范例库与查漏查错 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用样板房（`sample_flat`）范例库回答「该不该有这一行」，把漏项三档判定与合理性指标接入 `process_project` Phase 5，并在清单页/报告上可见；补入行只列项、不带量。

**Architecture:** 新增纯模块 `app/core/takeoff/template.py`（加载 JSON 模板、三档判定、合理区间评估）与 `app/core/takeoff/templates/sample_flat.json`（由本地 227 清单脚本抽取，工程量列读入即丢）。`reconcile.py` 不再持有硬编码区间，改为调用 `template.evaluate_ranges`；踢脚比的分母从「地面面积」改为「参与踢脚的围合周长」。`process_project` 在 Phase 4 后插入 Phase 5：只写 issues 与 `summary_json['takeoff']['template']`，不新增/修改任何计量行。

**Tech Stack:** Python 3.9 venv（`./venv/bin/python`）、Flask/SQLAlchemy、openpyxl、pytest；CI 为 Python 3.11 + `pytest -m "not slow"`。

**Spec:** `docs/superpowers/specs/2026-10-17-general-takeoff-engine-design.md` §5（5.1–5.8）。

## Global Constraints

- `app/core/takeoff/` 是纯包：`template.py` 只允许 import 标准库与 `.types`；不得 import flask / app.models / modeling / ezdxf。
- 227 封账值只进测试与模板来源说明，不得写入生产代码常量：地面 163.75 / 天棚 162.15 / 墙面 437.22 / 合计 763.12 m²、踢脚 166.73 m、洞口 42 樘、P2c 周长 210.02 m。
- 模板 JSON 不得含任何工程量数值；脚本内置 `assert_no_quantities`，进 Tier C。
- `auto_fill_missing` 补入行必须 `area/length/count is None`，不得改变任何分部合计。
- 不提交 `.env` 或凭证；不 stage/commit 用户未完成的 MEP 工作（`outputs/*.xlsx`、`outputs/model/mep_takeoff_m5.md`、`scripts/build_mep_workbook.py`、`tests/test_mep_module.py`）；不动 `_*.py`、`outputs/preview/**`、`.wrangler/`。
- 绝不设置 `TEST_DATABASE_URL`。本地 PG 用 5433 + `127.0.0.1`；Flask 用 5001。
- 每个任务独立可测、独立提交；提交前跑该任务列出的测试命令。

## 设计裁定（与 spec 的对应关系）

1. **匹配粒度**：spec §5.5 的 `(分部, 材料词, 单位)` 用于「历史清单 ↔ 当前行」的对照（Task 7）。模板审计对**几何型列项**改用「分部 + 能力」存在性判断（`floor_area`/`wall_base`/`ceiling_area`/`baseboard_length`/`opening_count`），因为里程碑 A 的引擎行没有材料码；对**非几何型列项**（饰面、家具、水电、暖通、消防）仍用材料词匹配，匹配不上即「缺图源」，绝不误报「真漏算」。
2. **合理性区间唯一来源**：`app/core/takeoff/templates/sample_flat.json` 的 `ranges`。`reconcile.py` 与 `app/comparison.py` 不再保留区间表；未知业态回退 `template.BUILTIN_RANGES`（仅 showroom/default，样板房必须有 JSON）。
3. **踢脚比分母**：参与踢脚的房间围合周长（残条不计）。227 实测 166.73 / 187.04 = 0.89；P2c 的 210.02 含残条，不作为分母。
4. **auto-fill 触发**：Phase 5 只披露，不自动补行；用户在清单页点「加入待算清单」才补，补入行 `category='template_gap'`，重算不清除。

---

### Task 1: 模板 JSON 抽取脚本与样板房范例库

**Files:**
- Create: `app/core/takeoff/template.py`（本任务只放词表与单位/材料归一化，Task 2 继续扩展）
- Create: `scripts/build_boq_template.py`
- Create: `tests/test_build_boq_template.py`
- Generate: `app/core/takeoff/templates/sample_flat.json`

**Interfaces:**
- Consumes: 本地工作簿 `装修图纸/227户型dxf/金融城227户型样板房工程量清单.xlsx`（gitignored）
- Produces:
  - `template.normalize_unit(raw) -> str`
  - `template.material_of(name) -> str | None`
  - `scripts.build_boq_template.build(workbook_path) -> dict`
  - `scripts.build_boq_template.assert_no_quantities(obj, path='$', key=None) -> None`
  - `app/core/takeoff/templates/sample_flat.json`

- [ ] **Step 1: 写失败测试（词表 + 无工程量守卫 + 合成工作簿抽取）**

`tests/test_build_boq_template.py`：

```python
import importlib.util
import json
import os

import openpyxl

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "build_boq_template", os.path.join(BASE, "scripts", "build_boq_template.py"))
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

from app.core.takeoff import template as vocab  # noqa: E402


def test_normalize_unit_aliases():
    assert vocab.normalize_unit("m²") == "m2"
    assert vocab.normalize_unit("㎡ ") == "m2"
    assert vocab.normalize_unit("M2") == "m2"
    assert vocab.normalize_unit("樘") == "樘"
    assert vocab.normalize_unit("") == "项"


def test_material_of_uses_controlled_vocabulary():
    assert vocab.material_of("墙面PT-01有色乳胶漆") == "乳胶漆"
    assert vocab.material_of("主卧衣柜1") == "衣柜"
    assert vocab.material_of("无法识别的东西") is None


def test_assert_no_quantities_rejects_numbers():
    mod.assert_no_quantities({"items": [{"name": "x", "unit": "m2"}]})
    import pytest

    with pytest.raises(AssertionError):
        mod.assert_no_quantities({"items": [{"name": "x", "quantity": 3.0}]})
    with pytest.raises(AssertionError):
        mod.assert_no_quantities({"items": [{"name": "x", "nested": {"area": 1.5}}]})


def _write_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ZS02-A-精装修工程-227户型"
    ws.append(["ZS02-A-精装修工程——分部分项工程量清单计价表"])
    ws.append(["序号", "项目名称", "项目特征", "计量单位"])
    ws.append(["一", "地面装饰工程"])
    ws.append(["（一）", "地面基层"])
    ws.append(["ZS02-A-001", "地面水泥砂浆找平层", "1:3 砂浆", "m2"])
    ws.append(["（二）", "地面瓷砖"])
    ws.append(["ZS02-A-007-1", "瓷砖地面铺贴阳台", "TL-02", "m²"])
    ws.append(["ZS02-A-007-1", "瓷砖地面铺贴阳台", "TL-02", "m2"])
    ws.append(["二", "墙柱面装饰工程"])
    ws.append(["（六）", "墙柱面踢脚线"])
    ws.append(["ZS02-A-075", "踢脚线不锈钢饰面", "不锈钢", "m"])
    ws.append(["三", "天棚装饰工程"])
    ws.append(["（五）", "天棚装饰线"])
    ws.append(["", "定制GRG线条1", "", ""])
    ws.append(["ZS02-A-106", "天棚刮腻子每增减一遍", "", "m2"])

    wb_b = wb.create_sheet("ZS02-B- 精装修水电136、227户型")
    wb_b.append(["序号", "项目名称", "项目特征", "计量单位"])
    wb_b.append(["五", "暖通（已填）"])
    wb_b.append(["新增", "厨房空调", "满足设计", "台"])

    wb_d = wb.create_sheet("户内门")
    wb_d.append(["序号", "材料名称/型号", "品牌", "单位"])
    wb_d.append(["一", "明细"])
    wb_d.append(["1", "户内门", "", "樘"])

    wb.save(path)


def test_build_from_synthetic_workbook(tmp_path):
    path = tmp_path / "boq.xlsx"
    _write_workbook(path)
    out = mod.build(str(path))
    items = out["items"]
    by_name = {it["name"]: it for it in items}

    assert by_name["地面水泥砂浆找平层"]["division"] == 1
    assert by_name["地面水泥砂浆找平层"]["capability"] == "floor_area"
    assert by_name["瓷砖地面铺贴阳台"]["unit"] == "m2"
    assert len([it for it in items if it["name"] == "瓷砖地面铺贴阳台"]) == 1
    assert by_name["踢脚线不锈钢饰面"]["division"] == 5
    assert by_name["踢脚线不锈钢饰面"]["capability"] == "baseboard_length"
    assert by_name["定制GRG线条1"]["group_only"] is True
    assert by_name["天棚刮腻子每增减一遍"]["group_only"] is True
    assert by_name["厨房空调"]["division"] == 8
    assert by_name["厨房空调"]["capability"] == "hvac"
    assert by_name["户内门"]["division"] == 4
    assert by_name["户内门"]["capability"] == "opening_count"
    mod.assert_no_quantities(out)


def test_committed_sample_flat_is_sanitized():
    path = os.path.join(BASE, "app", "core", "takeoff", "templates", "sample_flat.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    mod.assert_no_quantities(data)
    assert data["type"] == "sample_flat"
    assert {it["division"] for it in data["items"]} == {1, 2, 3, 4, 5, 6, 7, 8}
    assert len(data["items"]) >= 100
    allowed = {"m2", "m3", "m", "km", "个", "套", "台", "樘", "项", "kg", "t", "组", "根"}
    assert {it["unit"] for it in data["items"]} <= allowed
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_build_boq_template.py -q`
Expected: FAIL（`ModuleNotFoundError: app.core.takeoff.template` 或脚本不存在）

- [ ] **Step 3: 写 `app/core/takeoff/template.py` 的词表部分**

```python
"""行项模板层：词表归一化、模板加载、三档漏项判定、合理性区间。

纯包：只 import 标准库与 .types；不得 import flask / app.models / modeling。
"""
from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# 单位量纲：m²/㎡ 等别名统一到 m2；空单位按「项」处理
UNIT_ALIASES = {
    "m²": "m2", "㎡": "m2", "M2": "m2", "m2": "m2",
    "m³": "m3", "M3": "m3", "m3": "m3",
    "m": "m", "M": "m", "km": "km",
    "个": "个", "套": "套", "台": "台", "樘": "樘", "项": "项",
    "kg": "kg", "t": "t", "组": "组", "根": "根",
}

# 受控材料词表（spec §5.5：DIVISIONS 材料清单 + BASE_LAYER_RULES 键 + 图纸代号）
MATERIAL_TOKENS = (
    "岩板", "瓷砖", "石材", "木地板", "木饰面", "乳胶漆", "艺术涂料", "不锈钢",
    "金属饰面", "硬包", "玻璃", "石膏板", "铝扣板", "GRG", "墙纸", "软包", "铝板",
    "自流平", "地毯", "防水",
    "马桶", "面盆", "龙头", "花洒", "浴缸", "地漏", "角阀", "毛巾架", "纸巾架",
    "喷枪", "洗衣机",
    "配电箱", "插座", "开关", "射灯", "灯带", "灯槽", "窗帘盒", "浴霸",
    "抽气扇", "风口", "风机盘管", "空调",
    "衣柜", "橱柜", "浴室柜", "玄关柜", "餐厅柜", "电视柜", "书柜", "屏风",
    "洗手台", "门套",
)

# 长词优先：避免「灯」先命中「灯带」
_MATERIALS_LONGEST_FIRST = tuple(sorted(MATERIAL_TOKENS, key=len, reverse=True))


def normalize_unit(raw):
    text = (raw or "").strip()
    if not text:
        return "项"
    return UNIT_ALIASES.get(text, text)


def material_of(name):
    text = name or ""
    for token in _MATERIALS_LONGEST_FIRST:
        if token in text:
            return token
    return None
```

- [ ] **Step 4: 写抽取脚本 `scripts/build_boq_template.py`**

```python
#!/usr/bin/env python
"""从本地 227 清单抽取样板房行项模板（只取列项，工程量列读入即丢）。

用法：
    ./venv/bin/python scripts/build_boq_template.py \
        --workbook "装修图纸/227户型dxf/金融城227户型样板房工程量清单.xlsx" \
        --out app/core/takeoff/templates/sample_flat.json

工作簿被 .gitignore 排除，不进 CI；输出 JSON 入库，CI 只读 JSON。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.takeoff.template import material_of, normalize_unit  # noqa: E402

DEFAULT_WORKBOOK = ROOT / "装修图纸" / "227户型dxf" / "金融城227户型样板房工程量清单.xlsx"
DEFAULT_OUT = ROOT / "app" / "core" / "takeoff" / "templates" / "sample_flat.json"

SHEET_MAPS = {
    "ZS02-A-精装修工程-227户型": {"一": 1, "二": 2, "三": 3, "五": 5},
    "ZS02-B- 精装修水电136、227户型": {"一": 7, "二": 8, "三": 8, "四": 8, "五": 8, "七": 7},
    "户内门": {"一": 4},
}
MODIFIER_MARKERS = ("每增减", "每增一遍", "每减一遍", "面层增减一遍")
TOTAL_NAMES = ("合计", "总合计")
FORBIDDEN_KEYS = {
    "quantity", "qty", "amount", "area", "length", "count", "total", "price",
    "工程量", "数量", "合价", "综合单价", "合计",
}
NUMERIC_OK_KEYS = {"version", "division"}

RANGES = {
    "wall_floor_ratio": [2.0, 3.0],
    "baseboard_perimeter": [0.6, 1.1],
    "light_per_area": [0.10, 0.20],
    "socket_per_area": [0.08, 0.15],
}


def _s(value):
    return "" if value is None else str(value).strip()


def _clean_name(name):
    text = _s(name)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def capability_of(division, name, unit, section_name):
    if division == 1:
        return "floor_area" if any(k in name for k in ("找平", "基层")) else "finish_surface"
    if division == 2:
        if "腻子" in name or "基层" in name:
            return "wall_base"
        return "finish_surface"
    if division == 3:
        if any(k in name for k in ("刮腻子", "涂料", "乳胶漆")):
            return "ceiling_area"
        if any(k in name for k in ("灯槽", "窗帘盒", "GRG", "凹槽", "装饰线")):
            return "ceiling_detail"
        return "finish_surface"
    if division == 4:
        return "opening_count"
    if division == 5:
        return "baseboard_length" if "踢脚" in name else "other_linear"
    if division == 6:
        return "furniture"
    if division == 7:
        return "fire" if "消防" in section_name else "plumbing"
    if division == 8:
        return "hvac" if "暖通" in section_name else "electrical"
    return "finish_surface"


def _division_for(sheet, sec, sub, name, unit):
    base = SHEET_MAPS.get(sheet, {}).get(sec)
    if base is None:
        return None
    if sheet.startswith("ZS02-A") and sec == "二" and sub == "（六）":
        return 5
    if sheet.startswith("ZS02-A") and sec == "五":
        if unit == "个":
            return 6
        if name == "美缝":
            return 5
    return base


def parse_workbook(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    items = []
    for sheet, _map in SHEET_MAPS.items():
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        sec = sub = ""
        sec_name = sub_name = ""
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=4, values_only=True):
            c0, c1 = _s(row[0]), _s(row[1])
            unit = normalize_unit(_s(row[3]) if len(row) > 3 else "")
            if re.fullmatch(r"[一二三四五六七八九十]+", c0):
                sec, sub, sec_name, sub_name = c0, "", c1, ""
                continue
            if re.fullmatch(r"（[一二三四五六七八九十]+）", c0):
                sub, sub_name = c0, c1
                continue
            name = _clean_name(c1)
            if not name or name in TOTAL_NAMES or name.startswith("项目名称"):
                continue
            division = _division_for(sheet, sec, sub, name, unit)
            if division is None:
                continue
            group_only = (not _s(row[3])) or any(m in name for m in MODIFIER_MARKERS)
            items.append({
                "division": division,
                "name": name,
                "unit": unit,
                "material": material_of(name),
                "capability": capability_of(division, name, unit, sec_name),
                "group": "%s/%s" % (sec_name, sub_name) if sub_name else sec_name,
                "group_only": group_only,
            })
    wb.close()

    unique, seen = [], set()
    for item in items:
        key = (item["division"], item["name"], item["unit"])
        if key in seen:
            continue
        seen.add(key)
        item["id"] = "%d:%s:%s" % key
        unique.append(item)
    return unique


def assert_no_quantities(obj, path="$", key=None):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_KEYS:
                raise AssertionError("模板不得含工程量字段：%s.%s" % (path, k))
            assert_no_quantities(v, "%s.%s" % (path, k), k)
        return
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            assert_no_quantities(v, "%s[%d]" % (path, i), key)
        return
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if key in NUMERIC_OK_KEYS or ".ranges" in path:
            return
        raise AssertionError("模板不得含工程量数值：%s = %r" % (path, obj))


def build(workbook_path):
    items = parse_workbook(workbook_path)
    out = {
        "type": "sample_flat",
        "version": 1,
        "origin": "金融城227样板房工程量清单 · ZS02-A-精装修工程-227户型 / "
                  "ZS02-B-精装修水电136、227户型 / 户内门（列项抽取，工程量已丢弃）",
        "source_sheets": list(SHEET_MAPS),
        "ranges": RANGES,
        "items": items,
    }
    assert_no_quantities(out)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", default=str(DEFAULT_WORKBOOK))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    data = build(args.workbook)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {}
    for item in data["items"]:
        counts[item["division"]] = counts.get(item["division"], 0) + 1
    print("wrote %s: %d items, by division %s" % (out, len(data["items"]), counts))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_build_boq_template.py -q`
Expected: 前 4 个用例 PASS；`test_committed_sample_flat_is_sanitized` FAIL（JSON 尚未生成）

- [ ] **Step 6: 生成真正的样板房模板**

Run:
```bash
./venv/bin/python scripts/build_boq_template.py
```
Expected: 打印 `wrote .../sample_flat.json: N items, by division {1: ..., 2: ..., ...}`，八个分部齐全。

- [ ] **Step 7: 复跑测试并抽查 JSON**

Run:
```bash
./venv/bin/python -m pytest tests/test_build_boq_template.py -q
./venv/bin/python - <<'PY'
import json
d=json.load(open("app/core/takeoff/templates/sample_flat.json",encoding="utf-8"))
print(len(d["items"]), d["ranges"])
print([i["name"] for i in d["items"] if i["division"]==4])
print([i["name"] for i in d["items"] if i["division"]==5])
PY
```
Expected: 全部 PASS；第 4 分部只有 `户内门`；第 5 分部含 `踢脚线不锈钢饰面`、`美缝`；JSON 无任何工程量字段。

- [ ] **Step 8: 提交**

```bash
git add app/core/takeoff/template.py scripts/build_boq_template.py \
  app/core/takeoff/templates/sample_flat.json tests/test_build_boq_template.py
git commit -m "feat(template): 抽取样板房行项范例库，工程量列读入即丢弃"
```

---

### Task 2: 模板加载、三档漏项判定与合理区间评估

**Files:**
- Modify: `app/core/takeoff/template.py`（在 Task 1 词表后追加）
- Create: `tests/test_takeoff_template.py`

**Interfaces:**
- Consumes: `sample_flat.json`；`Row` / `Coverage` / `Issue`（`.types`）
- Produces:
  - `template.load_template(project_type, templates_dir=TEMPLATES_DIR) -> dict | None`
  - `template.ranges(project_type) -> dict`
  - `template.totals(rows, metrics=None) -> dict`
  - `template.evaluate_ranges(totals, project_type) -> (list[dict], list[Issue])`
  - `template.audit(rows, coverage, project_type, metrics=None, excluded=None, templates_dir=TEMPLATES_DIR) -> (dict, list[Issue])`
  - `template.gap_rows(audit) -> list[Row]`

- [ ] **Step 1: 写失败测试**

`tests/test_takeoff_template.py`：

```python
import json
import os

from app.core.takeoff import template
from app.core.takeoff.types import Coverage

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tpl_dir(tmp_path, items, ranges=None):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "sample_flat.json").write_text(json.dumps({
        "type": "sample_flat", "version": 1, "items": items,
        "ranges": ranges or {"wall_floor_ratio": [2.0, 3.0],
                             "baseboard_perimeter": [0.6, 1.1]},
    }, ensure_ascii=False), encoding="utf-8")
    return d


def _item(**kwargs):
    base = {"id": "1:地面:m2", "division": 1, "name": "地面", "unit": "m2",
            "material": None, "capability": "floor_area", "group_only": False}
    base.update(kwargs)
    return base


def _row(div, label="行", value=1.0, unit="m2", category="dxf_floor", source="图纸实测"):
    return {"division_id": div, "label": label, "value": value, "unit": unit,
            "category": category, "source_tag": source}


def _cov(*pairs):
    cov = Coverage()
    for space, part, status in pairs:
        cov.mark(space, part, status, "dxf")
    return cov


def test_audit_true_miss_when_part_has_source(tmp_path):
    d = _tpl_dir(tmp_path, [_item()])
    audit, issues = template.audit([], _cov(("客厅", "地面", "有源")),
                                   "sample_flat", templates_dir=d)
    assert audit["counts"]["missing"] == 1
    assert audit["missing"][0]["reason"] == "真漏算"
    assert [i.code for i in issues] == ["template_item_missing"]
    assert issues[0].level == "warn"


def test_audit_no_source_when_coverage_empty(tmp_path):
    d = _tpl_dir(tmp_path, [_item()])
    audit, issues = template.audit([], _cov(), "sample_flat", templates_dir=d)
    assert audit["counts"]["no_source"] == 1
    assert audit["missing"] == []
    assert [i.code for i in issues] == ["template_no_source"]
    assert issues[0].level == "info"


def test_audit_present_when_division_row_exists(tmp_path):
    d = _tpl_dir(tmp_path, [_item()])
    audit, issues = template.audit([_row(1)], _cov(("客厅", "地面", "有源")),
                                   "sample_flat", templates_dir=d)
    assert audit["counts"]["present"] == 1
    assert audit["counts"]["missing"] == 0
    assert issues == []


def test_group_only_is_skipped(tmp_path):
    d = _tpl_dir(tmp_path, [_item(group_only=True)])
    audit, issues = template.audit([], _cov(), "sample_flat", templates_dir=d)
    assert audit["counts"]["group_only"] == 1
    assert audit["counts"]["checked"] == 0
    assert issues == []


def test_excluded_item_is_not_missing(tmp_path):
    d = _tpl_dir(tmp_path, [_item(id="1:地面:m2")])
    audit, issues = template.audit([], _cov(), "sample_flat",
                                   excluded={"1:地面:m2"}, templates_dir=d)
    assert audit["counts"]["excluded"] == 1
    assert audit["counts"]["missing"] == 0
    assert [i.code for i in issues] == ["template_item_excluded"]


def test_gap_rows_have_no_quantities(tmp_path):
    d = _tpl_dir(tmp_path, [_item(id="6:衣柜:个", division=6, name="衣柜",
                                  unit="个", capability="furniture")])
    audit, _ = template.audit([], _cov(), "sample_flat", templates_dir=d)
    rows = template.gap_rows(audit)
    assert len(rows) == 1
    assert rows[0].value is None
    assert rows[0].category == "template_gap"
    assert rows[0].source_tag == "待确认"
    assert rows[0].confidence_level == "low"


def test_evaluate_ranges_uses_perimeter_denominator():
    totals = {"floor_m2": 8.0, "wall_m2": 25.0,
              "baseboard_m": 10.0, "perimeter_m": 16.0}
    rows, issues = template.evaluate_ranges(totals, "showroom")
    by = {r["code"]: r for r in rows}
    assert by["wall_floor_ratio"]["value"] == 3.12
    assert by["baseboard_ratio"]["value"] == 0.62
    assert by["baseboard_ratio"]["in_band"] is True
    assert [i.code for i in issues] == ["wall_floor_ratio"]


def test_evaluate_ranges_skips_density_without_rows():
    rows, issues = template.evaluate_ranges(
        {"floor_m2": 10.0, "wall_m2": 25.0, "baseboard_m": 8.0,
         "perimeter_m": 10.0}, "sample_flat")
    assert {r["code"] for r in rows} == {"wall_floor_ratio", "baseboard_ratio"}
    assert issues == []


def test_sample_flat_ranges_come_from_json():
    assert template.ranges("sample_flat")["wall_floor_ratio"] == [2.0, 3.0]
    assert template.ranges("unknown_type")["wall_floor_ratio"] == [1.5, 4.0]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_template.py -q`
Expected: FAIL（`AttributeError: module ... has no attribute 'audit'`）

- [ ] **Step 3: 在 `template.py` 追加加载/判定/区间代码**

```python
import json

from .types import Issue, Row

BUILTIN_RANGES = {
    "showroom": {
        "wall_floor_ratio": [2.5, 3.5],
        "baseboard_perimeter": [0.6, 1.1],
        "light_per_area": [0.08, 0.15],
        "socket_per_area": [0.05, 0.10],
    },
    "default": {
        "wall_floor_ratio": [1.5, 4.0],
        "baseboard_perimeter": [0.6, 1.1],
        "light_per_area": [0.05, 0.30],
        "socket_per_area": [0.05, 0.20],
    },
}

GEOMETRY_CAPS = {"floor_area", "wall_base", "ceiling_area",
                 "opening_count", "baseboard_length"}
CAP_PARTS = {
    "floor_area": ("地面",),
    "wall_base": ("墙面",),
    "ceiling_area": ("天棚",),
    "baseboard_length": ("踢脚",),
    "opening_count": ("洞口",),
}
LIGHT_TOKENS = ("灯",)
SOCKET_TOKENS = ("插座", "开关")
DENSITY_SKIP_SOURCES = ("经验补全", "历史参照", "待确认")


def _get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def template_path(project_type, templates_dir=TEMPLATES_DIR):
    if not project_type or not re.fullmatch(r"[a-z_]+", project_type):
        return None
    return Path(templates_dir) / ("%s.json" % project_type)


def load_template(project_type, templates_dir=TEMPLATES_DIR):
    path = template_path(project_type, templates_dir)
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def ranges(project_type):
    tpl = load_template(project_type)
    if tpl and isinstance(tpl.get("ranges"), dict):
        return tpl["ranges"]
    return BUILTIN_RANGES.get(project_type or "", BUILTIN_RANGES["default"])


def totals(rows, metrics=None):
    m = metrics or {}

    def total(key, div):
        if m.get(key) is not None:
            return float(m[key] or 0.0)
        return sum(float(_get(r, "value") or 0.0) for r in rows
                   if int(_get(r, "division_id", 0) or 0) == div)

    def counted(key, tokens):
        if m.get(key) is not None:
            return float(m[key] or 0.0)
        value = 0.0
        for r in rows:
            if int(_get(r, "division_id", 0) or 0) != 8:
                continue
            if (_get(r, "source_tag", "") or "") in DENSITY_SKIP_SOURCES:
                continue
            label = _get(r, "label", "") or ""
            if any(t in label for t in tokens):
                value += float(_get(r, "value") or 0.0)
        return value

    return {
        "floor_m2": total("floor_m2", 1),
        "wall_m2": total("wall_m2", 2),
        "baseboard_m": total("baseboard_m", 5),
        "perimeter_m": float(m.get("perimeter_m") or 0.0),
        "light_count": counted("light_count", LIGHT_TOKENS),
        "socket_count": counted("socket_count", SOCKET_TOKENS),
    }


def _fmt(value):
    return "%.1f" % float(value)


def evaluate_ranges(totals_map, project_type):
    bands = ranges(project_type)
    out, issues = [], []

    def add(code, key, label, value, unit=""):
        band = bands.get(key)
        if not band:
            return
        lo, hi = band[0], band[1]
        value = round(float(value), 2)
        in_band = (lo is None or value >= lo) and (hi is None or value <= hi)
        out.append({"code": code, "label": label, "value": value,
                    "band": [lo, hi], "in_band": in_band})
        if not in_band:
            issues.append(Issue(
                code, "warn",
                "%s=%.2f%s，参考范围%s~%s（业态 %s），请核查"
                % (label, value, unit, _fmt(lo), _fmt(hi), project_type or "未指定"),
                {"ratio": value, "band": [lo, hi], "project_type": project_type}))

    floor = float(totals_map.get("floor_m2") or 0.0)
    wall = float(totals_map.get("wall_m2") or 0.0)
    baseboard = float(totals_map.get("baseboard_m") or 0.0)
    perimeter = float(totals_map.get("perimeter_m") or 0.0)
    if floor > 0 and wall > 0:
        add("wall_floor_ratio", "wall_floor_ratio", "项目墙地比", wall / floor)
    if perimeter > 0 and baseboard > 0:
        add("baseboard_ratio", "baseboard_perimeter",
            "项目踢脚/围合周长比", baseboard / perimeter)
    if floor > 0 and totals_map.get("light_count"):
        add("light_per_area", "light_per_area", "灯具密度",
            float(totals_map["light_count"]) / floor, "个/m²")
    if floor > 0 and totals_map.get("socket_count"):
        add("socket_per_area", "socket_per_area", "插座密度",
            float(totals_map["socket_count"]) / floor, "个/m²")
    return out, issues


def _add_no_source(buckets, item):
    division = int(item.get("division") or 0)
    bucket = buckets.setdefault(division, {
        "division": division, "count": 0, "items": []})
    bucket["count"] += 1
    bucket["items"].append({
        "id": item.get("id"), "name": item.get("name"),
        "unit": item.get("unit"), "capability": item.get("capability"),
        "material": item.get("material")})


def audit(rows, coverage, project_type, metrics=None, excluded=None,
          templates_dir=TEMPLATES_DIR):
    tpl = load_template(project_type, templates_dir)
    if not tpl:
        return ({"type": project_type or "", "available": False,
                 "counts": {"checked": 0, "present": 0, "missing": 0,
                            "no_source": 0, "excluded": 0, "group_only": 0},
                 "missing": [], "no_source": [], "gap_items": [], "ranges": []},
                [Issue("template_not_found", "info",
                       "业态 %s 暂无范例库，未做漏项审计（不影响算量）" % (project_type or "未指定"),
                       {"project_type": project_type})])

    excluded = set(excluded or ())
    counts = {"checked": 0, "present": 0, "missing": 0,
              "no_source": 0, "excluded": 0, "group_only": 0}
    missing, no_source, issues = [], {}, []
    cells = (coverage.cells if coverage is not None else {}) or {}

    for item in tpl.get("items") or []:
        if item.get("group_only"):
            counts["group_only"] += 1
            continue
        iid = item.get("id")
        if iid in excluded:
            counts["excluded"] += 1
            issues.append(Issue(
                "template_item_excluded", "info",
                "模板列项已人工排除：%s" % item.get("name"),
                {"id": iid, "division": item.get("division")}))
            continue

        counts["checked"] += 1
        division = int(item.get("division") or 0)
        capability = item.get("capability") or ""
        has_rows = any(
            int(_get(r, "division_id", 0) or 0) == division
            and _get(r, "value") is not None
            for r in rows)

        if capability in GEOMETRY_CAPS:
            parts = CAP_PARTS.get(capability, ())
            has_source = any(
                cell.get("status") == "有源"
                for (_space, part), cell in cells.items() if part in parts)
            if has_rows:
                counts["present"] += 1
            elif has_source:
                counts["missing"] += 1
                message = ("模板列项未计量：%s（第%d分部，覆盖矩阵该部位有源）"
                           % (item.get("name"), division))
                missing.append({
                    "id": iid, "division": division, "name": item.get("name"),
                    "unit": item.get("unit"), "reason": "真漏算",
                    "message": message})
                issues.append(Issue(
                    "template_item_missing", "warn", message,
                    {"id": iid, "division": division,
                     "capability": capability}))
            else:
                counts["no_source"] += 1
                _add_no_source(no_source, item)
        else:
            material = item.get("material")
            matched = bool(material) and any(
                int(_get(r, "division_id", 0) or 0) == division
                and material in (_get(r, "label", "") or "")
                for r in rows)
            if matched:
                counts["present"] += 1
            else:
                counts["no_source"] += 1
                _add_no_source(no_source, item)

    no_source_rows = [no_source[k] for k in sorted(no_source)]
    for bucket in no_source_rows:
        names = [it["name"] for it in bucket["items"]]
        issues.append(Issue(
            "template_no_source", "info",
            "第%d分部有 %d 个模板列项无图源：%s%s"
            % (bucket["division"], bucket["count"], "、".join(names[:5]),
               "…" if len(names) > 5 else ""),
            {"division": bucket["division"], "count": bucket["count"],
             "names": names}))

    range_rows, range_issues = evaluate_ranges(totals(rows, metrics), project_type)
    issues.extend(range_issues)
    if "waterproof_ratio" not in ranges(project_type):
        issues.append(Issue(
            "waterproof_range_disabled", "info",
            "防水比值需要防水上翻高度来源，本轮未启用该判据",
            {"project_type": project_type}))

    gap_items = [dict(it, division=bucket["division"])
                 for bucket in no_source_rows for it in bucket["items"]]
    return ({
        "type": tpl.get("type") or project_type,
        "version": tpl.get("version"),
        "available": True,
        "counts": counts,
        "missing": missing,
        "no_source": no_source_rows,
        "gap_items": gap_items,
        "ranges": range_rows,
    }, issues)


def gap_rows(audit_map):
    rows = []
    for bucket in audit_map.get("no_source") or []:
        for item in bucket.get("items") or []:
            rows.append(Row(
                int(bucket["division"]), item["name"], "template_gap",
                item.get("unit") or "项", None,
                "范例库列项：本图无图源，工程量待补",
                "（范例库列项，待算）",
                remarks="来自 %s 范例库；缺图源，未计量，禁止直接用于报价"
                        % (audit_map.get("type") or ""),
                source_tag="待确认", confidence_level="low",
                project_features=item.get("material") or ""))
    return rows
```

同时在文件顶部补 `import re`（`template_path` 使用）。

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_takeoff_template.py tests/test_build_boq_template.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/core/takeoff/template.py tests/test_takeoff_template.py
git commit -m "feat(template): 三档漏项判定与合理区间评估"
```

---

### Task 3: 踢脚比改围合周长 + metrics transport + 区间唯一来源

**Files:**
- Modify: `app/core/takeoff/types.py`（`TakeoffResult.metrics`）
- Modify: `app/core/takeoff/takeoff.py`（`rows_for_divisions` 汇总 metrics）
- Modify: `app/core/takeoff/__init__.py`（跨图汇总、传给 reconcile、写入结果）
- Rewrite: `app/core/takeoff/reconcile.py`
- Modify: `tests/test_takeoff_rows.py`（reconcile 测试改用 metrics）
- Modify: `tests/test_takeoff_web.py`（227 指标断言）

**Interfaces:**
- Consumes: `template.totals` / `template.evaluate_ranges`（Task 2）
- Produces:
  - `Row` 的 `check` 键：`wall_floor_ratio`（div2）、`baseboard_ratio`（div5）、`light_per_area` / `socket_per_area`（div8 匹配行）
  - `TakeoffResult.metrics`：`{floor_m2, wall_m2, ceiling_m2, baseboard_m, perimeter_m, opening_count}`
  - `reconcile.measurement_view(measurement) -> dict`
  - `reconcile.apply(rows, params=None, metrics=None) -> list[Issue]`

- [ ] **Step 1: 写失败测试（先改测试既定期望）**

`tests/test_takeoff_rows.py` 中替换三个 reconcile 用例：

```python
def test_reconcile_writes_two_bands_from_metrics(tmp_path):
    """两条带：墙地比 4.15、踢脚/周长 0.50 均越带；只写 check，不改 value、不增行。"""
    data = _data(tmp_path)
    rows, _ = rows_for_divisions(data, TakeoffParams(floor_height_mm=2800.0))
    assert len(rows) == 13
    before = [r.value for r in rows]
    metrics = {"floor_m2": 19.73, "wall_m2": 81.88,
               "baseboard_m": 29.02, "perimeter_m": 58.0}
    issues = reconcile_apply(rows, metrics=metrics)
    assert {i.code for i in issues} == {"wall_floor_ratio", "baseboard_ratio"}

    wall = [r for r in rows if r.division_id == 2]
    bb = [r for r in rows if r.division_id == 5]
    assert all(r.check["wall_floor_ratio"] == 4.15 for r in wall)
    assert all(r.check["baseboard_ratio"] == 0.5 for r in bb)
    assert [r.value for r in rows] == before
    assert len(rows) == 13

    by_code = {i.code: i for i in issues}
    assert by_code["wall_floor_ratio"].detail["band"] == [2.0, 3.0]
    assert by_code["baseboard_ratio"].detail["band"] == [0.6, 1.1]
    assert "4.15" in by_code["wall_floor_ratio"].message
    assert "0.50" in by_code["baseboard_ratio"].message


def test_reconcile_band_respects_project_type(tmp_path):
    data = _data(tmp_path)
    rows, _ = rows_for_divisions(data, TakeoffParams(floor_height_mm=2800.0))
    metrics = {"floor_m2": 19.73, "wall_m2": 81.88,
               "baseboard_m": 29.02, "perimeter_m": 37.0}
    issues = reconcile_apply(rows, TakeoffParams(project_type="showroom"),
                             metrics=metrics)
    wf = [i for i in issues if i.code == "wall_floor_ratio"][0]
    assert wf.detail["project_type"] == "showroom"
    assert wf.detail["band"] == [2.5, 3.5]

    synth = [
        Row(1, "地面", "dxf_floor", "m2", 10.0, "f"),
        Row(2, "墙面", "dxf_wall", "m2", 30.0, "f", check={"gross_m2": 30.0}),
        Row(5, "踢脚", "dxf_baseboard", "m", 8.0, "f"),
    ]
    assert reconcile_apply(
        synth, TakeoffParams(project_type="showroom"),
        metrics={"floor_m2": 10, "wall_m2": 30,
                 "baseboard_m": 8, "perimeter_m": 10}) == []
    assert synth[1].check["wall_floor_ratio"] == 3.0
    assert synth[2].check["baseboard_ratio"] == 0.8


def test_reconcile_baseboard_uses_perimeter_not_floor():
    """旧口径 10/8=1.25 会越带；新口径 10/15=0.67 落带内。"""
    rows = [
        Row(1, "地面", "dxf_floor", "m2", 8.0, "f"),
        Row(5, "踢脚", "dxf_baseboard", "m", 10.0, "f"),
    ]
    issues = reconcile_apply(
        rows, metrics={"floor_m2": 8, "baseboard_m": 10, "perimeter_m": 15})
    assert issues == []
    assert rows[1].check["baseboard_ratio"] == 0.67
```

`test_reconcile_ratio_rounded_before_band_check` 改为：
```python
def test_reconcile_ratio_rounded_before_band_check():
    def synth(wall_value):
        return [Row(1, "地面", "dxf_floor", "m2", 10.0, "f"),
                Row(2, "墙面", "dxf_wall", "m2", wall_value, "f")]

    in_band = synth(30.04)
    assert reconcile_apply(
        in_band, metrics={"floor_m2": 10, "wall_m2": 30.04}) == []
    assert in_band[1].check["wall_floor_ratio"] == 3.0

    out_band = synth(30.06)
    issues = reconcile_apply(
        out_band, metrics={"floor_m2": 10, "wall_m2": 30.06})
    wf = [i for i in issues if i.code == "wall_floor_ratio"][0]
    assert wf.detail["ratio"] == 3.01
    assert "3.01" in wf.message
    assert out_band[1].check["wall_floor_ratio"] == 3.01
```

`test_reconcile_empty_and_zero_totals_short_circuit` 改为：
```python
def test_reconcile_empty_and_zero_totals_short_circuit():
    assert reconcile_apply([]) == []

    no_floor = [Row(2, "墙面", "dxf_wall", "m2", 30.0, "f"),
                Row(5, "踢脚", "dxf_baseboard", "m", 8.0, "f")]
    assert reconcile_apply(
        no_floor, metrics={"floor_m2": 0, "baseboard_m": 8,
                           "perimeter_m": 10}) == []
    assert no_floor[0].check is None and no_floor[1].check is None

    no_baseboard = [Row(1, "地面", "dxf_floor", "m2", 10.0, "f"),
                    Row(2, "墙面", "dxf_wall", "m2", 25.0, "f",
                        check={"gross_m2": 25.0})]
    assert reconcile_apply(
        no_baseboard, metrics={"floor_m2": 10, "wall_m2": 25}) == []
    assert no_baseboard[1].check == {"gross_m2": 25.0, "wall_floor_ratio": 2.5}
    assert "baseboard_ratio" not in no_baseboard[1].check
```

在 `tests/test_takeoff_web.py` 的 `test_web_chain_227_sealed_values` 末尾追加：
```python
        takeoff = latest_takeoff_summary(pid)
        metrics = takeoff["metrics"]
        assert abs(metrics["perimeter_m"] - 187.04) <= 0.05
        assert abs(metrics["baseboard_m"] - 166.73) <= 0.01
        ratio = round(metrics["baseboard_m"] / metrics["perimeter_m"], 2)
        assert ratio == 0.89
```
并在该测试所在作用域导入 `latest_takeoff_summary`（若未导入）。

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_rows.py -q -k reconcile`
Expected: FAIL（`TypeError: apply() got an unexpected keyword argument 'metrics'`）

- [ ] **Step 3: `types.py` 增加 metrics 字段**

```python
@dataclass
class TakeoffResult:
    rows: list
    coverage: Coverage
    issues: list
    metrics: dict = field(default_factory=dict)
```

- [ ] **Step 4: `takeoff.py` 汇总 metrics**

在 `rows_for_divisions` 开头：
```python
    metrics = {"floor_m2": 0.0, "wall_m2": 0.0, "ceiling_m2": 0.0,
               "baseboard_m": 0.0, "perimeter_m": 0.0, "opening_count": 0.0}
```
地面行后：`metrics["floor_m2"] += area`
天棚行后：`metrics["ceiling_m2"] += area`
`if residual: ... continue` 之后（非残条房间）：`metrics["perimeter_m"] += per / 1e3`
墙面行后：`metrics["wall_m2"] += round(net, 2)`
踢脚行内：
```python
        if bb > 0:
            value = round(bb, 2)
            metrics["baseboard_m"] += value
            rows.append(Row(
                5, "%s-踢脚线" % name, "dxf_baseboard", "m", value, ...
```
洞口去重后：`metrics["opening_count"] = float(len(uniq))`
`return rows, issues` 前：`data["takeoff_metrics"] = metrics`

- [ ] **Step 5: `__init__.py` 跨图汇总并传给 reconcile**

`run()` 内初始化：
```python
    metrics = {"floor_m2": 0.0, "wall_m2": 0.0, "ceiling_m2": 0.0,
               "baseboard_m": 0.0, "perimeter_m": 0.0, "opening_count": 0.0}
```
`rows, row_issues = takeoff.rows_for_divisions(...)` 后：
```python
            drawing_metrics = data.get("takeoff_metrics") or {}
            for key in metrics:
                metrics[key] += float(drawing_metrics.get(key) or 0.0)
```
末尾：
```python
    issues.extend(reconcile.apply(all_rows, params=params, metrics=metrics))
    return TakeoffResult(rows=all_rows,
                         coverage=seed_coverage or Coverage(),
                         issues=issues,
                         metrics=metrics)
```
两个早退分支保持构造 `TakeoffResult(..., metrics={})`（dataclass 默认即可）。

- [ ] **Step 6: 重写 `reconcile.py`**

```python
"""两条判定带对账：墙地比 + 踢脚/围合周长比。

只写 row.check 与 Issue——不新增任何数字行、不改 row.value。
带值唯一来源是 template.ranges（样板房取 templates/sample_flat.json，
未知业态走 template.BUILTIN_RANGES 兜底），本模块不保留硬编码区间。

踢脚分母是**参与踢脚的房间围合周长**（残条不计），不是地面面积：
227 实测 166.73 / 187.04 = 0.89。metrics 由 rows_for_divisions 汇总传入；
缺 perimeter 时跳过踢脚判定，不用错误分母凑结论。
"""
from __future__ import annotations

from . import template
from .types import Issue

_CHECK_DIV = {"wall_floor_ratio": 2, "baseboard_ratio": 5}
_LIGHT_TOKENS = ("灯",)
_SOCKET_TOKENS = ("插座", "开关")


def _get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _check_of(row):
    if row.check is None:
        row.check = {}
    return row.check


def measurement_view(m):
    """ORM Measurement → 纯 dict（只取判定需要的字段）。"""
    unit = (getattr(m, "unit", "") or "").strip()
    value = getattr(m, "area", None)
    if unit == "m":
        value = getattr(m, "length", None)
    elif unit not in ("m2", "m²"):
        value = getattr(m, "count", None)
    return {"division_id": getattr(m, "division_id", 0) or 0,
            "label": getattr(m, "label", "") or "",
            "category": getattr(m, "category", "") or "",
            "source_tag": getattr(m, "source_tag", "") or "",
            "unit": unit, "value": value}


def apply(rows, params=None, metrics=None):
    project_type = getattr(params, "project_type", None) or "sample_flat"
    results, issues = template.evaluate_ranges(
        template.totals(rows, metrics), project_type)
    by_code = {item["code"]: item for item in results}

    for row in rows:
        division = int(_get(row, "division_id", 0) or 0)
        for code, check_key in (("wall_floor_ratio", "wall_floor_ratio"),
                                ("baseboard_ratio", "baseboard_ratio")):
            if _CHECK_DIV[code] == division and code in by_code:
                _check_of(row)[check_key] = by_code[code]["value"]
        if division == 8:
            label = _get(row, "label", "") or ""
            if "light_per_area" in by_code and any(t in label for t in _LIGHT_TOKENS):
                _check_of(row)["light_per_area"] = by_code["light_per_area"]["value"]
            if "socket_per_area" in by_code and any(t in label for t in _SOCKET_TOKENS):
                _check_of(row)["socket_per_area"] = by_code["socket_per_area"]["value"]
    return issues
```

- [ ] **Step 7: 运行相关测试**

Run:
```bash
./venv/bin/python -m pytest tests/test_takeoff_rows.py tests/test_takeoff_template.py -q
./venv/bin/python -m pytest tests/test_takeoff_web.py -q -k "227 or reconcile_band"
```
Expected: PASS（227 的 `metrics.perimeter_m` ≈ 187.04、ratio 0.89）

- [ ] **Step 8: 提交**

```bash
git add app/core/takeoff/types.py app/core/takeoff/takeoff.py \
  app/core/takeoff/__init__.py app/core/takeoff/reconcile.py \
  tests/test_takeoff_rows.py tests/test_takeoff_web.py
git commit -m "feat(takeoff): 踢脚比改用围合周长，区间统一由模板提供"
```

---

### Task 4: Phase 5 接线与披露持久化

**Files:**
- Modify: `app/processing.py`
- Modify: `tests/test_takeoff_web.py`

**Interfaces:**
- Consumes: `template.audit`（Task 2）、`TakeoffResult.metrics`（Task 3）
- Produces:
  - `process_project` 返回 `takeoff` 新增 `metrics` 与 `template`
  - issues 新增 `template_item_missing` / `template_no_source` / `template_not_found` / `waterproof_range_disabled`

- [ ] **Step 1: 写失败测试**

在 `tests/test_takeoff_web.py` 追加：

```python
def test_phase5_persists_template_audit_without_adding_rows(web_env, tmp_path):
    """Phase 5 只披露：Measurement 行数与分部合计不变，summary 带 template 审计。"""
    app, pid, _ = web_env
    _copy_plan(tmp_path, app)
    with app.app_context():
        from app import db
        from app.models import Measurement, Project
        from app.processing import latest_takeoff_summary, process_project

        _add_dxf_drawing(pid)
        p = Project.query.get(pid)
        process_project(p, p.drawings.all())
        n1 = Measurement.query.filter_by(project_id=pid).count()
        s1 = _division_sums(pid)

        takeoff = latest_takeoff_summary(pid)
        audit = takeoff["template"]
        assert audit["available"] is True
        assert audit["counts"]["checked"] > 0
        assert audit["counts"]["missing"] == 0
        assert audit["counts"]["no_source"] > 0          # 第 6 分部等无图源
        assert takeoff["metrics"]["floor_m2"] > 0
        assert any(i["code"] == "template_no_source" for i in takeoff["issues"])
        assert not any(i["code"] == "template_item_missing" for i in takeoff["issues"])

        process_project(p, p.drawings.all())
        assert Measurement.query.filter_by(project_id=pid).count() == n1
        s2 = _division_sums(pid)
        for div in (1, 2, 3, 5):
            assert abs(s2[div] - s1[div]) < 1e-6
        assert s2[4] == s1[4]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_web.py::test_phase5_persists_template_audit_without_adding_rows -q`
Expected: FAIL（`KeyError: 'template'`）

- [ ] **Step 3: `processing.py` 增加 Phase 5 与辅助函数**

顶部 import：
```python
from app.core.takeoff import (
    Issue, clear_previous_measurements, params_from_project, reconcile_pinned,
    run as takeoff_run,
)
from app.core.takeoff import template as takeoff_template
```

在模块内、`process_project` 之前加：

```python
def _measurement_views(measurements):
    from app.core.takeoff.reconcile import measurement_view
    return [measurement_view(m) for m in measurements]


def _excluded_template_items(project):
    raw = getattr(project, "takeoff_params", None)
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return set()
    items = data.get("excluded_template_items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return set()
    return {str(item) for item in items}


def _merge_issues(target, extra):
    seen = {(i.code, json.dumps(i.detail, sort_keys=True, ensure_ascii=False))
            for i in target}
    for issue in extra:
        key = (issue.code, json.dumps(issue.detail, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        target.append(issue)
        seen.add(key)
```

在 Phase 4 之后、生成标注图之前插入：

```python
    # Phase 5: 模板漏项与合理性（只披露，不新增/不改数值行）
    template_audit, template_issues = takeoff_template.audit(
        _measurement_views(dxf_measurements + all_measurements),
        dxf_result.coverage,
        project.project_type,
        metrics=dxf_result.metrics,
        excluded=_excluded_template_items(project),
    )
    _merge_issues(dxf_result.issues, template_issues)
```

返回的 takeoff 增加：
```python
        'metrics': dxf_result.metrics,
        'template': template_audit,
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_takeoff_web.py::test_phase5_persists_template_audit_without_adding_rows tests/test_takeoff_web.py::test_process_twice_does_not_accumulate -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/processing.py tests/test_takeoff_web.py
git commit -m "feat(takeoff): Phase 5 接线模板漏项审计并随报告持久化"
```

---

### Task 5: auto-fill 重定义为「加入待算清单」

**Files:**
- Modify: `app/comparison.py:389-426`（`auto_fill_missing`）
- Modify: `app/core/takeoff/__init__.py`（`clear_previous_measurements` 跳过 `template_gap`）
- Modify: `app/api.py`（`/api/historical/auto-fill/<id>` 不再强制 historical_id）
- Create: `tests/test_comparison_template.py`
- Modify: `app/templates/project_detail.html`（按钮文案）

**Interfaces:**
- Consumes: `audit['gap_items']` / 历史对照 `missing_items`
- Produces: `auto_fill_missing(project_id, items) -> list[Measurement]`，补入行量全 None

- [ ] **Step 1: 写失败测试**

`tests/test_comparison_template.py`：

```python
import os
import shutil
import sys
from types import SimpleNamespace

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)

from tests.test_takeoff_web import web_env  # noqa: E402,F401


def test_auto_fill_rows_have_no_quantities_and_keep_totals(web_env):
    app, pid, _ = web_env
    with app.app_context():
        from app import db
        from app.comparison import auto_fill_missing
        from app.core.takeoff import clear_previous_measurements
        from app.models import Measurement, Project

        db.session.add(Measurement(
            label="客厅-地面", division_id=1, category="dxf_floor",
            area=10.0, unit="m²", source_tag="图纸实测",
            confidence_level="high", project_id=pid))
        db.session.commit()

        items = [{"division": 6, "name": "主卧衣柜", "unit": "个",
                  "material": "衣柜", "capability": "furniture"}]
        filled = auto_fill_missing(pid, items)
        assert len(filled) == 1
        m = filled[0]
        assert m.area is None and m.length is None and m.count is None
        assert m.category == "template_gap"
        assert m.source_tag == "待确认"
        assert m.confidence_level == "low"
        assert m.unit == "个"

        floor = sum(x.area or 0 for x in Measurement.query.filter_by(
            project_id=pid, division_id=1).all())
        assert floor == 10.0

        assert auto_fill_missing(pid, items) == []       # 幂等

        clear_previous_measurements(Project.query.get(pid))
        assert Measurement.query.filter_by(
            project_id=pid, category="template_gap").count() == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_comparison_template.py -q`
Expected: FAIL（补入行 `count=1` 或重算被清除）

- [ ] **Step 3: 重写 `auto_fill_missing`**

```python
def auto_fill_missing(project_id, missing_items):
    """把范例库缺列项加入「待算清单」：只写列项，任何工程量都不写。

    缺陷 #1 回归：历史表某分部有 N 行 ≠ 本项 N 个，禁止再把行数当 count。
    补入行 category='template_gap'，重算不清除（clear_previous_measurements 跳过）。
    """
    filled = []
    for item in missing_items or []:
        division_id = int(item.get("division") or item.get("division_id") or 0)
        name = (item.get("name") or "").strip()
        if not division_id or not name:
            continue
        existing = Measurement.query.filter_by(
            project_id=project_id, division_id=division_id, label=name).first()
        if existing:
            continue
        m = Measurement(
            label=name,
            division_id=division_id,
            category='template_gap',
            area=None,
            length=None,
            count=None,
            unit=item.get('unit') or '项',
            source_tag='待确认',
            source_tag_icon='❓',
            confidence_level='low',
            confidence_icon='🔴',
            confidence=0.0,
            position_desc='（范例库列项，待算）',
            formula_text='范例库列项：本图无图源，工程量待补',
            project_features=item.get('material') or '',
            remarks='来自样板房范例库；缺图源，未计量，禁止直接用于报价',
            project_id=project_id,
        )
        db.session.add(m)
        filled.append(m)
    db.session.commit()
    return filled
```

同时删除 `_find_item_quantity`（无调用点）。

- [ ] **Step 4: 重算保护模板补入行**

`app/core/takeoff/__init__.py::clear_previous_measurements` 的循环内加：
```python
        if (m.category or "") == "template_gap":
            continue
```

- [ ] **Step 5: API 路由去掉 historical_id 强制**

`app/api.py::auto_fill_missing`：
```python
    data = request.get_json() or {}
    items_to_fill = data.get('items', [])
    if not items_to_fill:
        return jsonify({'success': False, 'error': '缺少待填项目'}), 400
```
删除 `historical_id` 变量与其校验。

- [ ] **Step 6: 按钮文案**

`app/templates/project_detail.html:116`：
```html
                    🔄 补入遗漏项目
```
改为：
```html
                    ➕ 加入待算清单
```
同文件 100 行说明改为：
```html
                        <p class="text-muted small">上传同类项目的工程量清单Excel，系统将自动比对；缺图源的列项只加入待算清单，不自动补工程量。</p>
```

- [ ] **Step 7: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_comparison_template.py tests/test_takeoff_web.py -q -k "template or process_twice"`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add app/comparison.py app/core/takeoff/__init__.py app/api.py \
  app/templates/project_detail.html tests/test_comparison_template.py
git commit -m "fix(comparison): auto-fill 只加入待算清单，禁止把行数当工程量"
```

---

### Task 6: 清单页与报告的「疑似漏项 / 指标越界」面板

**Files:**
- Modify: `app/routes.py`（`view_boq` 传 `template_audit`；新增 `fill_template_gaps`）
- Modify: `app/templates/boq.html`
- Modify: `app/templates/report_export.html`
- Modify: `tests/test_takeoff_web.py`

**Interfaces:**
- Consumes: `summary.takeoff.template` / `latest_takeoff_summary`
- Produces: 面板 + `POST /project/<id>/template-gaps/fill`

- [ ] **Step 1: 写失败测试**

```python
def test_boq_page_and_report_show_template_panel(web_env, tmp_path):
    app, pid, _ = web_env
    _copy_plan(tmp_path, app)
    with app.app_context():
        from app.models import Project, Report
        from app.processing import process_project

        _add_dxf_drawing(pid)
        p = Project.query.get(pid)
        process_project(p, p.drawings.all())
        report = Report.query.filter_by(project_id=pid).first()
        html = open(report.file_path, encoding="utf-8").read()
        assert "疑似漏项 / 指标越界" in html
        assert "缺图源" in html
        assert "合理性指标" in html

    client = _login_client(app)
    page = client.get(f"/project/{pid}/boq")
    body = page.get_data(as_text=True)
    assert "疑似漏项 / 指标越界" in body
    assert "加入待算清单" in body


def test_fill_template_gaps_route_adds_rows_without_quantities(web_env, tmp_path):
    app, pid, _ = web_env
    _copy_plan(tmp_path, app)
    with app.app_context():
        from app.models import Measurement, Project
        from app.processing import process_project

        _add_dxf_drawing(pid)
        p = Project.query.get(pid)
        process_project(p, p.drawings.all())
        before = Measurement.query.filter_by(project_id=pid).count()

    client = _login_client(app)
    resp = client.post(f"/project/{pid}/template-gaps/fill", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        from app.models import Measurement

        gaps = Measurement.query.filter_by(
            project_id=pid, category="template_gap").all()
        assert gaps
        assert all(g.area is None and g.length is None and g.count is None for g in gaps)
        assert Measurement.query.filter_by(project_id=pid).count() == before + len(gaps)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_takeoff_web.py -q -k template_panel`
Expected: FAIL（页面无面板）

- [ ] **Step 3: `routes.py` 传参并新增补行路由**

`view_boq` 的 `render_template` 增加：
```python
                           template_audit=takeoff.get('template'),
```
文件末尾（或 `view_boq` 之后）新增：
```python
@main_bp.route('/project/<int:project_id>/template-gaps/fill', methods=['POST'])
@login_required
def fill_template_gaps(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != current_user.id and not current_user.is_admin:
        flash('无权操作此项目。', 'danger')
        return redirect(url_for('main.dashboard'))

    from app.comparison import auto_fill_missing
    from app.processing import latest_takeoff_summary

    gaps = (latest_takeoff_summary(project.id).get('template') or {}).get('gap_items') or []
    filled = auto_fill_missing(project.id, gaps)
    flash('已加入待算清单 %d 项（量为空，不计入分部合计）。' % len(filled), 'success')
    return redirect(url_for('main.view_boq', project_id=project.id))
```

- [ ] **Step 4: `boq.html` 加面板**

在覆盖矩阵 card 之后、`{% if not measurements %}` 之前插入：

```html
{% if template_audit %}
<div class="card">
    <div class="card-header"><h3>疑似漏项 / 指标越界</h3></div>
    <div class="card-body">
        {% if not template_audit.available %}
        <p class="text-muted">该业态暂无范例库，未做漏项审计（不影响算量）。</p>
        {% else %}
        <p class="text-muted">
            范例库 {{ template_audit.type }} v{{ template_audit.version }} ·
            已核对 {{ template_audit.counts.checked }} 项 ·
            真漏算 <strong>{{ template_audit.counts.missing }}</strong> ·
            缺图源 <strong>{{ template_audit.counts.no_source }}</strong> ·
            人工排除 {{ template_audit.counts.excluded }}
        </p>

        {% if template_audit.missing %}
        <h4>真漏算（该部位有图源但无行）</h4>
        <div class="table-responsive">
            <table class="table">
                <thead><tr><th>分部</th><th>列项</th><th>单位</th><th>说明</th></tr></thead>
                <tbody>
                    {% for item in template_audit.missing %}
                    <tr>
                        <td>第{{ item.division }}分部</td>
                        <td>{{ item.name }}</td>
                        <td>{{ item.unit }}</td>
                        <td class="text-muted">{{ item.message }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endif %}

        {% if template_audit.no_source %}
        <h4>缺图源（加入待算清单，不补数）</h4>
        {% for bucket in template_audit.no_source %}
        <details>
            <summary>第{{ bucket.division }}分部 · {{ bucket.count }} 项</summary>
            <ul>
                {% for item in bucket["items"] %}
                <li>{{ item.name }}（{{ item.unit }}）</li>
                {% endfor %}
            </ul>
        </details>
        {% endfor %}
        <form method="post" action="{{ url_for('main.fill_template_gaps', project_id=project.id) }}">
            <button class="btn btn-outline btn-sm" type="submit">➕ 加入待算清单</button>
        </form>
        {% endif %}

        {% if template_audit.ranges %}
        <h4>合理性指标</h4>
        <div class="table-responsive">
            <table class="table">
                <thead><tr><th>指标</th><th>当前值</th><th>参考带</th><th>结论</th></tr></thead>
                <tbody>
                    {% for item in template_audit.ranges %}
                    <tr>
                        <td>{{ item.label }}</td>
                        <td>{{ item.value }}</td>
                        <td>{{ item.band[0] }} ~ {{ item.band[1] }}</td>
                        <td>{% if item.in_band %}🟢 带内{% else %}🔴 越界{% endif %}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endif %}
        {% endif %}
    </div>
</div>
{% endif %}
```

- [ ] **Step 5: `report_export.html` 加同构面板**

在覆盖矩阵 table 之后插入：
```html
    {% set template_audit = summary.takeoff.template if summary.takeoff else None %}
    {% if template_audit %}
    <h2>疑似漏项 / 指标越界</h2>
    {% if not template_audit.available %}
    <p>该业态暂无范例库，未做漏项审计（不影响算量）。</p>
    {% else %}
    <p>范例库 {{ template_audit.type }} v{{ template_audit.version }} ·
       已核对 {{ template_audit.counts.checked }} 项 ·
       真漏算 {{ template_audit.counts.missing }} ·
       缺图源 {{ template_audit.counts.no_source }} ·
       人工排除 {{ template_audit.counts.excluded }}</p>
    {% if template_audit.missing %}
    <table class="boq-table">
        <thead><tr><th>分部</th><th>列项</th><th>单位</th><th>说明</th></tr></thead>
        <tbody>
            {% for item in template_audit.missing %}
            <tr><td>第{{ item.division }}分部</td><td>{{ item.name }}</td>
                <td>{{ item.unit }}</td><td>{{ item.message }}</td></tr>
            {% endfor %}
        </tbody>
    </table>
    {% endif %}
    {% if template_audit.no_source %}
    <h3>缺图源（加入待算清单，不补数）</h3>
    {% for bucket in template_audit.no_source %}
    <p><strong>第{{ bucket.division }}分部 · {{ bucket.count }} 项：</strong>
       {{ bucket["items"]|map(attribute='name')|join('、') }}</p>
    {% endfor %}
    {% endif %}
    {% if template_audit.ranges %}
    <h3>合理性指标</h3>
    <table class="boq-table">
        <thead><tr><th>指标</th><th>当前值</th><th>参考带</th><th>结论</th></tr></thead>
        <tbody>
            {% for item in template_audit.ranges %}
            <tr><td>{{ item.label }}</td><td>{{ item.value }}</td>
                <td>{{ item.band[0] }} ~ {{ item.band[1] }}</td>
                <td>{{ '带内' if item.in_band else '越界' }}</td></tr>
            {% endfor %}
        </tbody>
    </table>
    {% endif %}
    {% endif %}
    {% endif %}
```

- [ ] **Step 6: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_takeoff_web.py -q -k "template_panel or fill_template_gaps"`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add app/routes.py app/templates/boq.html app/templates/report_export.html \
  tests/test_takeoff_web.py
git commit -m "feat(ui): 清单页与报告增加疑似漏项/指标越界面板"
```

---

### Task 7: 历史对照匹配键修正（spec §5.5 / 缺陷 #2）

**Files:**
- Modify: `app/comparison.py`（删除 `REASONABLENESS_RANGES`、`_items_match`、`_find_item_quantity`；新增 `_has_current`）
- Modify: `tests/test_comparison_template.py`

**Interfaces:**
- Consumes: `template.material_of` / `template.normalize_unit`
- Produces: `compare_with_historical` 新增 `manual_review_items`；`missing_items` 不再含 `historical_quantity`

- [ ] **Step 1: 写失败测试**

在 `tests/test_comparison_template.py` 追加：

```python
def test_has_current_requires_division_material_and_unit():
    from types import SimpleNamespace
    from app.comparison import _has_current

    rows = [SimpleNamespace(division_id=2, unit="m²", label="客厅-墙面瓷砖",
                            project_features="")]
    assert _has_current(rows, 2, "瓷砖", "m2") is True
    assert _has_current(rows, 2, "瓷砖", "m") is False      # 单位不同
    assert _has_current(rows, 1, "瓷砖", "m2") is False     # 分部不同
    assert _has_current(rows, 2, "石材", "m2") is False     # 材料不同


def test_no_material_token_is_manual_review(web_env):
    import json
    app, pid, _ = web_env
    with app.app_context():
        from app import db
        from app.comparison import compare_with_historical
        from app.models import HistoricalProject, User

        admin = User.query.filter_by(is_admin=True).first()
        hist = HistoricalProject(
            name="历史样例", user_id=admin.id,
            summary_json=json.dumps({
                "stats": {"total_items": 1,
                          "division_items": {"2": ["某某无法识别列项"]}}}))
        db.session.add(hist)
        db.session.commit()

        result = compare_with_historical(pid, hist.id)
        assert result["missing_items"] == []
        assert [i["name"] for i in result["manual_review_items"]] == ["某某无法识别列项"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/bin/python -m pytest tests/test_comparison_template.py -q -k "has_current or manual_review"`
Expected: FAIL（`_has_current` 不存在）

- [ ] **Step 3: 改 `comparison.py`**

删除 `REASONABLENESS_RANGES = {...}` 整块与 `_items_match`、`_find_item_quantity`。顶部加：
```python
from app.core.takeoff import template as takeoff_template
```

新增：
```python
def _has_current(measurements, division_id, material, unit):
    target = takeoff_template.normalize_unit(unit)
    for m in measurements:
        if m.division_id != division_id:
            continue
        if division_id != 4 and takeoff_template.normalize_unit(m.unit or "") != target:
            continue
        text = (m.label or "") + " " + (getattr(m, "project_features", "") or "")
        if material in text:
            return True
    return False
```

`compare_with_historical` 的对照循环改为：
```python
    missing, manual_review = [], []
    for div_id, div_name, unit, _ in DIVISIONS:
        hist_items = hist_data.get('stats', {}).get('division_items', {}).get(str(div_id), [])
        for hist_name in hist_items:
            material = takeoff_template.material_of(hist_name)
            if not material:
                manual_review.append({
                    'name': hist_name,
                    'division_id': div_id,
                    'division_name': div_name,
                    'unit': unit,
                    'reason': '无可提取材料词，需人工裁定',
                })
                continue
            if _has_current(current_measurements, div_id, material, unit):
                continue
            missing.append({
                'name': hist_name,
                'division_id': div_id,
                'division_name': div_name,
                'unit': unit,
                'reason': '历史清单有此列项，当前算量未检出',
            })
```
返回值增加 `'manual_review_items': manual_review`。

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/bin/python -m pytest tests/test_comparison_template.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/comparison.py tests/test_comparison_template.py
git commit -m "fix(comparison): 行项改按分部+材料+单位精确匹配，无材料词转人工裁定"
```

---

### Task 8: 全量验证与封账回归

**Files:** 无新增；仅验证与必要修正。

- [ ] **Step 1: 跑纯包与模板相关测试**

```bash
./venv/bin/python -m pytest tests/test_build_boq_template.py tests/test_takeoff_template.py \
  tests/test_takeoff_rows.py tests/test_comparison_template.py -q
```
Expected: PASS

- [ ] **Step 2: 跑 CI 同口径（非 slow）**

```bash
./venv/bin/python -m pytest -m "not slow" -q
```
Expected: 全绿（基线 222+ 通过；新增用例计入）

- [ ] **Step 3: 跑 slow 与 227 封账**

```bash
./venv/bin/python -m pytest -m slow -q
./venv/bin/python -m pytest tests/test_takeoff_web.py -q -k "227 or template or fill_template_gaps"
```
Expected: 227 六值仍为 163.75 / 162.15 / 437.22 / 763.12 m² + 166.73 m + 42 樘；metrics 周长 ≈ 187.04、踢脚比 0.89；无 `template_item_missing`。

- [ ] **Step 4: 真实 HTTP 冒烟（本地）**

```bash
./venv/bin/python -m flask --app app run --port 5001
```
浏览器或 curl 依次验证：登录 → 上传 `tests/fixtures/minimal_flat.dxf` → 计量 → 清单页出现「疑似漏项 / 指标越界」→ 点「加入待算清单」→ 行数增加且分部合计不变 → 导出 xlsx → 打开报告页出现同构面板。

- [ ] **Step 5: 检查工作区**

```bash
git status --short
git diff --stat
```
Expected: 只含本计划涉及文件；无 `.env`、无 MEP 用户未完成文件、无临时文件。

- [ ] **Step 6: 提交收尾（如有修正）**

```bash
git add app tests scripts docs
git commit -m "test(takeoff): 里程碑 B 模板层全量回归"
```

---

## 自检（对照 spec）

| spec 条款 | 落点 |
|---|---|
| §5.3 模板文件形态 | Task 1 `sample_flat.json` + `ranges` |
| §5.4 三档判据 | Task 2 `audit`：`真漏算`/`缺图源`/人工排除；`group_only` 跳过 |
| §5.5 匹配键 | Task 7 历史对照 `(分部, 材料词, 单位)`；模板几何项按能力判存在（见「设计裁定 1」） |
| §5.6 auto-fill | Task 5：量全 None、`template_gap`、重算保留、合计不变 |
| §5.7 合理性判据 | Task 2/3：墙地比、踢脚/围合周长、灯具/插座密度；区间唯一来源 JSON；`waterproof` 不启用并记 issue |
| §5.8 Phase 5 接线与面板 | Task 4 + Task 6 |
| 缺陷 #1 | Task 5 |
| 缺陷 #2 | Task 7 |
| 缺陷 #3 | Task 2/3 |
| 无工程量泄漏 | Task 1 `assert_no_quantities` + Tier C 测试 |

**已知不在本轮范围：** 用户上传清单覆盖阈值（spec §5.3 第三层）、`scale_count` 按间判据（样板房无此类区间）、112 户型适配（待 DXF 导出后单独立项）。
