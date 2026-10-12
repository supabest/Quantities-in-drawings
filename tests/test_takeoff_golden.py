#!/usr/bin/env python
"""装修算量封账数字 golden 回归测试（P1~P5 + P2c/P3b 定案口径）。

两层（与 tests/test_mep_golden.py 同构）：
  Tier A 报告一致性 —— 解析已入库的 outputs/model/*.md，校验封账值与内部恒等式
                       （报告在库即可跑，CI 适用，无需图纸）；
  Tier B 全链复算   —— 重跑 modeling.summary / modeling.wall_vs_ifc 与报告比对
                       （图纸不入库，仅本地有图时跑，slow 排除）。

数值来源：docs/QUANTITY_TAKEOFF_MANUAL.md §3.4/§4.5/§5.3/§7.4，
产物：outputs/model/{TAKEOFF_SUMMARY,wall_vs_ifc,ceiling_zones}.md。

纪律锚（R9/R14）：几何为唯一数值源，每行必须带【几何计算式】+【尺寸追溯】；
BOQ 校验列仅开发期交叉校验，不得充当取数来源。

容差说明：各报告逐行按两位小数四舍五入后相加，与合计行可差 ±0.02；
ceiling_zones 分级合计与原洪水填充 Σ 差 0.06（文档已注明=切割线像素）。
"""
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD = os.path.join(ROOT, "outputs", "model")
SUMMARY = os.path.join(MD, "TAKEOFF_SUMMARY.md")
IFC_XCHK = os.path.join(MD, "wall_vs_ifc.md")
ZONES = os.path.join(MD, "ceiling_zones.md")
DRAWINGS = os.path.join(ROOT, "装修图纸", "227户型dxf")

# ── 封账清单（手册 §7.4 / §4.5 / §5.3 定案值） ────────────────────────
SEALED = {
    "floor_m2": 163.75,      # P1 地面·16行
    "wall_m2": 391.68,       # P2 墙面基层·10行（已按逐间主吊顶净高，非一刀切3020）
    "skirting_m": 148.14,    # P2 踢脚线·10行
    "ceiling_m2": 149.44,    # P3 天花净顶投影·10行
    "furniture_pcs": 21,     # P4 固装·20行（含餐厅柜×2；柜体 m² 不可自动提取，见 §6.3）
    "total_m2": 704.87,
    "total_rows": 66,
    "perimeter_qty": 210.02,   # P2c 高度无关护栏：算量 Σ 边
    "perimeter_ifc": 210.10,   # 模型 IfcSpace 边长汇总
    "wall_gross_qty": 598.87,  # 毛墙(主吊顶净高)
    "wall_gross_ifc": 599.13,  # 模型 IfcSpace 顶高毛墙 → 验吊顶标高已入模
    "wall_net_qty": 505.28,    # 净墙 = 毛墙 − 洞口
    "openings_m2": 93.59,
    "ceiling_zones_m2": 97.78,  # P3b P02 已确认分级水平面 Σ（19 域）
    "zone_split_hang": 0.34,    # 域119 左窄带无符号 → 挂账不计
}
TOL = 0.02       # 逐行四舍五入累加
TOL_ZONE = 0.07  # 洪水填充像素切割 0.06 + 四舍五入


def _rows(path, section=None, npage=0):
    """取 md 中的表格数据行（跳过表头/分隔行）；section 为标题或加粗段落片段。

    章节边界 = 以 '#' 开头的标题行 **或** 以 '**' 开头的加粗段落行
    （ceiling_zones 的"分级合计"表挂在加粗小标题下，不是 markdown 标题）。
    """
    out, in_sec = [], section is None
    for line in open(path, encoding="utf-8"):
        if line.startswith("#") or line.startswith("**"):
            in_sec = section is None or section in line
            continue
        if not in_sec or not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set("".join(cells)) <= set("-: "):
            continue
        if npage and len(cells) != npage:
            continue
        out.append(cells)
    return out


def _num(text):
    """'**704.87**' / '163.75' / '一致(-0.0%)' → float；取不到返回 None。"""
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace("*", ""))
    return float(m.group()) if m else None


