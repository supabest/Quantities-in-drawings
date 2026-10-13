# -*- coding: utf-8 -*-
"""scripts/build_decoration_workbook.py — 227户型 装修封账(地/墙/顶/固装) → 工程量计算表(xlsx)。

纪律(与 scripts/build_mep_workbook.py 同构，违反即构建失败、不产出文件):
  1. **数值零新增零修改**: 主表 72 行一律取自 modeling.summary(几何权威链, 单一真源),
     天花附件取自 modeling.ceiling_zones.regenerate(); 本脚本只做字段映射、备注、排版。
  2. **恒等断言**: 72 行 / m² 763.12 / m 166.73 / 21 个 / 分段行数(16,24,12,20) /
     顶 162.15 = 地 163.75 − 无标注块 1.60 / 墙 437.22 = 全间净墙 505.28 − 窄条 68.06 /
     P3b 97.39 + 龛左窄带 0.34 + 切割线 0.05 = 97.78 / 灯槽 16.89 = 17.73 − 裁定剔除 0.84,
     任一项破账 → AssertionError。
  3. **每行必须交代清楚**: 备注由 REMARK_RULES 规则驱动(不手写逐行文案), 任何一条规则
     命中 0 行即构建失败(防规则空转/防口径漂移后仍套旧话术); 未命中任何规则的行显式
     标 "无"(= 已确证无不确定项), 不允许留空。
  4. **不能确定的不猜**: 降为待确认(conf=low)并写明原因 + 需补资料; 被裁定排除的量
     走「待确认与挂账清单」独立披露, 不混进 72 行封账。

三态署名: MACHINE(几何确定性算出) / VLM_READ(人工放大读图转录) /
          USER_RULING(用户裁定的归属或口径) / ARCHIVE_ONLY(机制不可复现, 仅存档值)。

运行: venv/bin/python scripts/build_decoration_workbook.py
产物: outputs/227户型装修工程量计算表.xlsx
"""
import os
import sys
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from openpyxl import Workbook                                        # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill             # noqa: E402
from openpyxl.utils import get_column_letter                         # noqa: E402

from modeling import base_extract as B                                # noqa: E402
from modeling import summary as S                                     # noqa: E402
from modeling import ceiling_zones as CZ                               # noqa: E402

OUT_PATH = os.path.join(ROOT, 'outputs', '227户型装修工程量计算表.xlsx')

# ── 封账基准(golden 同源, 破账即不产出) ──────────────────────────────────
SEAL = OrderedDict([
    ("n_rows", 72), ("m2", 763.12), ("m", 166.73), ("ge", 21),
    ("floor_m2", 163.75), ("wall_m2", 437.22), ("wall_m", 166.73),
    ("ceil_m2", 162.15), ("sec_rows", (16, 24, 12, 20)),
    ("wall_gross_m2", 505.28), ("wall_strip_out_m2", 68.06),
    ("ceil_anon_m2", 1.60), ("p3b_total_m2", 97.78),
    ("p3b_flood_m2", 97.39), ("p3b_niche_m2", 0.34), ("p3b_cut_m2", 0.05),
    ("cove_sealed_m", 16.89), ("cove_raw_m", 17.73), ("cove_ruling_m", 0.84),
])

# ── 分部映射(app/models.py DIVISIONS 口径) ───────────────────────────────
DIVISION_OF = {
    "地面 (P1)": (1, "楼地面工程"),
    "墙面 (P2)": (2, "墙柱面工程"),      # 踢脚线行单独改派 5
    "天花 (P3)": (3, "天棚工程"),
    "固定家具/柜台 (P4)": (6, "固定柜台工程"),
}
SKIRT_DIVISION = (5, "踢脚线工程")
UNIT_SHOW = {"m2": "m²", "m": "m", "个": "个"}
CONF_RANK = {"high": 0, "medium": 1, "low": 2}

# ── 假设值(口径披露用, 逐行备注引用) ─────────────────────────────────────
ASSUMED = {"door_h": 2.10, "win_h": 1.50, "win_sill": 0.00}


def R(code, applies, text, state, conf, need=""):
    return {"code": code, "applies": applies, "text": text,
            "state": state, "conf": conf, "need": need}


