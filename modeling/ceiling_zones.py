"""P3b 天花分级吊顶算量复算链（227 户型 P02 = PDF idx12）。

存在理由：封账数（分级水平面 Σ97.78㎡、+2650=6.58㎡、灯槽采信 16.89m）此前只活在
报告文本和一批**没有生成器**的 JSON 里——2026-10-12 实测：elev/ 下 12 个工件中 7 个
在全仓找不到任何 .py 引用（含 97.78 的直接载体 p02_zones_flood.json）。本模块把
**可由几何确定的部分**固化为可重跑、可校验的单一入口。

三类值严格分离、逐条署名（这是本模块的核心契约，不得混写）：
  MACHINE     由 PDF 矢量/栅格确定性算出：⊕标记位置、闭合面、洪水域 lid 与面积、归属。
  VLM_READ    标高数字由人工放大读图转录——几何只能定位符号，读不出数字。
  USER_RULING 用户裁定的口径/归属：域119 沿床头龛切分、龛左窄带归 +2650、
              ⊕23 泄漏带=暗藏灯带、虚线链#10=入户门弧弦剔出灯槽。

数据哈希锁：源图纸按 .gitignore 刻意不入库（`装修图纸/`），故每个工件写
`meta.source_sha256`；载入或重跑时本地文件指纹与钉值不符 → 立即报错，
禁止拿过期工件出数（范式沿用机电线 mep/arrows.py 的 meta.rows_md5）。

尚未固化的阶段（诚实标注，勿当作已复现）：zone_bands / bands_bridge（侧带 2.25㎡）、
dashes / coves / coves_accepted（灯槽 16.89m）的生成器仍缺，见 TODO_STAGES。

用法: ./venv/bin/python -m modeling.ceiling_zones          # 重跑并校验
      ./venv/bin/python -m modeling.ceiling_zones --check   # 只校验不写盘
"""
import hashlib
import json
import math
import os
import sys

import cv2
import fitz
import numpy as np

PDF = os.path.join("装修图纸", "227户型dxf", "广州珠实金融城住宅项目--227户型施工图纸.pdf")
OUTDIR = os.path.join("outputs", "preview", "vlm", "elev")
PAGE_IDX = 12          # P02 ↔ idx12（图号↔idx 已逐页实测，见手册 §2.1）

# 单位链：A3 1:100 → 0.352778 mm/pt ×100
PT2MM = 0.352778 * 100
DPI = 200
SC = DPI / 72.0
PX2M = 1.0 / SC * PT2MM / 1000.0
PX2M2 = PX2M ** 2
LEAK_M2 = 100.0        # 域面积 > 此值判为泄漏到背景（state=leak_bg），不计入封账

# ── 数据哈希锁 ────────────────────────────────────────────────────────
SOURCE_SHA256 = "0681015aa370fcc8d5b64761218b2bf271f5bceee1755d3ebbfbe8bd0931bc71"


def source_sha256(path=PDF):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def verify_source():
    """源图纸必须与复算链所依据的那一版一致，否则整条链作废。"""
    if not os.path.exists(PDF):
        raise IOError("源图纸缺失（227 图纸集不入库，见 .gitignore）：%s\n"
                      "→ 无法重跑复算链；已入库工件仍带 source_sha256 可校验。" % PDF)
    got = source_sha256()
    if got != SOURCE_SHA256:
        raise ValueError(
            "数据哈希锁失败：源图纸已变更，复算链作废，须重跑并逐值复核。\n"
            "  期望 sha256=%s\n  实际 sha256=%s\n  文件=%s" % (SOURCE_SHA256, got, PDF))
    return got


# ── MACHINE：⊕ 标记定位（黑色双象限填充⊕ = 两个 2~4pt 'fs' 小三角对角配对）──
def marker_positions(page):
    quads = []
    for dr in page.get_drawings():
        if dr.get("fill") is None or dr.get("type") != "fs":
            continue
        r = dr["rect"]
        if 2 <= r.width <= 4 and 2 <= r.height <= 4:
            quads.append(((r.x0 + r.x1) / 2.0, (r.y0 + r.y1) / 2.0))
    used, centers = set(), []
    for i in range(len(quads)):
        for j in range(i + 1, len(quads)):
            if i in used or j in used:
                continue
            (x1, y1), (x2, y2) = quads[i], quads[j]
            if (x1 - x2) ** 2 + (y1 - y2) ** 2 < 25:
                used.add(i); used.add(j)
                centers.append((round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1)))
    return centers


