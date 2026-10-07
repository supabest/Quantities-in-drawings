#!/usr/bin/env python
"""MEP 封账数字 golden 回归测试（M4 定案口径）。

两层：
  Tier A 工件一致性 —— 校验已封账工件 json 的内部数字（工件在库即可跑，CI 适用）；
  Tier B PDF 复算   —— 从施工图纸 PDF 重新提取并比对（图纸不入库，仅本地有图时跑）。

改动任何提取算法后先跑本测试；数值来源见 outputs/model/mep_takeoff_m{2,3,4}.md。
"""
import json
import math
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "outputs", "preview", "vlm", "mep")
PDF = os.path.join(ROOT, "装修图纸", "227户型dxf",
                   "广州珠实金融城住宅项目--227户型施工图纸.pdf")

# ── M4 定案清单（封账值） ──────────────────────────────────────────────
SEALED = {
    "ld_count": 32,          # P05 标签 32（缺03）＝ P07 注记并集 32（缺22）
    "panels": 15,            # P05/P06/P07 三页逐字相同的控制回路注记实体
    "control_wires": 93,     # Σ每面板受控对象数（两地控制分开计线，上界定案）
    "sprinkler_down": 24,
    "sprinkler_side": 9,
    "vents_S": 7, "vents_R": 7, "vents_E": 4,
    "usb": 6,                # P08 通注"共配置6个"；图上第7锚=双注
    "p08_anchors": 86,       # P08 注记锚点（USB 去重后 85 件）
    "mep_total_wifi": 6, "mep_total_mc": 6,
}


def _load(name):
    return json.load(open(os.path.join(ART, name), encoding="utf-8"))


# ── Tier A：工件一致性（工件在库即可跑） ──────────────────────────────
@pytest.mark.skipif(not os.path.exists(os.path.join(ART, "m3_circuits.json")),
                    reason="封账工件 m3_circuits.json 不在库")
def test_m3_circuits_sealed():
    j = _load("m3_circuits.json")
    panels = j["panels"]
    n_wires = sum(p["n_wires"] for p in panels)
    assert len(panels) == SEALED["panels"], f"面板数 {len(panels)} != 封账 {SEALED['panels']}"
    assert n_wires == SEALED["control_wires"], f"控制线 {n_wires} != 封账 {SEALED['control_wires']}"
    assert j["total_control_wires"] == n_wires  # 工件内部自洽


@pytest.mark.skipif(not os.path.exists(os.path.join(ART, "p24_sprinkler_split.json")),
                    reason="封账工件 p24_sprinkler_split.json 不在库")
def test_sprinkler_split_sealed():
    j = _load("p24_sprinkler_split.json")
    assert len(j["down"]) == SEALED["sprinkler_down"]
    assert len(j["side"]) == SEALED["sprinkler_side"]
    assert len(j.get("smoke_candidates", [])) == 0  # 烟感=图上没有


@pytest.mark.skipif(not os.path.exists(os.path.join(ART, "m2_counts_clean.json")),
                    reason="封账工件 m2_counts_clean.json 不在库")
def test_p08_anchor_count_sealed():
    j = _load("m2_counts_clean.json")
    total = sum(v["total"] for v in j["18"].values())
    assert total == SEALED["p08_anchors"], f"P08 锚点 {total} != 封账 {SEALED['p08_anchors']}"
    assert j["18"]["USB插座"]["total"] == 7  # 锚点7 → 去重后 6（通注定案）


# ── Tier B：PDF 全量复算（图纸仅本地，不入库） ────────────────────────
pdf_only = pytest.mark.skipif(not os.path.exists(PDF), reason="施工图纸 PDF 不在本地")


@pdf_only
def test_p05_ld_labels_recount():
    import fitz
    doc = fitz.open(PDF)
    labels = set()
    for b in doc[15].get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                t = "".join(s["text"].split())
                if re.fullmatch(r"(2w)?LD-\d{1,2}", t) and (s["bbox"][0] + s["bbox"][2]) / 2 > 262:
                    labels.add(t.upper())
    uniq = {re.sub(r"^2W", "", t) for t in labels}
    assert len(uniq) == SEALED["ld_count"]