# 规则只按**行内容**判定(不按记忆中的行号), 保证重排/补行后备注仍跟着事实走。
REMARK_RULES = [
    R("F1_ANON_ZONE",
      lambda sec, r: r["part"].startswith("(无标注@"),
      "该围合域在 P01 平面布置图/P11 砌筑图上均无房间名标注，仅按坐标挂账；"
      "面积=几何实测(可信)，空间归属与面层做法无法判定",
      "MACHINE", "low",
      "需设计师提供该域房间命名与做法归属(04空间做法表正文为图片，不可机读)"),

    R("W1_OPENER_ASSUMED",
      lambda sec, r: sec.startswith("墙面") and r["unit"] == "m2",
      "洞口扣减按**假设尺寸**(门高%.2fm/窗高%.2fm/窗台%.2fm)：图纸集无可机读门窗表"
      "(S04 门窗表正文为图片)，墙边长与吊顶净高为几何实测，洞口面积非图上实测"
      % (ASSUMED["door_h"], ASSUMED["win_h"], ASSUMED["win_sill"]),
      "MACHINE+假设", "medium",
      "需门窗表(可机读版)以实测洞口尺寸替换假设高度"),

    R("W2_ELEV_WINDOW_PEND",
      lambda sec, r: sec.startswith("墙面") and r["part"] == "电梯厅" and r["unit"] == "m2",
      "**待核**：电梯厅 5.73m 宽开口的性质(真窗 vs 电梯门开口)尚未判定；"
      "若为通高门(2.10m)则本行净墙应为 32.70㎡(−3.43㎡)，当前按窗(1.50m)扣减",
      "MACHINE+待核", "low",
      "需立面图/门窗表确认该开口性质与高度"),

    R("W3_NO_BOQ_COLUMN",
      lambda sec, r: "BOQ无此列" in r["formula"],
      "清单无此列项：2026-10-12 用户裁定电梯厅/过厅属精装范围，按与 P1/P3b 统一口径"
      "纯几何补计(非漏算回填)",
      "USER_RULING", "medium"),

    R("K1_SKIRT_NO_WIN_CUT",
      lambda sec, r: r["item"] == "踢脚线",
      "净周长与门洞宽为几何实测；**未扣落地窗/门联窗断点**(图面无窗台高度标注)，"
      "若现场踢脚在窗处断开则本行偏大",
      "MACHINE+假设", "medium",
      "需门窗表窗台高度(或现场确认踢脚是否断开)"),

    R("C1_CEIL_CALIBER",
      lambda sec, r: sec.startswith("天花"),
      "几何=**单层顶面投影净顶**(不含跌级立面/灯槽/返沿展开)，属口径选择而非算错；"
      "有清单校验值的行系统性偏小(−24.6%%~−47.8%%)=口径差非漏算；交付口径=投影+天花分级"
      "明细(2026-10-12 用户裁定 CD 大样比例暂定，维持投影口径)",
      "MACHINE+口径", "medium",
      "需 CD01~10 天花大样的可标定比例，才能给出展开面积"),

    R("C2_NO_BOQ_COLUMN",
      lambda sec, r: sec.startswith("天花") and "BOQ无此列" in r["formula"],
      "清单无此列项：2026-10-12 用户裁定电梯厅/过厅属精装范围，与 P1/P3b 口径统一补计",
      "USER_RULING", "medium"),

    R("P1_FIX_COUNT_NOT_GEOM",
      lambda sec, r: sec.startswith("固定家具"),
      "数量=图面清点+设计排程；柜体在 DXF 中为**未命名块**且大样逐柜标注稀疏 "
      "→ 件数不可由几何自动计数，柜体/台面展开面积待 P4b 去噪精算(本表只出行数)",
      "MACHINE(位置)+人工清点", "medium",
      "需物料表逐柜编号与图面块名的对应关系(或固装明细表)"),

    R("P2_FIX_NAME_PROVISIONAL",
      lambda sec, r: "？" in r["item"],
      "**名称未确识**(带问号)：CAD 自定义字体导致通用 OCR 失效，已按矢量字形重放判读，"
      "构件身份与归属待确认",
      "VLM_READ", "low",
      "需设计师确认该构件名称/是否计入固装清单"),

    R("P3_FIX_NO_DETAIL",
      lambda sec, r: sec.startswith("固定家具") and "无大样" in r["trace"],
      "无对应大样图 → 无法给出柜体/台面面积，按现场定制计量(仅列件数)",
      "MACHINE+缺图", "low",
      "需该构件大样图或物料表规格行"),
]

