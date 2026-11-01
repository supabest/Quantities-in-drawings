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
# 计量单位空缺但实为线性列项的名称关键字（GRG 线条、灯槽等，源表 E 列有 m 长度）
LINEAR_UNIT_MARKERS = ("GRG", "装饰线", "线条", "凹槽", "灯槽", "窗帘盒")
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


def _classify_unit(raw_unit, name):
    """返回 (单位, 是否仅作分组标题)。

    计量单位空缺的行默认是分组标题（单位「项」）；但天棚装饰线一类线性列项
    （GRG 线条、灯槽、窗帘盒…）在源表里真的漏填单位、E 列却带 m 长度，
    按判定口径归为真实行项，单位记「m」。名称含「每增减」等调整标记的行
    无论单位是否空缺都只作分组标题。
    """
    if any(marker in name for marker in MODIFIER_MARKERS):
        return normalize_unit(raw_unit), True
    if not raw_unit and any(marker in name for marker in LINEAR_UNIT_MARKERS):
        return "m", False
    return normalize_unit(raw_unit), not raw_unit


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
            raw_unit = _s(row[3]) if len(row) > 3 else ""
            if re.fullmatch(r"[一二三四五六七八九十]+", c0):
                sec, sub, sec_name, sub_name = c0, "", c1, ""
                continue
            if re.fullmatch(r"（[一二三四五六七八九十]+）", c0):
                sub, sub_name = c0, c1
                continue
            name = _clean_name(c1)
            if not name or name in TOTAL_NAMES or name.startswith("项目名称"):
                continue
            unit, group_only = _classify_unit(raw_unit, name)
            division = _division_for(sheet, sec, sub, name, unit)
            if division is None:
                continue
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
