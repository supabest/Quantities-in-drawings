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
CEILING = os.path.join(MD, "ceiling_takeoff.md")
DRAWINGS = os.path.join(ROOT, "装修图纸", "227户型dxf")

# ── 封账清单（手册 §7.4 / §4.5 / §5.3 定案值） ────────────────────────
SEALED = {
    "floor_m2": 163.75,      # P1 地面·16行
    "wall_m2": 437.22,       # P2 墙面基层·12行（已按逐间主吊顶净高，非一刀切3020；
                             #   2026-10-12 用户裁定补计电梯厅36.13+过厅9.41，原 391.68·10行）
    "skirting_m": 166.73,    # P2 踢脚线·12行（原 148.14·10行，补计 15.11+3.48）
    "ceiling_m2": 162.15,    # P3 天花净顶投影·12行（2026-10-12 用户裁定补计电梯厅11.63+过厅1.08；
                             #   原 149.44·10行系 BOQ ROOM_MAP 无该列→静默丢弃；恒等式
                             #   162.15 = P1 163.75 − 无标注块 1.60 精确闭合）
    "furniture_pcs": 21,     # P4 固装·20行（含餐厅柜×2；柜体 m² 不可自动提取，见 §6.3）
    "total_m2": 763.12,
    "total_rows": 72,
    "perimeter_qty": 210.02,   # P2c 高度无关护栏：算量 Σ 边
    "perimeter_ifc": 210.10,   # 模型 IfcSpace 边长汇总
    "wall_gross_qty": 598.87,  # 毛墙(主吊顶净高)
    "wall_gross_ifc": 599.13,  # 模型 IfcSpace 顶高毛墙 → 验吊顶标高已入模
    "wall_net_qty": 505.28,    # 净墙 = 毛墙 − 洞口
    "openings_m2": 93.59,
    "ceiling_zones_m2": 97.78,  # P3b P02 已确认分级水平面 Σ（21 域 = 原 19 洪水域，域119 切分为 3）
    "zone_2650_m2": 6.58,       # +2650 分级 Σ（含 2026-10-12 用户裁定并入的龛左窄带 0.34）
    "cove_accepted_m": 16.89,   # 暗藏灯槽采信长度（12 链 17.73 剔除 #10 入户门弧弦 0.84）
                                # ⚠ 存档值，非机器值：机制已重建但 12 条只逐字复现 9 条（§7.5）
    "split_119_niche_m2": 4.37,  # 域119 龛内(+2650)=3.77+0.60（已由 ceiling_zones 机器复现）
    "split_119_right_m2": 1.52,  # 域119 右带(+2600)（机器原值 6.28 经切线 x=564.0/637.9 拆得）
    "ceiling_bands_m2": 2.25,    # P3b 跌级侧带下界 Σ（确证邻接 6 对；已由 ceiling_zones 机器复现）
    "ceiling_bands_n": 6,        # 侧带对数（域各膨胀 12px 后仍不接触的界面一律不计）
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
    """'**763.12**' / '163.75' / '一致(-0.0%)' → float；取不到返回 None。"""
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
    """P3b 分级水平面：明细/分级两表**逐标高组**互约，且与 P3 投影口径关系已披露。

    2026-10-12 用户裁定后：0.34㎡ 已从挂账归入 +2650，因此不得再出现挂账行；
    分组校验必须逐标高做，否则“把面积记错标高”这类错仅靠总 Σ 看不出来。
    """
    _need_reports()
    head = open(ZONES, encoding="utf-8").read()
    m = re.search(r"已确认[^\n]*?Σ=([\d.]+)㎡", head)
    assert m and float(m.group(1)) == SEALED["ceiling_zones_m2"], "分级水平面封账 Σ 变了"

    zone_rows = [c for c in _rows(ZONES, section="## 1. 水平分区面", npage=5)]
    amounts = [_num(c[2]) for c in zone_rows if _num(c[2]) is not None]
    assert len(amounts) == 21, f"域行数应 21（原 19 洪水域+切分 2）, 实得{len(amounts)}"
    assert abs(sum(amounts) - SEALED["ceiling_zones_m2"]) < TOL_ZONE, sum(amounts)

    # 挂账必须已清零（待裁定域不得静默漂在账上）
    hang = [c for c in zone_rows if c[1] == "挂账"]
    assert not hang, f"存在未裁定挂账域: {hang}"

    def _lvl_key(s):
        return re.sub(r"[（(].*?[）)]", "", s).strip()     # 分级表键带“（窗帘盒）”等后缀

    detail = {}
    for c in zone_rows:
        if _num(c[2]) is not None:
            detail[_lvl_key(c[1])] = round(detail.get(_lvl_key(c[1]), 0.0) + _num(c[2]), 4)
    grouped = {_lvl_key(c[0]): _num(c[1])
               for c in _rows(ZONES, section="分级合计", npage=2) if _num(c[1])}
    assert set(detail) == set(grouped), (set(detail) ^ set(grouped))
    # 逐组容差 0.02：两表各自按两位小数舍入，累加允许漂（+2600 组实测差 0.01）
    drift = {k: (detail[k], grouped[k]) for k in detail if abs(detail[k] - grouped[k]) > 0.02}
    assert not drift, f"明细分组 Σ 与分级合计表漂移: {drift}"
    assert abs(grouped["+2650"] - SEALED["zone_2650_m2"]) < 0.02, grouped["+2650"]

    # 与 P3 投影的差额必须逐行披露，不得静默吞掉。
    # 2026-10-12: 原断言把“51.66”写死为字面量，P3 补计电梯厅/过厅后基数一变就假失败；
    #   改为仍从两份报告实算差额校验（护栏变强：同时验证 ceiling_takeoff.md 的合计就是封账值）。
    p3txt = open(CEILING, encoding="utf-8").read()
    m3 = re.search(r"\*\*m2 合计 ([\d.]+)\*\*", p3txt)
    assert m3, "ceiling_takeoff.md 未读到 m2 合计"
    assert abs(float(m3.group(1)) - SEALED["ceiling_m2"]) < TOL, \
        f"P3 报告合计 {m3.group(1)} 与封账 {SEALED['ceiling_m2']} 不符"
    gap = round(float(m3.group(1)) - SEALED["ceiling_zones_m2"], 2)
    assert f"{gap:.2f}" in head, \
        f"ceiling_zones.md 未披露与 P3 投影 {m3.group(1)}㎡ 的差额 {gap:.2f}㎡"
    assert f'{SEALED["ceiling_zones_m2"]}㎡' in head


def test_ceiling_sidebands_are_lower_bound_and_self_consistent():
    """P3b 跌级侧带：报告 §2 明细 = 封账 2.25㎡，且每行 A=L×Δh 自洽（Tier A）。

    侧带是**下界**：只有“域各膨胀 12px 后确证接触”的界面才计，其余如实留空；
    不得反过来把它当完整展开面积。**封账 2.25 不是猜出来的，而是“至少这么多”。**
    """
    _need_reports()
    head = open(ZONES, encoding="utf-8").read()
    rows = [c for c in _rows(ZONES, section="跌级侧带", npage=5)
            if _num(c[2]) is not None and _num(c[4]) is not None]
    assert len(rows) == SEALED["ceiling_bands_n"], \
        f"确证邻接侧带应为 {SEALED['ceiling_bands_n']} 对, 实得{len(rows)}"
    tot = sum(_num(c[4]) for c in rows)
    assert abs(tot - SEALED["ceiling_bands_m2"]) < 0.005, f"侧带 Σ{tot:.3f} vs 封账 2.25"
    for c in rows:                       # 逐行恒等式：A = L×Δh（不得有裸数字行）
        # 容差 0.002：报告只披露两位小数的 L，0.005×Δh(≤0.35) 的舍入误差会泄进 A
        #   （实测 3.96×0.35=1.386 vs 行值 1.387），不是算错。
        assert abs(_num(c[2]) * _num(c[3]) - _num(c[4])) < 0.002, c
    assert "下界" in head and "不猜" in head, "侧带口径（下界/不猜）未披露"


def test_ceiling_cove_and_rulings_are_tracked():
    """2026-10-12 用户裁定必须可追溯：灯槽采信值、裁定条目、门窗假设口径。"""
    _need_reports()
    head = open(ZONES, encoding="utf-8").read()
    m = re.search(r"采信 Σ=([\d.]+)m", head)
    assert m and float(m.group(1)) == SEALED["cove_accepted_m"], \
        f"灯槽采信长度应为 {SEALED['cove_accepted_m']}m, 文内读出 {m and m.group(1)}"
    assert "入户门弧弦" in head and "不计入灯槽" in head, "#10 剔除依据未披露"
    assert "用户裁定（2026-10-12）" in head, "10-12 裁定条目不在账本里"
    # 裁定后的分级总面积不得因“挂账改归属”而漂（只换组不改量）
    assert abs(SEALED["zone_2650_m2"] - 0.34 - 6.24) < 1e-9, \
        "0.34 并入前 +2650 应为 6.24（裁定只改归属不改面积）"


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


@pytest.mark.skipif(not os.path.isdir(DRAWINGS), reason="图纸未入库，仅本地可复算")
@pytest.mark.slow
def test_ceiling_zones_chain_rerun_matches_seal():
    """P3b 天花分级吊顶**复算链**重跑 = 封账（Tier B）。

    此链以前只存在于报告文本与若干**无生成器**的 JSON 里（2026-10-12 实测：
    elev/ 下 12 个工件有 7 个全仓无 .py 引用，含 97.78 的载体）——本测试把
    它钉成可重跑、可报错的单一入口，并逆验数据哈希锁与三类值分离契约。
    """
    from modeling import ceiling_zones as CZ
    CZ.verify_source()                       # 源图纸指纹不符即抛异常
    art = CZ.regenerate()
    assert art["meta"]["source_sha256"] == CZ.SOURCE_SHA256
    s = CZ.check_sealed(art, sealed_total=SEALED["ceiling_zones_m2"])   # 内含断言
    assert abs(s - SEALED["ceiling_zones_m2"]) < TOL, s
    # 切分硬值逐字复现（龛内 4.37 / 右带 1.52）
    sp = art["split_119"]["machine_grouped"]
    assert sp["龛内(+2650,⊕14)"] == SEALED["split_119_niche_m2"]
    assert sp["右带(+2600,⊕26)"] == SEALED["split_119_right_m2"]
    # 三类值分离契约：标高数字全部标 VLM_READ，且保留“未能读取”这一态（不猜）
    prov = {r["provenance_v"] for r in art["rows"]}
    assert prov == {"VLM_READ"}, prov
    vals = {r["m"]: r["v"] for r in art["rows"]}
    assert vals[30] is None, "⊕30 应=未能读取（三态），不得被填值"
    assert vals[22] == "SLAN-100", "⊕22 是灯槽物料代号非标高"
    # 侧带链（2026-10-13 重建）：周长/接触长度/面积全机器值，check_sealed 已逐字比对存档参照；
    # 此处只绑 golden 总量，并验证“剔除不静默”：每一对被丢下的界面都必须带原因。
    zb, bb = art["zone_bands"], art["bands_bridge"]
    assert abs(zb["sum_band_lower"]) < 1e-9, "直接邻接下界应=0（洪水域必被墨线隔开）"
    assert len(bb["bands"]) == SEALED["ceiling_bands_n"], bb["bands"]
    assert abs(bb["sum_band_m2"] - SEALED["ceiling_bands_m2"]) < 0.005, bb["sum_band_m2"]
    assert bb["skipped_pairs"] and all(s.get("why") for s in bb["skipped_pairs"]), \
        "剔除的域对必须逐对披露原因"
    unconf = set(int(k) for k in bb["level_unconfirmed"])
    hit = [s for s in bb["skipped_pairs"] if {s["z1"], s["z2"]} & unconf]
    assert unconf == {840} and hit, "未经互证域（+2450）的剔除必须存在且可追溯"
    assert all({840} & {b["z1"], b["z2"]} == set() for b in bb["bands"]), "840 不得出现在采信侧带里"
    # 灯槽链（2026-10-13 重建机制）：它是一块**反例**——机制可复现、数值不可升格。
    # 此处只绑三件事：存档内部恒等式、固定的 9/12 逐字复现名单、以及“机器值不得越封账”。
    dc = art["dash_chains"]
    assert len(dc["archive_accepted"]) == 12, len(dc["archive_accepted"])
    assert abs(dc["archive_raw_sum_m"] - 17.73) < 0.005, dc["archive_raw_sum_m"]
    assert abs(dc["archive_sealed_m"] - SEALED["cove_accepted_m"]) < 0.005, \
        "16.89 必须恰等于存档 Σ(12) 剔除 #10"
    assert tuple(dc["reproduced_idx"]) == CZ.COVE_REPRO_IDX, dc["reproduced_idx"]
    assert abs(dc["reproduced_span_m"] - CZ.COVE_REPRO_SPAN_M) < 0.005
    assert dc["n_chains"] == CZ.COVE_MACHINE_ANCHOR["n_chains"] \
        and dc["n_accepted"] == CZ.COVE_MACHINE_ANCHOR["n_accepted"], \
        "桶宽/池窗/门槛漂移（机器条数与 Σ 不得只靠文字记忆守）"
    assert dc["machine_sum_m"] > SEALED["cove_accepted_m"], \
        "机器 Σ 竟≤封账：截断口径可能被凑出来了，须人工重估而不是直接采信"
    assert "存档值" in dc["verdict"] and dc["rejected_causes"] and "存档值" in dc["provenance"], \
        "不可复现部分必须写进口径，不得只留在注释里"
    assert dc["machine_sum_m"] > dc["reproduced_span_m"], "机器 Σ 应大于逐字复现部分"
    # 几何字段全部来于机器，不得含人工录入痕迹
    assert all(r["provenance_geom"] == "MACHINE" for r in art["rows"])