@pdf_only
def test_p14_vent_anchors_recount():
    import fitz
    doc = fitz.open(PDF)
    for idx, tag in ((13, "P13"), (24, "P14")):
        cnt = {"S": 0, "R": 0, "E": 0}
        for b in doc[idx].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    t = "".join(s["text"].split())
                    x, y = (s["bbox"][0] + s["bbox"][2]) / 2, (s["bbox"][1] + s["bbox"][3]) / 2
                    if t in cnt and x > 262 and y < 815:
                        cnt[t] += 1
        want = {"S": SEALED["vents_S"], "R": SEALED["vents_R"], "E": SEALED["vents_E"]}
        assert cnt == want, f"{tag} 风口 {cnt} != 封账 {want}"


@pdf_only
def test_p07_circuit_union_covers_32():
    import fitz
    doc = fitz.open(PDF)
    ld = set()
    for b in doc[17].get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"])
            if "控制回路" not in t:
                continue
            for m in re.finditer(r"LD-?(\d{1,2})(?:~(?:LD-?)?(\d{1,2}))?", t.replace(" ", "")):
                a, b2 = int(m.group(1)), int(m.group(2) or m.group(1))
                ld.update(range(a, b2 + 1))
    assert len(ld) == SEALED["ld_count"]
    assert 22 not in ld and 3 in ld  # M4 挂账：LD-22 无回路注记、03 仅存在于范围写法


@pdf_only
def test_p08_usb_dedup_sealed():
    j = _load("m2_counts_clean.json")
    seen = []
    for _mult, bbox in j["18"]["USB插座"]["items"]:
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        if not any(math.hypot(cx - ux, cy - uy) < 15 for ux, uy in seen):
            seen.append((cx, cy))
    assert len(seen) == SEALED["usb"], "USB 去重后应为通注的 6 个"


# ── M5：P08/P09 引线追踪归属（mep_takeoff_m5.md） ─────────────────
@pytest.mark.skipif(not os.path.exists(os.path.join(ART, "m5_zero_shift_evidence.json")),
                    reason="M5 工件不在库")
def test_m5_zero_shift_evidence():
    j = _load("m5_zero_shift_evidence.json")
    # 零平移：P08/P09 最强峰相对 P01 平移 <1pt（双平面误判已勘误）
    for k in ("P08", "P09"):
        sx, sy = j[f"{k}_peak_shift_pt"]
        assert abs(sx) < 1.0 and abs(sy) < 1.0, f"{k} 子平面平移非零，标定复用前提破坏"


@pytest.mark.skipif(not os.path.exists(os.path.join(ART, "m5_P08_rows.json")),
                    reason="M5 工件不在库")
def test_m5_attribution_totals_match_m2():
    """引线归属不改变量：每类 Σn 必须等于 M2 封账计数。"""
    m2 = _load("m2_counts_clean.json")
    rows = _load("m5_P08_rows.json")
    got = {}
    for r in rows:
        got[r["kind"]] = got.get(r["kind"], 0) + r["n"]
    for kind, v in got.items():
        assert v == m2["18"][kind]["total"], f"P08 {kind} 归属Σ{v} != M2 封账 {m2['18'][kind]['total']}"
    rows9 = _load("m5_P09_rows.json")
    t9 = _load("m2_textcounts.json")["19"]["counts"]
    got9 = {}
    for r in rows9:
        got9[r["kind"]] = got9.get(r["kind"], 0) + r["n"]
    for kind, v in got9.items():
        assert v == t9[kind], f"P09 {kind} 归属Σ{v} != M2 封账 {t9[kind]}"


@pytest.mark.skipif(not os.path.exists(os.path.join(ART, "m5_P09_rows.json")),
                    reason="M5 工件不在库")
def test_m5_semantic_anchors():
    """语义铁律抽查：燃气/洗衣机在生阳台，四浴室各有且仅有 1 马桶角阀。"""
    rows9 = _load("m5_P09_rows.json")
    room = {r["kind"]: r["room"] for r in rows9 if r["room"] != "未能归属"}
    assert room["燃气点"] == "生活阳台"
    assert room["洗衣机龙头"] == "生活阳台"
    assert room["浴缸龙头"] == "主人浴室"
    toilets = [r["room"] for r in rows9 if r["kind"] == "马桶/喷枪角阀" and r["room"] != "未能归属"]
    assert sorted(toilets) == sorted(["主人浴室", "浴室一", "浴室二", "浴室三"]), "四浴室应各 1 马桶角阀"