# ── MACHINE：洪水掩膜与连通域（与 _p3b_split.py/封账完全同参）────────────
def flood_mask(page):
    pm = page.get_pixmap(dpi=DPI)
    img = np.frombuffer(pm.samples, np.uint8) \
              .reshape(pm.height, pm.width, pm.n)[:, :, :3].copy()
    blk = cv2.dilate((cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) < 128).astype(np.uint8),
                     np.ones((3, 3), np.uint8), iterations=2)
    n, lab, stats, _ = cv2.connectedComponentsWithStats((1 - blk).astype(np.uint8))
    return lab, stats, blk


def nearest_lid(lab, stats, x, y, max_r=10, min_m2=0.02):
    """点邻域内最近白像素的 lid，不设面积上限（背景由调用方标 leak_bg）。

    ⊕ 符号中心正落在自身墨线上，直取 lab[py,px] 必然命中 label 0（墨线网络），
    故必须做邻域最近白像素探测——2026-10-12 重建时在此踩坑（31 行全错）。
    """
    xi, yi = int(x * SC), int(y * SC)
    r1 = int(max_r * SC)
    y0s, x0s = max(0, yi - r1), max(0, xi - r1)
    win = lab[y0s:yi + r1, x0s:xi + r1]
    blk_w = (lab[y0s:yi + r1, x0s:xi + r1] > 0)
    ys, xs = np.where(win > 0)
    if len(ys) == 0:
        return None
    d2 = (ys - (yi - y0s)) ** 2 + (xs - (xi - x0s)) ** 2
    for k in np.argsort(d2)[:400]:
        lid = int(win[ys[k], xs[k]])
        if float(stats[lid, cv2.CC_STAT_AREA]) * PX2M2 >= min_m2:
            return lid
    return None


# ── MACHINE：沿指定切线重连通，把一个域拆成子块（裁定线为输入，面积由机器算）──
CUT_119_PT = (564.0, 637.9)   # 床头龛边界（USER_RULING 2026-10-04 选线）
# 子块→归属等级的映射也是裁定（几何不知道哪块该归哪个标高）：
SPLIT_119_LID = 119          # 待切分的原域（床头龛未闭合导致的合并域）


def cut_mask(blk, cuts_pt, width_px=3):
    """在栅格上画通高竖线，返回 (n, lab, stats)。"""
    cut = blk.copy()
    for xpt in cuts_pt:
        px = int(round(xpt * SC))
        cut[:, max(0, px - 1):px + width_px - 1] = 1
    return cv2.connectedComponentsWithStats((1 - cut).astype(np.uint8))


def decompose_domain(lid0, lab0, stats0, lab, stats):
    """把原域 lid0 的像素映射到切割后的新标签 → ({子域lid: ㎡}, 切线消耗㎡, 原域㎡)。

    必须用**原域像素集**去裁新标签；若改成遍历新标签的 bbox，会选到整条竖列上
    所有无关域（2026-10-12 重建时实踩过，得 64.86㎡ vs 应≈原域 6.28㎡）。
    """
    raw = float(stats0[lid0, cv2.CC_STAT_AREA]) * PX2M2
    sub = lab[lab0 == lid0]
    vals, cnts = np.unique(sub[sub > 0], return_counts=True)
    parts = {int(v): round(float(c) * PX2M2, 2) for v, c in zip(vals, cnts)}
    parts = {k: v for k, v in sorted(parts.items(), key=lambda kv: -kv[1]) if v >= 0.02}
    return parts, round(raw - sum(parts.values()), 2), round(raw, 2)


def split_119(parts, lab, stats, cut_ink, niche_raw, raw):
    """按裁定归并子块：龛内=两子块之和、右带=⊕26 所在块、左窄带=龛左无符号条。

    输出与封账工件 split_119 字段同名可直比；面积均系机器算，只有“哪块归哪级”是人的裁定。
    """
    m14 = nearest_lid(lab, stats, *niche_raw[0])          # ⊕14 所在子块
    m26 = nearest_lid(lab, stats, *niche_raw[1])          # ⊕26 所在子块
    niche = [l for l in parts if _lid_xspan(l, stats)[0] / SC <= CUT_119_PT[0] + 2.0
             and _lid_xspan(l, stats)[1] / SC >= CUT_119_PT[1] - 2.0
             and l != m26]
    band = [l for l in parts if l not in niche and l != m26]
    out = {"龛内(+2650,⊕14)": round(sum(parts[l] for l in niche), 2),
           "右带(+2600,⊕26)": parts[m26],
           "左窄带(无符号)": round(sum(parts[l] for l in band), 2),
           "切割线像素": cut_ink}
    assert round(sum(out.values()), 2) == raw, "切分不闭合: %s vs %s" % (out, raw)
    return out, {"niche_lids": niche, "right_lid": m26, "band_lids": band}