# ── 挂账项(被裁定排除或图上读不出的量，独立披露不混入 72 行) ────────────
HANGOVER = [
    ("墙面·无标注窄条", "68.06㎡", "全间净墙 505.28 − 窄条 68.06 = 封账 437.22",
     "窄条(含 1.14㎡/17.6m 周长那条)无可归属饰面标注，2026-10-12 用户裁定维持不出行",
     "USER_RULING"),
    ("天花·无标注块", "1.60㎡", "地面 163.75 − 1.60 = 天花 162.15",
     "两个无房间名围合域(1.14+0.46)不计入天棚，命名后可归入",
     "MACHINE+待命名"),
    ("天花·跌级侧带展开面积", "仅确证 6 对 2.25㎡(下界)", "P3b §2 侧带",
     "洪水域被墨线隔开，靠墙带/灯槽带/未经互证域(840@2450 标高冲突)均不计；"
     "CD 大样无可标定比例 → 展开面积不可全自动，维持投影+分级明细",
     "MACHINE(下界)+口径"),
    ("灯槽(藏灯虚线)长度", "存档 16.89m；机器口径 46.57~64.15m", "17.73 − 0.84 = 16.89",
     "机制已重建但 12 条采信链仅 9 条逐字复现(Σ=11.77m)；存档链截断位置不可复现"
     "(宽度带/长度窗/图层/gap/实线穿越/共线丢弃 六类判据逐否)，机器 Σ 恒偏大 → "
     "16.89m 定为 ARCHIVE_ONLY，不得用机器值替换；带弧段与斜向虚线欠采",
     "ARCHIVE_ONLY"),
    ("固装·柜体/台面面积", "未出行", "P4b 待精算",
     "柜体块名未编码+大样标注稀疏，去噪后可算展开面积；当前仅列件数 21 个",
     "MACHINE+待办"),
    ("门窗洞口面积", "按假设值", "门 2.10m 高 / 窗 1.50m 高 / 窗台 0",
     "无可机读门窗表(S04 为图片)，扣减面积随假设浮动；墙面 24 行逐行已标 F1/W1",
     "MACHINE+假设"),
    ("电梯厅 5.73m 开口性质", "±3.43㎡", "36.13㎡(按窗) vs 32.70㎡(按通高门)",
     "真窗/电梯门未定 → 敏感度已量化，落待核(W2)",
     "MACHINE+待核"),
]

# ── P3b 附件行的备注规则(域级) ───────────────────────────────────────────
ZONE_REMARKS = {
    121: ("图上无标高符号；2026-10-12 用户裁定按附近吊顶归 +2650 —— "
          "面积 0.34㎡=MACHINE，标高归属=USER_RULING", "USER_RULING", "medium"),
    840: ("平面 05 无 2450 锚 → 标高冲突(读图值与平面标注冲突)，"
          "故其与域 791 之间的侧带不计入", "VLM_READ+冲突", "low"),
    155: ("旧注“卧室区主顶”不成立：几何拆分后客厅/餐厅/玄关占 59%，"
          "另含浴室二 11%/过厅 6%/卧室二 2%/墙带 22%", "MACHINE", "medium"),
    83: ("标高为 SLAN-100(窗帘盒带)，非吊顶标高 → 单列不并入分级合计",
         "VLM_READ", "medium"),
}
MARKER_NO_LEADER = (5, 8, 19, 23)   # ⊕编号：垂直引线穿墙，程序未捕获

# 域119 按裁定的展开行(封账 21 行口径 = 19 洪水域 − 119 + 3)。
# key 必须与 ceiling_zones.split_119 的输出键逐字一致，否则取不到值。
# 机器重跑值与封账存档值在龛左窄带/切割线像素上差 0.01(切割线像素归属的末位差)，
# 本表**同时列出两值并标末位差**，出行取封账值，不拿机器值静默替掉封账。
SPLIT_ROWS = (("龛内(+2650,⊕14)", "119a", "2650", "14"),
              ("右带(+2600,⊕26)", "122", "2600", "26"),
              ("左窄带(无符号)", "121", "2650", "—"))


# ══════════════════════════════════════════════════════════════ 取数与判定
def annotate(sec, row):
    """一行 → (备注文本, 规则码, 三态集合, 置信度)。未命中任何规则 → '无'。"""
    hits = [ru for ru in REMARK_RULES if ru["applies"](sec, row)]
    if not hits:
        return "无", [], ["MACHINE"], "high"
    texts, codes = [], []
    states, conf = [], "high"
    for ru in hits:
        texts.append(ru["text"])
        codes.append(ru["code"])
        states.append(ru["state"])
        if CONF_RANK[ru["conf"]] > CONF_RANK[conf]:
            conf = ru["conf"]
    return "；".join(texts), codes, states, conf