def _need_reports():
    missing = [p for p in (SUMMARY, IFC_XCHK, ZONES) if not os.path.exists(p)]
    if missing:
        pytest.skip("算量报告未入库: " + ", ".join(os.path.basename(m) for m in missing))


# ── Tier A：报告一致性（报告在库即可跑） ─────────────────────────────
def test_sealed_professional_totals():
    """手册 §7.4 四专业小计与合计一字未动。"""
    _need_reports()
    got = {}
    for c in _rows(SUMMARY, section="## 专业汇总", npage=5):
        if _num(c[1]) is None:            # 表头行「专业|m²小计|…」无数值
            continue
        key = c[0].replace("*", "")
        got[key] = [_num(c[1]), _num(c[2]), _num(c[3]), _num(c[4])]
    assert got["地面 (P1)"][0] == SEALED["floor_m2"], got["地面 (P1)"]
    assert got["墙面 (P2)"][:2] == [SEALED["wall_m2"], SEALED["skirting_m"]], got["墙面 (P2)"]
    assert got["天花 (P3)"][0] == SEALED["ceiling_m2"], got["天花 (P3)"]
    assert got["固定家具/柜台 (P4)"][2] == SEALED["furniture_pcs"], got["固定家具/柜台 (P4)"]
    total = got["合计"]
    assert total[:3] == [SEALED["total_m2"], SEALED["skirting_m"], SEALED["furniture_pcs"]], total
    assert total[3] == SEALED["total_rows"], total
    # 合计恒等：三专业 m² 相加 = 合计行；行数 = 各专业行数之和
    assert abs(sum(v[0] for k, v in got.items() if k != "合计") - total[0]) < TOL
    assert sum(v[3] for k, v in got.items() if k != "合计") == total[3]


@pytest.mark.parametrize("section,unit,expect", [
    ("## 地面", "m2", SEALED["floor_m2"]),
    ("## 天花", "m2", SEALED["ceiling_m2"]),
    ("## 墙面", "m2", SEALED["wall_m2"]),
    ("## 墙面", "m", SEALED["skirting_m"]),
    ("## 固定家具", "个", SEALED["furniture_pcs"]),
])
def test_section_rows_sum_to_sealed_subtotal(section, unit, expect):
    """封账合计必须真等于明细行之和——防'只改汇总不改明细'。"""
    _need_reports()
    vals = [_num(c[4]) for c in _rows(SUMMARY, section=section, npage=8)
            if c[2] == unit and _num(c[4]) is not None]
    assert len(vals) >= 10, f"{section}/{unit} 明细行不足: {len(vals)}"
    assert abs(sum(vals) - expect) < TOL, f"{section} {unit}: Σ{sum(vals):.2f} vs 封账 {expect}"


def test_every_quantity_row_carries_formula_and_trace():
    """R9/R14 纪律：每行工程量都要带几何计算式与尺寸追溯，不得裸数字。"""
    _need_reports()
    bare = []
    for c in _rows(SUMMARY, npage=8):
        if c[0] in ("专业", "部位") or c[0].startswith("地面 (") or c[0].startswith("**合计"):
            continue
        if _num(c[4]) is None:      # 非数量行(汇总/说明)
            continue
        if len(c[3]) < 2 or len(c[5]) < 2:
            bare.append((c[0], c[1]))
    assert not bare, f"缺计算式/追溯的行: {bare}"


def test_ifc_crosscheck_height_agnostic_guard():
    """P2c 三维校验三条护栏：围合周长(高度无关)、吊顶传播、毛净墙洞口恒等。"""
    _need_reports()
    rows = _rows(IFC_XCHK, npage=8)
    def row_of(keyword):
        hit = [c for c in rows if keyword in c[1]]
        assert hit, f"wall_vs_ifc.md 缺'{keyword}'行"
        return hit[0]

    per = row_of("围合周长合计")
    assert _num(per[4]) == SEALED["perimeter_qty"], per
    assert _num(per[6]) == pytest.approx(SEALED["perimeter_ifc"]), per
    assert abs(_num(per[6]) - _num(per[4])) / _num(per[4]) < 0.001, "围合护栏偏差>0.1%"
    # 逐间 Σ 边 = 合计(允许四舍五入)
    each = [_num(c[4]) for c in rows if "模型↔算量·" in c[0]]
    assert len(each) == 16, f"房间数应为16, 实得{len(each)}"
    assert abs(sum(each) - SEALED["perimeter_qty"]) < 0.05, sum(each)

    gross = row_of("算量毛墙合计")
    assert _num(gross[4]) == SEALED["wall_gross_qty"], gross
    assert _num(gross[6]) == SEALED["wall_gross_ifc"], gross
    net = row_of("算量净墙合计")
    assert _num(net[4]) == SEALED["wall_net_qty"], net
    assert abs(SEALED["wall_gross_qty"] - SEALED["openings_m2"] - SEALED["wall_net_qty"]) < TOL
    assert f'{SEALED["openings_m2"]}' in net[5], f"洞口面积未在追溯列披露: {net}"