def _lid_xspan(lid, stats):
    x0 = stats[lid, cv2.CC_STAT_LEFT]
    return x0, x0 + stats[lid, cv2.CC_STAT_WIDTH]


# ── VLM_READ：标高数字（几何读不出，人工放大读图转录；None=未能读取）──────
# 逐格判读依据为 outputs/preview/vlm/elev/ 下的编号裁图；改读必须同时改此表并重跑。
ELEV_VLM_READ = {
    0: 2950, 1: 2550, 2: 2600, 3: 2600, 4: 2950, 5: 2550, 6: 2550, 7: 2620,
    8: 2750, 9: 2650, 10: 2950, 11: 2600, 12: 2600, 13: 2600, 14: 2650,
    15: 2750, 16: 2600, 17: 2950, 18: 2600, 19: 2600, 20: 2750, 21: 2600,
    22: "SLAN-100",   # 非标高：灯槽物料代号（读图确认）
    23: 2350,         # 阳台梁带；USER_RULING 见下
    24: 2450, 25: 2650, 26: 2600, 27: 2650, 28: 2600, 29: 2600,
    30: None,         # 未能读取（三态：不猜）
}

# ── USER_RULING：用户裁定（不得由几何“推断”出来，也不得静默改动）───────────
ADJUDICATIONS = {
    "split_119": {
        "date": "2026-10-04",
        "ruling": "域119 沿床头龛边界 x=564.0/637.9 通高切割重连通",
        "machine_raw_m2": 6.28,          # 本模块重跑得此值（2026-10-12 已复现）
        "parts_m2": {"龛内(+2650,⊕14)": 4.37, "右带(+2600,⊕26)": 1.52,
                     "左窄带(无符号)": 0.34, "切割线像素": 0.05},
        "machine_repro": "龛内=3.77+0.60 两子块（模型已能分别算出），0.34/0.05 与机器值 0.35/0.04 差 0.01（取整）",
        "closure": "4.37+1.52+0.34+0.05=6.28 与机器原值闭合",
    },
    "narrow_band_121": {
        "date": "2026-10-12",
        "ruling": "龛左窄带 0.34㎡ 图上无符号 → 用户裁定“按附近吊顶的标高”归 +2650",
        "effect": "+2650 由 6.24 → 6.58㎡；挂账清零；总 Σ 仍 97.78（裁定只改归属不改面积）",
    },
    "marker_23": {
        "date": "2026-10-04",
        "ruling": "⊕23 泄漏带经矢量去遮挡复核确认为阳台梁带，用户裁定=暗藏灯带（保留 +2350）",
    },
}

TODO_STAGES = (
    "zone_bands / bands_bridge（侧带 2.25㎡ 下界）与 dashes / coves / coves_accepted"
    "（灯槽采信 16.89m）的生成器尚未重建；这 5 个工件目前是**无源数据**，"
    "在其生成器补齐并逐一复现前，引用其数值须标注“不可复现，仅存档值”。"
)


def build_flood_rows(lab, stats, cands, vlm=ELEV_VLM_READ):
    """MACHINE 逐标记取 lid/面积/state + VLM_READ 标高 + USER_RULING 注记。"""
    rows = []
    for m, (cx, cy) in enumerate(cands):
        lid = nearest_lid(lab, stats, cx, cy)
        a = round(float(stats[lid, cv2.CC_STAT_AREA]) * PX2M2, 2) if lid is not None else None
        rows.append({
            "m": m, "xy": [cx, cy],
            "v": vlm.get(m), "provenance_v": "VLM_READ" if m in vlm else "UNREAD",
            "zone_m2": a, "lid": lid,
            "state": ("leak_bg" if (a or 0) > LEAK_M2 else "closed"),
            "provenance_geom": "MACHINE",
        })
    return rows