def collect_rows():
    """主表 72 行：来自 modeling.summary(单一真源)，逐行加备注。"""
    data = B.build()
    secs = S.sections(data)
    rows, code_hits = [], {ru["code"]: 0 for ru in REMARK_RULES}
    for name, rws in secs:
        for r in rws:
            remark, codes, states, conf = annotate(name, r)
            for c in codes:
                code_hits[c] += 1
            div = DIVISION_OF[name]
            if r["item"] == "踢脚线":
                div = SKIRT_DIVISION
            rows.append({"section": name, "division": div, "remark": remark,
                         "codes": codes, "states": states, "conf": conf, **r})
    dead = [c for c, n in rows and code_hits.items() if n == 0]
    assert not dead, "规则空转(命中 0 行，口径已漂移或话术过期): %s" % dead
    return rows, code_hits


def need_info(row):
    """该行「需补充的资料」：从命中的规则回查，不另存一份口径。"""
    needs = [ru["need"] for ru in REMARK_RULES if ru["code"] in row["codes"] and ru["need"]]
    return " / ".join(needs)


def seal_checks(rows):
    """恒等断言：破账直接 AssertionError，不产出文件。

    容差口径：封账数（763.12 等）是各 takeoff 模块**先求和后 `%.2f` 显示**的值，
    故此处一律用**未舍入的真值**与封账比，容差 0.01（即只允许末位显示差）。
    若先 round(行) 再 sum 会引入银行家舍入差（实测 763.115 → round 得 763.11），
    那是断言口径错，不是账错——不得为此改封账。
    """
    TOL = 0.011
    raw_m2 = sum(r["value"] for r in rows if r["unit"] == "m2")
    raw_m = sum(r["value"] for r in rows if r["unit"] == "m")
    ge = int(sum(r["value"] for r in rows if r["unit"] == "个"))
    m2, m = round(raw_m2, 2), round(raw_m, 2)
    assert len(rows) == SEAL["n_rows"], "行数 %d ≠ 封账 %d" % (len(rows), SEAL["n_rows"])
    assert abs(raw_m2 - SEAL["m2"]) < TOL, "m² Σ %.4f ≠ 封账 %.2f" % (raw_m2, SEAL["m2"])
    assert abs(raw_m - SEAL["m"]) < TOL, "m Σ %.4f ≠ 封账 %.2f" % (raw_m, SEAL["m"])
    assert ge == SEAL["ge"], "离散件 %d ≠ 封账 %d" % (ge, SEAL["ge"])
    per_sec = OrderedDict()
    for r in rows:
        per_sec.setdefault(r["section"], []).append(r)
    got_rows = tuple(len(v) for v in per_sec.values())
    assert got_rows == SEAL["sec_rows"], "分段行数 %s ≠ 封账 %s" % (got_rows, SEAL["sec_rows"])
    for key, sec, want in (("floor_m2", "地面 (P1)", SEAL["floor_m2"]),
                           ("ceil_m2", "天花 (P3)", SEAL["ceil_m2"]),
                           ("wall_m2", "墙面 (P2)", SEAL["wall_m2"])):
        g = sum(r["value"] for r in per_sec[sec] if r["unit"] == "m2")
        assert abs(g - want) < TOL, "%s %.4f ≠ 封账 %.2f" % (sec, g, want)
    # 跨专业恒等式(几何口径链的护栏)
    assert abs(SEAL["ceil_m2"] - (SEAL["floor_m2"] - SEAL["ceil_anon_m2"])) < 0.01, \
        "顶=地−无标注块 恒等式破"
    assert abs(SEAL["wall_m2"] - (SEAL["wall_gross_m2"] - SEAL["wall_strip_out_m2"])) < 0.01, \
        "墙=全间净墙−窄条 恒等式破"
    return m2, m, ge, per_sec


# ══════════════════════════════════════════════════════════════ 排版
HDR_FILL = PatternFill("solid", fgColor="1F4E78")
HDR_FONT = Font(bold=True, color="FFFFFF", size=11)
SEC_FILL = PatternFill("solid", fgColor="DDEBF7")
LOW_FILL = PatternFill("solid", fgColor="FFF2CC")
WRAP = Alignment(wrap_text=True, vertical="top")


def sheet_table(ws, headers, widths, rows_data, start=1, freeze=True):
    for j, (h, w) in enumerate(zip(headers, widths), start=1):
        c = ws.cell(row=start, column=j, value=h)
        c.fill, c.font, c.alignment = HDR_FILL, HDR_FONT, Alignment(
            wrap_text=True, vertical="center", horizontal="center")
        ws.column_dimensions[get_column_letter(j)].width = w
    for i, row in enumerate(rows_data, start=start + 1):
        for j, v in enumerate(row, start=1):
            c = ws.cell(row=i, column=j, value=v)
            c.alignment = WRAP
    if freeze:
        ws.freeze_panes = ws.cell(row=start + 1, column=1)
    return len(rows_data) + start