def test_ceiling_zone_reconciliation_closes():
    """P3b 分级水平面：明细/分级两表互相一致，且与 P3 投影口径关系已披露。"""
    _need_reports()
    head = open(ZONES, encoding="utf-8").read()
    m = re.search(r"已确认[^\n]*?Σ=([\d.]+)㎡", head)
    assert m and float(m.group(1)) == SEALED["ceiling_zones_m2"], "分级水平面封账 Σ 变了"

    zone_rows = [c for c in _rows(ZONES, section="## 1. 水平分区面", npage=5)]
    amounts = [_num(c[2]) for c in zone_rows if _num(c[2]) is not None]
    assert abs(sum(amounts) - SEALED["ceiling_zones_m2"]) < TOL_ZONE, sum(amounts)
    # 挂账行(图上无符号)必须在明细中标注且不计入标高分组
    hang = [c for c in zone_rows if c[1] == "挂账"]
    assert len(hang) == 1 and _num(hang[0][2]) == SEALED["zone_split_hang"], hang
    # 分级合计表只列有标高域(不含挂账0.34)→ 加回挂账后必须等于明细 Σ，两表不得各自漂移
    lvl = [_num(c[1]) for c in _rows(ZONES, section="分级合计", npage=2) if _num(c[1])]
    assert lvl and abs(sum(lvl) + SEALED["zone_split_hang"] - sum(amounts)) < 0.05, \
        (sum(lvl), sum(amounts))
    # 与 P3 投影的差额必须逐行披露，不得静默吞掉
    assert "51.66" in head and f'{SEALED["ceiling_zones_m2"]}㎡' in head


@pytest.mark.skipif(not os.path.isdir(DRAWINGS), reason="图纸未入库，仅本地可复算")
@pytest.mark.slow
def test_summary_rerun_matches_seal():
    """全链重跑 = 封账（Tier B；会重写 TAKEOFF_SUMMARY.md，本地验证用）。"""
    p = subprocess.run([sys.executable, "-m", "modeling.summary"],
                       cwd=ROOT, capture_output=True, text=True, timeout=1800)
    assert p.returncode == 0, p.stdout[-800:] + p.stderr[-800:]
    m = re.search(r"m² 合计 ([\d.]+) \| m 合计 ([\d.]+) \| 离散件 (\d+) 个", p.stdout)
    assert m, p.stdout[-400:]
    assert [float(m.group(1)), float(m.group(2)), int(m.group(3))] == \
        [SEALED["total_m2"], SEALED["skirting_m"], SEALED["furniture_pcs"]], m.groups()


@pytest.mark.skipif(not os.path.isdir(DRAWINGS), reason="图纸未入库，仅本地可复算")
@pytest.mark.slow
def test_wall_vs_ifc_rerun_matches_seal():
    """三维交叉校验重跑 = 封账护栏值（Tier B）。"""
    p = subprocess.run([sys.executable, "-m", "modeling.wall_vs_ifc"],
                       cwd=ROOT, capture_output=True, text=True, timeout=1800)
    assert p.returncode == 0, p.stdout[-800:] + p.stderr[-800:]
    rows = _rows(IFC_XCHK, npage=8)
    per = [c for c in rows if "围合周长合计" in c[1]][0]
    assert _num(per[4]) == SEALED["perimeter_qty"], per
    gross = [c for c in rows if "算量毛墙合计" in c[1]][0]
    assert _num(gross[4]) == SEALED["wall_gross_qty"], gross