def regenerate():
    verify_source()
    doc = fitz.open(PDF)
    page = doc[PAGE_IDX]
    cands = marker_positions(page)
    lab, stats, blk = flood_mask(page)
    rows = build_flood_rows(lab, stats, cands)
    # 域119 切分：线为裁定、面积为机器。与封账工件 split_119 逐字段可直比。
    n2, lab2, stats2, _ = cut_mask(blk, CUT_119_PT)
    parts, cut_ink, raw = decompose_domain(SPLIT_119_LID, lab, stats, lab2, stats2)
    split, split_lids = split_119(parts, lab2, stats2, cut_ink,
                                  (cands[14], cands[26]), raw)
    return {
        "meta": {"source": os.path.basename(PDF), "source_sha256": SOURCE_SHA256,
                 "page_idx": PAGE_IDX, "dpi": DPI, "dilate": 2,
                 "provenance_legend": {"MACHINE": "几何确定性算出",
                                        "VLM_READ": "人工放大读图转录标高数字",
                                        "USER_RULING": "用户裁定的归属/口径"}},
        "cands": cands, "rows": rows, "adjudications": ADJUDICATIONS,
        "split_119": {"machine_parts_by_lid": parts, "machine_grouped": split,
                      "grouping_lids": split_lids, "cut_lines_pt": CUT_119_PT,
                      "sealed_reference": ADJUDICATIONS["split_119"]["parts_m2"],
                      "provenance": "面积=MACHINE、切线与归级=USER_RULING"},
        "pending_stages": TODO_STAGES,
    }


def check_sealed(art, sealed_total=97.78):
    """恒等式校验：只校验本模块能确定的一部分，不假装覆盖未重建的链。"""
    closed = [r for r in art["rows"] if r["state"] == "closed"]
    per_lid = {}
    for r in closed:
        per_lid.setdefault(r["lid"], r["zone_m2"])
    s = sum(per_lid.values())
    leak = [r["m"] for r in art["rows"] if r["state"] == "leak_bg"]
    print("  机器闭合域 %d 个 Σ=%.2f㎡（封账 %.2f）；泄漏标记 %s" % (
        len(per_lid), s, sealed_total, leak))
    assert abs(s - sealed_total) < 0.02, "97.78 未复现：得 %.2f" % s
    sp = art["split_119"]["machine_grouped"]
    ref = ADJUDICATIONS["split_119"]["parts_m2"]
    print("  域119=%.2f㎡ 切分（机器）: %s" % (per_lid.get(119, float("nan")), sp))
    print("                封账存档值: %s" % ref)
    for k in sp:
        # 0.011 而非 0.01：abs(0.04-0.05) 在浮点下为 0.010000000000000009，会误炸。
        assert abs(sp[k] - ref[k]) <= 0.011, \
            "切分 %s 与封账不符: %.2f vs %.2f" % (k, sp[k], ref[k])
    # 窄带/切线两个“剩余”桶可互相吐 0.01（切线像素算给谁），但两者之和必须相同；
    # 龛内与右带属硬值，必须逐字相等。
    assert abs(sp["左窄带(无符号)"] + sp["切割线像素"]
               - ref["左窄带(无符号)"] - ref["切割线像素"]) < 0.005, "窄带+切线不闭合"
    assert sp["龛内(+2650,⊕14)"] == ref["龛内(+2650,⊕14)"] \
        and sp["右带(+2600,⊕26)"] == ref["右带(+2600,⊕26)"], "龛内/右带未逐字复现"
    return s


def main():
    check = "--check" in sys.argv
    art = regenerate()
    print("=" * 62)
    print("P3b 复算链（源图纸哈希锁 %s…）" % art["meta"]["source_sha256"][:12])
    print("=" * 62)
    s = check_sealed(art)
    if not check:
        os.makedirs(OUTDIR, exist_ok=True)
        p = os.path.join(OUTDIR, "p02_zones_flood.json")
        with open(p, "w") as f:
            json.dump({"dpi": DPI, "dilate": 2, "rows": art["rows"],
                       "split_119": ADJUDICATIONS["split_119"], "meta": art["meta"]},
                      f, ensure_ascii=False, indent=1)
        print("  工件已写: %s" % p)
    print("  ⚠ %s" % TODO_STAGES)
    return s


if __name__ == "__main__":
    main()