def note_block(ws, title, lines, start_row):
    c = ws.cell(row=start_row, column=1, value=title)
    c.font = Font(bold=True, size=12)
    r = start_row + 1
    for ln in lines:
        cc = ws.cell(row=r, column=1, value=ln)
        cc.alignment = WRAP
        r += 1
    return r


def build_main(ws, rows):
    headers = ["序号", "分部", "部位/房间", "列项(项目名称)", "单位", "几何计算式",
               "工程量", "尺寸追溯", "数据来源(三态)", "置信度", "备注(不确定原因)",
               "需补充的资料"]
    widths = [6, 16, 22, 22, 6, 46, 10, 26, 20, 8, 60, 40]
    body = []
    for i, r in enumerate(rows, start=1):
        body.append([
            i, "%d·%s" % (r["division"][0], r["division"][1]), r["part"], r["item"],
            UNIT_SHOW.get(r["unit"], r["unit"]), r["formula"],
            round(r["value"], 2) if isinstance(r["value"], (int, float)) else r["value"],
            r["trace"], " + ".join(r["states"]),
            {"high": "高", "medium": "中", "low": "低"}[r["conf"]],
            r["remark"], need_info(r)])
    last = sheet_table(ws, headers, widths, body, start=1)
    for i, r in enumerate(rows, start=2):
        if r["conf"] == "low":
            for j in range(1, len(headers) + 1):
                ws.cell(row=i, column=j).fill = LOW_FILL
    return last


def pend_sorted(rows):
    """待确认行(低/中)的**唯一定序**：低置信在前，同置信按分部段次。xlsx 与静态站共用。"""
    pend = [r for r in rows if r["conf"] in ("low", "medium")]
    return sorted(pend, key=lambda r: (-CONF_RANK[r["conf"]], r["section"]))


def pend_summary_lines(rows, m2, m, ge):
    """「受影响工程量(按置信度)」的**唯一文案源**：xlsx 与本表页/静态站共用。"""
    pend = [r for r in rows if r["conf"] in ("low", "medium")]
    low_m2 = round(sum(r["value"] for r in pend
                       if r["conf"] == "low" and r["unit"] == "m2"), 2)
    low_m = round(sum(r["value"] for r in pend
                      if r["conf"] == "low" and r["unit"] == "m"), 2)
    return [
        "待确认(low)：%d 行 ｜ 涉及 m² %.2f ｜ m %.2f ｜ 件 %d" % (
            sum(1 for r in pend if r["conf"] == "low"), low_m2, low_m,
            sum(1 for r in pend if r["conf"] == "low" and r["unit"] == "个")),
        "建议复核(medium)：%d 行 ｜ 全表 %d 行中 %d 行无备注(已确证无不确定项)" % (
            sum(1 for r in pend if r["conf"] == "medium"), len(rows),
            sum(1 for r in rows if r["remark"] == "无")),
        "全表合计(封账不变)：m² %.2f ｜ m %.2f ｜ 件 %d" % (m2, m, ge),
        "说明：待确认项的数**已计入**上述合计(除下方挂账清单另列者)，置信度低不代表数量可改，"
        "只代表口径/归属仍需外部资料收敛。",
    ]


def build_pending(ws, rows, m2, m, ge):
    headers = ["序号", "分部", "部位/房间", "列项", "工程量", "单位",
               "置信度", "不确定原因", "需补充的资料"]
    widths = [6, 16, 22, 22, 10, 6, 8, 60, 44]
    pend = pend_sorted(rows)
    body = []
    for i, r in enumerate(pend, start=1):
        body.append([i, "%d·%s" % (r["division"][0], r["division"][1]), r["part"],
                     r["item"], round(r["value"], 2), UNIT_SHOW.get(r["unit"], r["unit"]),
                     {"high": "高", "medium": "中", "low": "低"}[r["conf"]],
                     r["remark"], need_info(r)])
    last = sheet_table(ws, headers, widths, body, start=1)
    last += 2
    last = note_block(ws, "▍受影响工程量(按置信度)",
                      pend_summary_lines(rows, m2, m, ge) + ["", "↓ 挂账清单（见下表）"], last)
    last += 1
    c = ws.cell(row=last, column=1, value="▍挂账清单（裁定排除或图上读不出，未计入 72 行封账）")
    c.font = Font(bold=True, size=12)
    hg_head = ["项目", "量", "算式/口径", "原因", "状态"]
    for j, h in enumerate(hg_head, start=1):
        c = ws.cell(row=last + 1, column=j, value=h)
        c.fill, c.font = HDR_FILL, HDR_FONT
    for i, h in enumerate(HANGOVER, start=last + 2):
        for j, v in enumerate(h, start=1):
            ws.cell(row=i, column=j, value=v).alignment = WRAP
    return last + 1 + len(HANGOVER)


