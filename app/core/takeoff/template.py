"""行项模板层：词表归一化、模板加载、三档漏项判定、合理性区间。

纯包：只 import 标准库与 .types；不得 import flask / app.models / modeling。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .types import Issue, Row

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

# 「型」紧跟词后是修饰用法（如「开关型」），不算材料名
_MODIFIER_SUFFIX = "型"


def normalize_unit(raw):
    text = (raw or "").strip()
    if not text:
        return "项"
    return UNIT_ALIASES.get(text, text)


def material_of(name):
    """取名称中最靠右的受控词作为材料（中文中心语在后）。

    「洗衣机地漏」→「地漏」、「洗衣机龙头」→「龙头」、「马桶喷枪」→「喷枪」；
    同一位置有长短词重叠时取长词。括号（全角「（）」与半角「()」）内是定位语/
    修饰语而非材料，先整体剥掉再匹配——「石膏板吊顶 立面（灯槽位置）」→「石膏板」、
    「单相二、三极防溅插座(带开关)」→「插座」。紧跟「型」出现的是修饰用法
    （如「开关型」），跳过该次命中；若再无有效命中则返回 None，交由人工复核。
    """
    text = re.sub(r"[（(][^）)]*[）)]", "", name or "")
    best = None  # (起点, 词长, 词)
    for token in MATERIAL_TOKENS:
        start = text.find(token)
        while start != -1:
            end = start + len(token)
            if text[end:end + 1] != _MODIFIER_SUFFIX:
                if best is None or start > best[0] or (start == best[0] and len(token) > best[1]):
                    best = (start, len(token), token)
            start = text.find(token, start + 1)
    return best[2] if best else None


# ════════════════════════════════════════════════════════════════════
# 模板加载、三档漏项判定、合理区间评估（Milestone B / Task 2）
# 区间唯一来源是 templates/<project_type>.json 的 ranges；无模板时按下表兜底。
# ════════════════════════════════════════════════════════════════════

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
# 判据需要额外来源（如防水上翻高度），本轮不启用；只在审计载荷里披露
DISABLED_RANGE_KEYS = ("waterproof_ratio",)


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
    # 没有地面面积就没有可比基线：任何比值都不可判定，也不报越带
    if floor <= 0:
        return [], []
    if wall > 0:
        add("wall_floor_ratio", "wall_floor_ratio", "项目墙地比", wall / floor)
    if perimeter > 0 and baseboard > 0:
        add("baseboard_ratio", "baseboard_perimeter",
            "项目踢脚/围合周长比", baseboard / perimeter)
    if totals_map.get("light_count"):
        add("light_per_area", "light_per_area", "灯具密度",
            float(totals_map["light_count"]) / floor, "个/m²")
    if totals_map.get("socket_count"):
        add("socket_per_area", "socket_per_area", "插座密度",
            float(totals_map["socket_count"]) / floor, "个/m²")
    return out, issues


def _disabled_ranges(bands):
    return [key for key in DISABLED_RANGE_KEYS if key not in bands]


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
                 "missing": [], "no_source": [], "gap_items": [], "ranges": [],
                 "disabled_ranges": _disabled_ranges(ranges(project_type))},
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
                and _get(r, "value") is not None
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

    bands = ranges(project_type)
    range_rows, range_issues = evaluate_ranges(totals(rows, metrics), project_type)
    issues.extend(range_issues)

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
        # 未启用的判据只披露，不发 Issue：issue 列表要能被调用方当作「真问题」消费
        "disabled_ranges": _disabled_ranges(bands),
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
