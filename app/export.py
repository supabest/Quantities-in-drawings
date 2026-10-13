# -*- coding: utf-8 -*-
"""app.export — 把项目清单(Measurement)导出为工程量计算表 xlsx(含备注列)。

设计要点：
  1. **数据源只有 DB**：不在 web 请求里重跑几何链(分钟级)，导出的是库里已封账的行；
     数值一致性由灌数时(scripts/seed_227_decoration.py)的恒等断言保证。
  2. **备注列不可缺**：每行的"不确定原因"与"需补充的资料"照库内 remarks/data_json 原样输出，
     低置信度整行高亮 —— 与 docs/QUANTITY_TAKEOFF_MANUAL.md 的三态披露同一口径。
  3. 只读，不改任何数量；无清单时返回 None(由路由决定提示)。
"""
import io
import json
from collections import OrderedDict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.models import DIVISIONS, CONFIDENCE_LEVELS

# 分部 id → 名称（与 models.DIVISIONS 同源，不另写一份）
DIV_NAME = OrderedDict((d[0], d[1]) for d in DIVISIONS)

HEADERS = ["序号", "分部", "项目名称", "项目特征", "部位(房间+图纸追溯)",
           "几何计算式", "工程量", "单位", "来源标签", "置信度",
           "备注(不确定原因)", "需补充的资料"]
WIDTHS = [6, 16, 24, 26, 30, 46, 10, 7, 12, 8, 58, 40]

HDR_FILL = PatternFill("solid", fgColor="1F4E78")
HDR_FONT = Font(bold=True, color="FFFFFF", size=11)
LOW_FILL = PatternFill("solid", fgColor="FFF2CC")
WRAP = Alignment(wrap_text=True, vertical="top")


def _qty(m):
    """按计量单位取该行的量(面/长/件 三制)。"""
    if m.unit and "m²" in (m.unit or ""):
        return m.area
    if (m.unit or "").strip() == "m":
        return m.length
    if (m.unit or "").strip() in ("个", "樘", "套", "台", "组"):
        return m.count
    return m.area if m.area else (m.length if m.length else m.count)


def _extra(m):
    try:
        return json.loads(m.data_json) if m.data_json else {}
    except (TypeError, ValueError):
        return {}


def build_workbook(project, measurements):
    """→ BytesIO(可下载) ；无行则 None。含三个 sheet：清单/待确认/说明。"""
    if not measurements:
        return None
    wb = Workbook()
    ws = wb.active
    ws.title = "工程量计算表"
    _write_header(ws)
    pend = []
    for i, m in enumerate(measurements, start=1):
        ex = _extra(m)
        need = ex.get("need", "") or ""
        row = [i, "%d·%s" % (m.division_id or 0, DIV_NAME.get(m.division_id, "")),
               m.label, m.project_features or "", m.position_desc or "",
               m.formula_text or "", _qty(m), m.unit or "",
               "%s %s" % (m.source_tag_icon or "", m.source_tag or ""),
               CONFIDENCE_LEVELS.get(m.confidence_level or "medium", {}).get("label", ""),
               m.remarks or "", need]
        for j, v in enumerate(row, start=1):
            c = ws.cell(row=i + 1, column=j, value=v)
            c.alignment = WRAP
        if m.confidence_level == "low":
            for j in range(1, len(HEADERS) + 1):
                ws.cell(row=i + 1, column=j).fill = LOW_FILL
            pend.append(row)
        elif m.confidence_level == "medium":
            pend.append(row)
    ws.freeze_panes = "A2"

    # ── 待确认清单（低置信在前） ──
    ws2 = wb.create_sheet("待确认清单")
    _write_header(ws2, cols=HEADERS[1:])
    for k, row in enumerate(sorted(pend, key=lambda r: 0 if r[8] == "低" else 1), start=2):
        for j, v in enumerate(row[1:], start=1):
            ws2.cell(row=k, column=j, value=v).alignment = WRAP
    ws2.freeze_panes = "A2"

    # ── 汇总与口径 ──
    ws3 = wb.create_sheet("汇总与口径")
    ws3.column_dimensions["A"].width = 150
    sums = OrderedDict()
    for m in measurements:
        s = sums.setdefault(m.division_id or 0, {"area": 0.0, "length": 0.0,
                                                  "count": 0, "n": 0})
        s["area"] += m.area or 0
        s["length"] += m.length or 0
        s["count"] += m.count or 0
        s["n"] += 1
    lines = ["■ 项目：%s ｜ 状态：%s" % (project.name, project.status)]
    for did, s in sorted(sums.items()):
        lines.append("｜ 分部 %d·%s：m² %.2f ｜ m %.2f ｜ 件 %d ｜ %d 行" % (
            did, DIV_NAME.get(did, ""), round(s["area"], 2),
            round(s["length"], 2), s["count"], s["n"]))
    lines.append("｜ 全表：%d 行 ｜ 低置信 %d 行 ｜ 中置信 %d 行" % (
        len(measurements),
        sum(1 for m in measurements if m.confidence_level == "low"),
        sum(1 for m in measurements if m.confidence_level == "medium")))
    meta = _extra(measurements[0]).get("seal_note") if measurements else None
    if meta:
        lines.append("｜ " + meta)
    for i, ln in enumerate(lines, start=1):
        c = ws3.cell(row=i, column=1, value=ln)
        c.alignment = WRAP
        if i == 1:
            c.font = Font(bold=True, size=12)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _write_header(ws, cols=None):
    cols = cols or HEADERS
    widths = WIDTHS if cols is HEADERS else WIDTHS[1:]
    for j, (h, w) in enumerate(zip(cols, widths), start=1):
        c = ws.cell(row=1, column=j, value=h)
        c.fill, c.font = HDR_FILL, HDR_FONT
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        ws.column_dimensions[get_column_letter(j)].width = w