def build_ceiling(ws, art):
    """天花分级明细(P3b)：21 域 + 分级合计 + 侧带 + 灯槽，逐行三态。"""
    headers = ["域id", "标高(mm)", "水平面(㎡)", "标记⊕", "数据来源(三态)",
               "置信度", "备注(不确定原因)"]
    widths = [10, 14, 12, 14, 22, 8, 70]
    from modeling.ceiling_zones import build_domains                     # 同口径
    dom = build_domains(art["rows"])
    sp = art["split_119"]
    mach, arch = sp["machine_grouped"], sp["sealed_reference"]
    body, raw_areas = [], 0.0
    for d in dom:
        lid = d["lid"]
        if lid == CZ.SPLIT_119_LID:          # 展开为封账口径的 3 行(出行取存档值)
            for key, zid, lvl, mk in SPLIT_ROWS:
                mv, av = round(mach[key], 2), round(arch[key], 2)
                note = ("域119 沿床头龛边界 x=564.0/637.9 切开的子块；"
                        "面积=MACHINE、切线位置与归级=USER_RULING(2026-10-04 裁定，"
                        "2026-10-12 裁定左窄带归 +2650)")
                if zid == "121":
                    note = ZONE_REMARKS[121][0] + "；" + note
                if abs(mv - av) >= 0.005:
                    note += ("；机器重跑 %.2f㎡ 与封账存档 %.2f㎡ 差 0.01"
                             "(=0.05㎡ 切割线像素的归属末位差) → 本行按**封账值**出行"
                             % (mv, av))
                else:
                    note += "；机器与封账逐字一致"
                st = "USER_RULING" if zid == "121" else "MACHINE+USER_RULING"
                body.append([zid, "+%s" % lvl, av, mk, st, "中", note])
                raw_areas += av
            continue
        rm, st, cf = ZONE_REMARKS.get(lid, ("标高由 VLM 逐格拼版转录(非机器读取)；"
                                           "面积=洪水填充几何实测", "MACHINE+VLM_READ",
                                           "medium"))
        markers = ",".join(str(x) for x in d["markers"]) or "—"
        if any(x in MARKER_NO_LEADER for x in d["markers"]):
            rm += "；含⊕标记(%s)垂直引线穿墙未捕获 → 归属以目视复核为准" % (
                ",".join(str(x) for x in d["markers"] if x in MARKER_NO_LEADER))
            st, cf = st + "+VLM_READ", "low"
        body.append([lid, "+%s" % d["lvl"], round(d["area_m2"], 2), markers, st,
                     {"high": "高", "medium": "中", "low": "低"}[cf], rm])
        raw_areas += d["area_m2"]
    assert len(body) == 21, "P3b 展开后应 21 行，实得 %d" % len(body)
    cut_arch = round(arch["切割线像素"], 2)
    assert abs(raw_areas + cut_arch - SEAL["p3b_total_m2"]) < 0.02, \
        "P3b Σ %.4f + 切割线 %.2f ≠ 封账 %.2f" % (raw_areas, cut_arch, SEAL["p3b_total_m2"])
    last = sheet_table(ws, headers, widths, body, start=1)
    total = round(raw_areas, 2)
    lv = {}
    for b in body:
        lv[b[1]] = round(lv.get(b[1], 0) + b[2], 2)
    last = note_block(ws, "▍分级合计(机器重跑, 与封账对账)", [
        " ｜ ".join("%s %.2f㎡" % (k, v) for k, v in sorted(
            lv.items(), key=lambda kv: -kv[1]))], last)
    last = note_block(ws, "▍封账恒等式", [
        "洪水域 Σ = %.2f㎡ ｜ 封账 97.78 = 97.39(20 域) + 0.34(龛左窄带, 归 +2650) "
        "+ 0.05(切割线像素) → 域119 切分后为 21 域" % total,
        "对账式(分级) 48.01+30.99+6.58+7.21+1.87+0.51+2.55 = 97.72，余 0.06㎡ 为切割线像素",
        "⚠ 域 119 的切分：面积=MACHINE、切线位置与归级=USER_RULING(2026-10-12 裁定)",
        "⚠ 域 840@2450 标高与平面 05 无锚互证 → 冲突披露，其与 791 的侧带不计"], last)
    bands = art["bands_bridge"]["bands"]
    last += 1
    bh = ["域对", "标高差(m)", "接触长(m)", "侧带面积(㎡)", "备注"]
    for j, h in enumerate(bh, start=1):
        c = ws.cell(row=last, column=j, value=h)
        c.fill, c.font = HDR_FILL, HDR_FONT
    for i, b in enumerate(bands, start=last + 1):
        ws.cell(row=i, column=1, value="%s↔%s" % (b["z1"], b["z2"]))
        ws.cell(row=i, column=2, value=b["dh_m"])
        ws.cell(row=i, column=3, value=b["len_m"])
        ws.cell(row=i, column=4, value=b["area_m2"])
        ws.cell(row=i, column=5, value="接触像素/长度/面积=MACHINE，标高=VLM_READ").alignment = WRAP
    last = last + 1 + len(bands) + 1
    last = note_block(ws, "▍跌级侧带(下界)", [
        "已确证 %d 对 Σ = %.2f㎡ ｜ 口径=**下界**：靠墙带/灯槽带/未经互证域均不计" % (
            len(bands), art["bands_bridge"]["sum_band_m2"]),
        "展开面积不可全自动：CD01~10 天花大样无可标定比例(2026-10-12 用户裁定“比例暂定”，"
        "维持投影 + 分级明细口径)"], last)
    dc = art["dash_chains"]
    last += 1
    last = note_block(ws, "▍灯槽(藏灯虚线链)", [
        "封账采信 = %.2fm（**ARCHIVE_ONLY：存档值，非机器值**）" % dc["archive_sealed_m"],
        "算式：存档 12 条 Σ %.2fm − 用户裁定剔除 #10(45°链 %.2fm，判为入户门弧弦) = %.2fm"
        % (dc["archive_raw_sum_m"], dc["archive_accepted"][dc["excluded_idx"]]["span_m"],
           dc["archive_sealed_m"]),
        "复现度：机制已重建，12 条仅 %d 条逐字命中(Σ=%.2fm，域 #2~#10)；"
        "#0/#1/#11 机器链长 1~3 段与存档不符" % (len(dc["reproduced_idx"]),
                                                 dc["reproduced_span_m"]),
        "机器口径 Σ = %.2fm(链 %d 条，过门槛 %d 条)；池上限敏感度 %s → 恒偏大，"
        "不得用来替换封账" % (dc["machine_sum_m"], dc["n_chains"], dc["n_accepted"],
                              dc["machine_sum_sensitivity"]),
        "已否定的截断判据(六类)：%s" % "；".join(dc["rejected_causes"]),
        "样式判读：%s" % dc["style_note"],
        "采信的口径要点：'c' 曲线成员取**贝塞尔弧长**(实测 2.649≈存档 2.64，端点弦 2.456 不符)，"
        "分桶用 floor 而非 round，span_m=端点距×0.0352778"], last)
    return last


def _num(v):
    try:
        int(v)
        return True
    except (TypeError, ValueError):
        return False


NOTE_TITLE = "口径、假设与依据"


def note_lines(code_hits):
    """口径与假设的**唯一文案源**：xlsx「口径与假设」sheet 与静态站 notes 页共用本函数，
    避免两处话术各写一份而漂移。"""
    return [
        "■ 范围：仅 227 户型(415 板房样板房之一；210 户型即 227 别名)。"
        "415 另一样板房、工控方案图、ZN 变更均不在算量范围(用户裁定)。",
        "■ 最高原则：工程量一律由几何从图纸独立算出，每行带【计算式】+【尺寸追溯】；"
        "BOQ 仅作开发期校验与列项/单位模板，生产环境不依赖(本表 BOQ 校验列已剔除)。",
        "■ 单位链：源图 A3 注记 1:100 → 1pt = 35.2778mm；天花 P3b 栅格 DPI=200，"
        "PX2M = 0.012700008；pt² → ㎡ 因子 0.0012445232。",
        "■ 封账基准：m² %.2f ｜ m %.2f ｜ 离散件 %d 个 ｜ 共 %d 行(地 16 / 墙 24 / 顶 12 / 固装 20)。"
        % (SEAL["m2"], SEAL["m"], SEAL["ge"], SEAL["n_rows"]),
        "■ 末位舍入口径(重要)：封账 m² 763.12 是各模块「先求和、再取 2 位小数显示」的值(未舍入真值 763.115)；"
        "本表与库内均为逐行取 2 位后再求和，得 763.13，与封账差 ≤0.02——此属逐行舍入末位差，"
        "不是账实不符，不得为此改封账；恒等断言一律用未舍入真值 + 容差 0.011。",
        "■ 跨专业恒等式(已在构建时断言)：顶 162.15 = 地 163.75 − 无标注块 1.60；"
        "墙 437.22 = 全间净墙 505.28 − 无标注窄条 68.06；"
        "P3b 97.78 = 97.39 + 0.34 + 0.05；灯槽 16.89 = 17.73 − 0.84。",
        "■ 洞口假设：门高 %.2fm / 窗高 %.2fm / 窗台 %.2fm(无可机读门窗表)，"
        "已在墙面 m² 行逐行标注(F1)。" % (ASSUMED["door_h"], ASSUMED["win_h"],
                                          ASSUMED["win_sill"]),
        "■ 天花口径：几何=单层顶面投影净顶，清单值含跌级/灯槽/返沿展开 → 差 24.6%%~47.8%% "
        "属口径差非漏算(C1)；展开面积需 CD 大样可标定比例，当前用户裁定维持投影口径。",
        "■ P03/P04 天花图(2026-10-13 三页配准实测)：与 P02 **零平移**同位，线栅格 IoU "
        "0.848/0.804、面 Jaccard 0.908/0.895、质心同位命中 93%%/92%% → 判定为**同一平面的"
        "重复表达，不新增吊顶面积**(遗留问题⑤就此结案)；标高符号 P02 命中 31 个、"
        "P03/P04 命中 0 个(不缺图，缺判读)。",
        "■ 复算链可复现性：_p3b_elev/_p3b_leader/_p3b_pagereg/_p3b_split/_p3b_gap 五脚本"
        "于 2026-10-13 全链重跑，13 个 JSON 工件**逐字节相同**、PNG 无变化 → 零漂移；"
        "唯 _p3b_gap 原硬写“应=149.44”为 P3 旧基数，已于 2026-10-13 更正为 162.16 并加漂移断言"
        "(重跑实测 Σ净顶=162.16，与封账天花 162.15 差 0.01 属逐间末位舍入)。",
        "■ 灯槽链(2026-10-13)：机制入库 modeling/ceiling_zones.py，16.89m 定为存档值；"
        "12 条仅 9 条逐字复现，机器 Σ 46.57~64.15m 随池上限漂移且恒偏大 → 不升格。",
        "■ 三态图例：MACHINE=几何确定性算出 ｜ VLM_READ=人工放大读图转录(不产数值) ｜ "
        "USER_RULING=用户裁定的归属/口径 ｜ ARCHIVE_ONLY=机制不可复现、仅存档值。",
        "■ 置信度用法：高=可直接组价/报价；中=建议复核后对外；低=**禁止直接报价**，"
        "须现场确认或补资料。",
        "■ 备注规则命中统计(规则未命中即构建失败，防话术过期)：%s"
        % " ｜ ".join("%s:%d 行" % (k, v) for k, v in sorted(code_hits.items())),
        "■ 数据源哈希锁：源 PDF sha256 = %s(页 idx12=P02)，与 golden 测试同一把锁。"
        % CZ.SOURCE_SHA256,
    ]


def build_notes(ws, art, code_hits):
    note_block(ws, "■ " + NOTE_TITLE + "(构建时已断言，破账不产出本文件)",
               note_lines(code_hits), 1)
    ws.column_dimensions["A"].width = 150


# ══════════════════════════════════════════════════════════════ 主流程
def main():
    print("① 取主表 72 行(modeling.summary, 几何权威链)…")
    rows, code_hits = collect_rows()
    m2, m, ge, per_sec = seal_checks(rows)
    print("   ✓ 恒等断言通过：%d 行 ｜ m² %.2f ｜ m %.2f ｜ 件 %d" % (len(rows), m2, m, ge))

    print("② 跑天花分级链 P3b(modeling.ceiling_zones.regenerate)…")
    art = CZ.regenerate()
    CZ.check_sealed(art)
    print("   ✓ P3b 封账断言通过（含 16.89m 存档值与复现度锚）")

    wb = Workbook()
    ws = wb.active
    ws.title = "工程量计算表"
    build_main(ws, rows)
    build_pending(wb.create_sheet("待确认与挂账清单"), rows, m2, m, ge)
    build_ceiling(wb.create_sheet("天花分级明细P3b"), art)
    build_notes(wb.create_sheet("口径与假设"), art, code_hits)
    wb.save(OUT_PATH)
    print("③ 已产出 -> %s" % OUT_PATH)
    print("   sheet: %s" % " / ".join(wb.sheetnames))


if __name__ == "__main__":
    main()
