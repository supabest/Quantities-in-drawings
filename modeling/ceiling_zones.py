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

尚未固化的阶段（诚实标注，勿当作已复现）：dashes / coves / coves_accepted
（灯槽采信 16.89m）的**机制**已于 2026-10-13 重建（dash_pool / dash_chains / coves_accepted），
但只逐字复现 12 条采信链中的 9 条（Σ=11.77m）——存档链的**截断位置**用六类可检验判据
都复现不出来，机器 Σ 恒偏大（46.57~64.15m，随池上限漂移），故 16.89m **仍是存档值、
不得当机器封账**，见 TODO_STAGES 与 dash_chains 工件的 verdict。侧带链（zone_bands /
 bands_bridge，2.25㎡）已于 2026-10-13 重建，19 行周长与 6 条带逐字复现存档值。

用法: ./venv/bin/python -m modeling.ceiling_zones          # 重跑、校验、写侧带工件
      ./venv/bin/python -m modeling.ceiling_zones --check   # 只校验不写盘
      ./venv/bin/python -m modeling.ceiling_zones --write-flood   # 额外覆盖洪水工件（谨慎，见 main 注）
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
    "cove_dash_10": {
        "date": "2026-10-12",
        "ruling": "虚线链 #10（ang=45°、span 0.84m、p0[541.6,255.5]→p1[558.6,272.4]）"
                  "为入户门开启弧的弦，不计入灯槽 → 灯槽采信 Σ 由 17.73m 降为 16.89m",
        "note": "该链本身可机器逐字复现（n=4 med=3.39 span=0.84 全等）；被剔出是语义裁定，"
                "不是几何判据，不得由“角度最异类”之类的推断自动生成。",
    },
}

TODO_STAGES = (
    "dashes / coves / coves_accepted（灯槽采信 16.89m）：机制已于 2026-10-13 重建"
    "（dash_pool / dash_chains / coves_accepted），12 条采信链逐字复现 **9** 条"
    "（Σ=11.77m，含被剔出的 #10）；未复现的是 #0/#1/#11——存档链比机制链短 1~3 段，"
    "其**截断位置**经宽度带/长度窗/图层/gap 阈值/实线穿越/path-index 六类判据逐一"
    "检验均不能成立，故 16.89m 只能作**存档值**引用，不是机器值，也不得被机器 Σ"
    "（46.57~64.15m，方向恒偏大；工件 machine_sum_sensitivity 每次重跑重新量）。"
    "（侧带链 zone_bands / bands_bridge 已逐字复现。）"
)

# ── MACHINE：侧带（跌级竖向带）口径 ────────────────────────────────────
# 界面长 = 两域各膨胀 BRIDGE_ITERS 后的接触像素 / (2×膨胀半径)；侧带面积 = L×Δh。
# 2026-10-13 反推确认：6 条带的 len_m/dh_m/area_m2 与存档 p02_bands_bridge.json 逐字相同；
# 周长口径为“4 邻域跳出域的转移次数×PX2M”，19/19 与存档 perim_m 逐字相同。
BRIDGE_ITERS = 12          # ≈4.3pt@1:100；膨胀后仍不接触的界面一律不计（不猜）

# 标高未经互证的域 → 不参与侧带（报告 §1 已披露“平面05 无 2450 锚 → 冲突披露”）。
# 这是数据质量裁定，不是几何判据，必须显式列定而非从图中“推”出。
LEVEL_UNCONFIRMED = {
    840: "+2450 无平面05 互证锚（报告 §1 冲突披露）→ 与 791(+2650) 的接触带不计",
}

# 存档参照（2026-10-04 产物；elev/ 不入库，故关键值钉在此处供逐字比对）
ARCHIVE_PERIM_M2 = {
    361: 92.53, 155: 91.36, 672: 34.49, 119: 50.77, 895: 28.32, 707: 31.24,
    30: 17.22, 345: 26.82, 555: 41.40, 465: 46.36, 83: 17.04, 335: 21.31,
    641: 20.55, 513: 18.36, 309: 9.17, 611: 10.82, 467: 7.87, 840: 9.60, 791: 7.24,
}
ARCHIVE_BANDS = [      # (z1, z2, v1, v2, len_m, dh_m, area_m2)
    (361, 465, 2950, 2600, 0.94, 0.35, 0.328),
    (555, 672, 2600, 2950, 3.96, 0.35, 1.387),
    (309, 335, 2550, 2620, 0.16, 0.07, 0.012),
    (611, 155, 2650, 2600, 3.51, 0.05, 0.175),
    (707, 155, 2950, 2600, 0.95, 0.35, 0.332),
    (641, 791, 2600, 2650, 0.31, 0.05, 0.016),
]
ARCHIVE_BAND_SUM_M2 = 2.25

# ── 灯槽（藏灯虚线链）口径 ────────────────────────────────────────
# 2026-10-12~13 用 13 个一次性探针反推（原生成器已被删，git 无引用）。
# **已逐字确证**：span_m = |p1−p0| × 0.0352778（31 行中 28 行成立；行 19/20/30 为
#   原实现端点顺序/符号不一致，均在被拒尾部，不影响封账）；ang = 成员自身角 mod 180
#   的均值 round 0.1；med_len = 成员笔长中位数（偶数取中间两项均值），且 **'c' 曲线取
#   贝塞尔弧长而非端点弦**（实测弧长 2.649 ≈ 存档 2.64，弦长 2.456 明显不符）。
# **未确证**：存档链的**截断位置**——机器链系统性比存档长 1~3 段（row0 8 段 vs 4、
# row1 19 vs 11、row11 6 vs 8），已逐一排除六类判据（REJECTED_CAUSES）。
# ⇒ 16.89m 不能升格为机器值：12 条采信链仅 9 条逐字复现（Σ=11.77m），机器 Σ 随
# 池上限在 46.57~64.15m 漂移且方向恒偏大（lmin=2.0 时 45.82~63.40，同结论）。
DASH_LMIN = 1.5          # 段池：成员笔长下限（pt）
DASH_LMAX = 30.0         # 段池：上限（pt）——敏感项，见上
DASH_GAP_PT = 8.0        # 沿轴相邻成员间隙阈值（pt）
DASH_BUCKET_DEG = 2.0    # 方向桶宽（°），链内用桶心 ka×2+1° 为名义轴
DASH_BUCKET_OFF = 0.6    # 法向偏移桶宽（pt）；floor 分桶天然去掉重复视图的同长段
DASH_MIN_N = 4           # 成员数门槛（存档 31 行最小 n=4，无 n<4）
COVE_MED_MIN = 2.5       # 采信门槛：med_len ≥ 2.5pt（本页真正起作用的门槛）
COVE_SPAN_MIN = 0.5      # 采信门槛：span ≥ 0.5m（实测平台区：降到 0.15 结果不变）
COVE_ARCHIVE_TOP_N = 12  # 存档 12 条 = dashes 按 span 降序取前 12（截断约定，非门槛结果）
PT2M_SPAN = 0.0352778    # span_m = 端点距(pt) × 此值

REJECTED_CAUSES = (
    "宽度带（w∈[0.18,0.30] 细线）：row0 成员含 w=0.48，证伪",
    "长度窗：须容 25.08 又须拒 17.04，单调不可能",
    "图层 layer：同链混 FIN/CEILING，被排除段与成员同为 CEILING w=0.24",
    "gap 阈值：截断处间隙仅 0.96~1.44pt（≪8）",
    "实线穿越/被共线实线全覆盖丢弃：706.88 处 1.08pt 竖穿未截断；"
    "#4672/#4673 被 #15750 全覆盖仍在存档链内",
    "按 path-index 连续串链：会得 14 段（存档 11）",
)

# 存档参照（2026-10-04 产物；elev/ 不入库，12 行完整钉在此处供逐字比对）
# (ang, n, span_m, p0, p1, med_len) ← p02_coves_accepted.json（= p02_dashes[:12]，span 降序）
ARCHIVE_DASHES_ACCEPTED = [
    (0.0, 4, 3.55, (628.9, 463.8), (729.4, 463.9), 24.48),
    (90.0, 11, 1.87, (558.6, 290.8), (558.6, 343.7), 3.48),
    (90.0, 11, 1.85, (666.3, 297.1), (666.3, 349.6), 3.48),
    (90.0, 10, 1.74, (523.5, 307.3), (523.5, 356.6), 3.48),
    (90.0, 10, 1.65, (643.2, 387.7), (643.2, 434.5), 3.48),
    (90.0, 8, 1.35, (533.5, 308.8), (533.5, 347.0), 3.54),
    (90.0, 8, 1.34, (478.5, 413.6), (478.5, 451.7), 3.48),
    (90.0, 6, 1.02, (558.6, 238.0), (558.6, 266.8), 3.48),
    (90.0, 6, 0.99, (740.5, 505.9), (740.5, 534.1), 3.48),
    (0.0, 6, 0.99, (840.8, 498.2), (869.0, 498.2), 3.48),
    (45.0, 4, 0.84, (541.6, 255.5), (558.6, 272.4), 3.39),   # ← USER_RULING 剔出
    (90.6, 8, 0.54, (732.4, 553.4), (732.4, 568.7), 2.64),   # ← 'c' 弧链
]
ARCHIVE_COVE_EXCLUDED_IDX = 10     # 入户门弧弦（见 ADJUDICATIONS["cove_dash_10"]）
ARCHIVE_COVE_SUM_M = 16.89         # 封账引用值 = Σ(12) − #10；仅存档值，非机器值
# 逐字复现结果（2026-10-13，lmin=1.5/lmax=30；其余池上限配置命中集相同）
COVE_REPRO_IDX = (2, 3, 4, 5, 6, 7, 8, 9, 10)
COVE_REPRO_SPAN_M = 11.77          # 含 #10 的 0.84（即逐字复现部分 12 条中 9 条）
# 机器口径回归锚（**不是封账**，是“这套参数现在量出来就是这个数”）：
# 只断言 16.89 与命中名单、不钉机器条数/Σ，则改桶宽或改采信门槛这类
# 口径漂移会静默通过（2026-10-13 反向验证实测：DASH_BUCKET_OFF 0.6→0.7、
# COVE_SPAN_MIN 0.5→0.3 两项均未炸，故补上本锁）。
COVE_MACHINE_ANCHOR = {"n_chains": 58, "n_accepted": 31, "sum_m": 46.57}
COVE_STYLE_NOTE = (
    "存档 style 字段（29 dashed / 2 dash-dot）为人工判读：本页 get_drawings 的 dashes "
    "元数据实测全为 '[] 0'(25802 条) 或 None(75 条)，无虚线样式可取 → 机器无从判定样式，"
    "本模块输出的链不带 style。"
)


def _numeric(v):
    return isinstance(v, str) and v.isdigit()


def domain_level(vals):
    """域标高：标记转录值全同 → '2600'；数值不一致 → 'CONFLICT:2600/2650'；代号原样保留。"""
    uniq = list(dict.fromkeys(vals))
    if len(uniq) == 1:
        return str(uniq[0]) if uniq[0] is not None else "UNREAD"
    nums = sorted({v for v in uniq if isinstance(v, int)})
    if len(nums) == len(uniq):
        return "CONFLICT:" + "/".join(str(n) for n in nums)
    return "CONFLICT:" + "/".join(sorted(str(v) for v in uniq))


def perimeter_px(mask):
    """域周长(px) = 4 邻域跳出 mask 的转移次数（存档 19/19 逐字相同的口径）。"""
    m = mask.astype(np.uint8)
    p = np.pad(m, 1)
    t = 0
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        sh = p[1 + dy:m.shape[0] + 1 + dy, 1 + dx:m.shape[1] + 1 + dx]
        t += int(((m > 0) & (sh == 0)).sum())
    return t


def build_domains(rows):
    """洪水行 → 域表（面积/标记/标高），按面积降序（与存档 rows 同序）。"""
    per = {}
    for r in rows:
        if r["state"] != "closed":
            continue
        d = per.setdefault(r["lid"], {"lid": r["lid"], "area_m2": r["zone_m2"],
                                      "markers": [], "vals": []})
        d["markers"].append(r["m"])      # 同域各标记的 zone_m2 必然同值（同一 lid）
        d["vals"].append(r["v"])
    out = []
    for d in per.values():
        d["markers"].sort()
        d["lvl"] = domain_level(d["vals"])
        out.append(d)
    out.sort(key=lambda d: -d["area_m2"])
    return out


def zone_bands(lab, domains):
    """域周长 + 直接邻接侧带（未膨胀，下界）。实测 bands 全空：洪水域必被墨线隔开。"""
    k3 = np.ones((3, 3), np.uint8)
    masks = {d["lid"]: (lab == d["lid"]) for d in domains}
    rows_out = []
    for d in domains:
        edge = cv2.dilate(masks[d["lid"]].astype(np.uint8), k3, iterations=1) > 0
        bands = []
        for o in domains:
            if o["lid"] == d["lid"] or not _numeric(d["lvl"]) or not _numeric(o["lvl"]):
                continue
            n = int((edge & masks[o["lid"]]).sum())
            if n:
                dh = abs(int(d["lvl"]) - int(o["lvl"])) / 1000.0
                bands.append({"z2": o["lid"], "len_m": round(n * PX2M, 2),
                              "dh_m": round(dh, 3), "area_m2": round(n * PX2M * dh, 3)})
        rows_out.append({"lid": d["lid"], "lvl": d["lvl"], "area_m2": d["area_m2"],
                         "perim_m": round(perimeter_px(masks[d["lid"]]) * PX2M, 2),
                         "band_m2": round(sum(b["area_m2"] for b in bands), 3),
                         "bands": bands, "markers": d["markers"]})
    return rows_out


def bands_bridge(lab, domains, iters=BRIDGE_ITERS):
    """确证邻接域对 → 跌级侧带 A=L×Δh（与存档 p02_bands_bridge.json 同构）。

    z1/z2 出场序 = 域的首个标记序号（存档 6 带同序）；剔除项带原因写进 skipped，
    不静默丢弃。Δh=0（同标高）与非数值标高（CONFLICT/SLAN-100/UNREAD）本质不是跌级。
    """
    k3 = np.ones((3, 3), np.uint8)
    order = sorted(domains, key=lambda d: min(d["markers"]))
    dil = {d["lid"]: cv2.dilate((lab == d["lid"]).astype(np.uint8), k3, iterations=iters)
           for d in order}
    bands, skipped = [], []
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            n = int(((dil[a["lid"]] > 0) & (dil[b["lid"]] > 0)).sum())
            if not n:
                continue
            len_m = n * PX2M / (2 * iters)
            v1, v2 = a["lvl"], b["lvl"]
            reason = None
            if not _numeric(v1) or not _numeric(v2):
                reason = "标高非数值(%s/%s)：CONFLICT/代号/未读域不计" % (v1, v2)
            elif int(v1) == int(v2):
                reason = "同标高 %s：Δh=0" % v1
            elif a["lid"] in LEVEL_UNCONFIRMED:
                reason = LEVEL_UNCONFIRMED[a["lid"]]
            elif b["lid"] in LEVEL_UNCONFIRMED:
                reason = LEVEL_UNCONFIRMED[b["lid"]]
            if reason:
                skipped.append({"z1": a["lid"], "z2": b["lid"], "v1": v1, "v2": v2,
                                "len_m": round(len_m, 2), "why": reason})
                continue
            dh = abs(int(v1) - int(v2)) / 1000.0
            bands.append({"z1": a["lid"], "z2": b["lid"], "v1": int(v1), "v2": int(v2),
                          "len_m": round(len_m, 2), "dh_m": round(dh, 3),
                          "area_m2": round(len_m * dh, 3)})
    return bands, skipped


def bezier_len(z0, z1, z2, z3, n=200):
    """三次贝塞尔弧长（200 段折线离散，精度 ≪0.01pt）。存档对 'c' 成员算的是弧长。"""
    tot, px, py = 0.0, z0.x, z0.y
    for i in range(1, n + 1):
        t = i / n
        mt = 1.0 - t
        x = mt ** 3 * z0.x + 3 * mt * mt * t * z1.x + 3 * mt * t * t * z2.x + t ** 3 * z3.x
        y = mt ** 3 * z0.y + 3 * mt * mt * t * z1.y + 3 * mt * t * t * z2.y + t ** 3 * z3.y
        tot += math.hypot(x - px, y - py)
        px, py = x, y
    return tot


def dash_pool(page, lmin=None, lmax=None):
    """灯槽候选段池（MACHINE）：type=='s' 且无填充且**单 item** 的孤段 stroke 路径。

    “单 item”是存档成员的共同特征（row0/row1/row11 逐一点名均成立）：多段折线
    （墙线、家具轮廓、填充边界）一律不入池。'l' 取端点距，'c' 取弧长。
    注：本图无虚线元数据（见 COVE_STYLE_NOTE），灯槽只能靠“等间距孤段链”识别。
    参数均采 None→运行时取模块常量：写成默认常量会在定义时绑定，反向验证改不动它。
    """
    lmin = DASH_LMIN if lmin is None else lmin
    lmax = DASH_LMAX if lmax is None else lmax
    segs = []
    for dr in page.get_drawings():
        if dr.get("fill") is not None or dr["type"] != "s" or len(dr["items"]) != 1:
            continue
        it = dr["items"][0]
        if it[0] == "l":
            a, b = it[1], it[2]
            L = math.hypot(b.x - a.x, b.y - a.y)
        elif it[0] == "c":
            a, b = it[1], it[4]                 # 方向/端点用弦，长度用弧长
            L = bezier_len(it[1], it[2], it[3], it[4])
        else:
            continue                            # 're' 矩形/'qu' 四边形不是笔段
        if L < lmin or (lmax and L > lmax):
            continue
        ang = math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 180.0
        th = math.radians(ang)
        off = -(a.x + b.x) / 2 * math.sin(th) + (a.y + b.y) / 2 * math.cos(th)
        segs.append({"bk": (int(ang // DASH_BUCKET_DEG), int(math.floor(off / DASH_BUCKET_OFF))),
                     "a": (a.x, a.y), "b": (b.x, b.y), "L": L, "ang": ang})
    return segs


def _median(vals):
    v = sorted(vals)
    i = len(v) // 2
    return v[i] if len(v) % 2 else (v[i - 1] + v[i]) / 2.0


def _split_by_gap(its, gap):
    cur = []
    for it in its:
        if cur and it[0] - cur[-1][1] > gap:
            yield cur
            cur = []
        cur.append(it)
    if cur:
        yield cur


def _chain_row(bk, ch):
    """一串成员 → 与存档 p02_dashes.json 同构的行（ang/n/p0/p1/span_m/med_len）。"""
    return {"ang": round(sum(c[5] for c in ch) / len(ch) % 180.0, 1),
            "bucket": list(bk), "n": len(ch),
            "p0": [round(ch[0][2][0], 1), round(ch[0][2][1], 1)],
            "p1": [round(ch[-1][3][0], 1), round(ch[-1][3][1], 1)],
            "span_m": round(math.hypot(ch[-1][3][0] - ch[0][2][0],
                                       ch[-1][3][1] - ch[0][2][1]) * PT2M_SPAN, 2),
            "med_len": round(_median([c[4] for c in ch]), 2)}


def dash_chains(page, lmin=None, lmax=None, gap=None, min_n=None):
    """段池 → (方向 2° × 法向 0.6pt) floor 分桶 → 沿轴 gap≤8pt 串链 → n≥4 链行。

    floor 分桶自带去重（row0 两条 22.56 分别落在 y=463.71/463.83 → 桶 772/773），
    重复视图的同长段不会被归进同一链——这是当初最难想到的一步。
    """
    lmin = DASH_LMIN if lmin is None else lmin
    lmax = DASH_LMAX if lmax is None else lmax
    gap = DASH_GAP_PT if gap is None else gap
    min_n = DASH_MIN_N if min_n is None else min_n
    buckets = {}
    for s in dash_pool(page, lmin, lmax):
        buckets.setdefault(s["bk"], []).append(s)
    out = []
    for bk, lst in buckets.items():
        th = math.radians(bk[0] * DASH_BUCKET_DEG + DASH_BUCKET_DEG / 2)   # 名义轴 = 桶心
        ux, uy = math.cos(th), math.sin(th)
        its = []
        for s in lst:
            a1, a2 = s["a"][0] * ux + s["a"][1] * uy, s["b"][0] * ux + s["b"][1] * uy
            lo, hi = (s["a"], s["b"]) if a1 <= a2 else (s["b"], s["a"])
            its.append((min(a1, a2), max(a1, a2), lo, hi, s["L"], s["ang"]))
        its.sort()
        for ch in _split_by_gap(its, gap):
            if len(ch) >= min_n:
                out.append(_chain_row(bk, ch))
    return out


def coves_accepted(chains):
    """采信门槛（MACHINE）：med_len ≥ 2.5pt 且 span ≥ 0.5m；按 span 降序（与存档同序）。

    ⚠ 两条实测事实，引用时不得误读：
    ① 本门槛在机器链上给出 31 条，**不是“恰好筛出存档的 12 条”**：存档 12 条是
       `p02_dashes` 按 span 降序取前 12 的**截断约定**（dashes[:12]），不是门槛的结果。
    ② span≥0.5 在本页是**平台区**：降到 0.15 得数完全不变（0.5~0.15 间无候选），
       真正在筛的是 med≥2.5（放宽到 2.0 → n_accepted 31→57、Σ 46.57→60.11）。
       2026-10-13 反向验证实测，归入本注而不是隐掉。
    """
    acc = [c for c in chains if c["med_len"] >= COVE_MED_MIN and c["span_m"] >= COVE_SPAN_MIN]
    return sorted(acc, key=lambda c: -c["span_m"])


def cove_repro(chains, archive=None, tol_pt=0.16, tol_mid_pt=6.0):
    """逐字复现判定：链端点重合（<0.16pt）或中点邻近（<6pt），且 n/med_len/span_m 三项全等。

    返回（命中存档行号元组, 命中行 span 之和）。名单固定写进 COVE_REPRO_IDX ⇒
    口径一飘就炸（包括“不当心全命中了”这种伪升级）。
    archive 不能在签名里默认成常量：默认值在定义时绑定，运行时改 ARCHIVE_DASHES_ACCEPTED
    就不生效（反向验证会假绿），故改为 None 再取当前模块值。
    """
    archive = ARCHIVE_DASHES_ACCEPTED if archive is None else archive
    hit, tot = [], 0.0
    for i, (_ang, n, span, p0, p1, med) in enumerate(archive):
        for c in chains:
            ends = (abs(c["p0"][0] - p0[0]) < tol_pt and abs(c["p0"][1] - p0[1]) < tol_pt
                    and abs(c["p1"][0] - p1[0]) < tol_pt and abs(c["p1"][1] - p1[1]) < tol_pt)
            mids = (abs((c["p0"][0] + c["p1"][0]) / 2 - (p0[0] + p1[0]) / 2) < tol_mid_pt
                    and abs((c["p0"][1] + c["p1"][1]) / 2 - (p0[1] + p1[1]) / 2) < tol_mid_pt)
            if (ends or mids) and (c["n"], c["med_len"], c["span_m"]) == (n, med, span):
                hit.append(i)
                tot += span
                break
    return tuple(hit), round(tot, 2)


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
    # 侧带链：域表 → 周长/直接邻接下界 → 膨胀桥接（全部 MACHINE，标高只参与筛选不参与计算）
    domains = build_domains(rows)
    zb_rows = zone_bands(lab, domains)
    bands, skipped = bands_bridge(lab, domains)
    # 灯槽链：机制已固化，但存档截断不可复现 → 只量化“逐字复现了几条”，不拿机器 Σ 替封账
    chs = dash_chains(page)
    acc = coves_accepted(chs)
    hit_idx, hit_span = cove_repro(chs)
    archive = [{"ang": a, "n": n, "span_m": sp, "p0": list(p0), "p1": list(p1),
                "med_len": med, "excluded_by_ruling": i == ARCHIVE_COVE_EXCLUDED_IDX}
               for i, (a, n, sp, p0, p1, med) in enumerate(ARCHIVE_DASHES_ACCEPTED)]
    arch_raw = round(sum(r["span_m"] for r in archive), 2)
    arch_sealed = round(arch_raw - archive[ARCHIVE_COVE_EXCLUDED_IDX]["span_m"], 2)
    # 池上限敏感度：不靠文字回忆，直接重跑三个配置量出来
    # （inf = 不设上限；不能用 None，None 的语义已变成“取默认 DASH_LMAX”）
    sens = {("lmax=%s" % lm): round(sum(c["span_m"] for c in
                                        coves_accepted(dash_chains(page, lmax=lm))), 2)
            for lm in (30.0, 40.0, float("inf"))}
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
        "zone_bands": {"dpi": DPI, "dilate": 2, "px2m": PX2M,
                       "sum_area": sum(r["area_m2"] for r in zb_rows),
                       "sum_band_lower": round(sum(r["band_m2"] for r in zb_rows), 3),
                       "note": "侧带仅计标记域间直接邻接(下界); 靠墙/灯槽带/泄漏域未计",
                       "rows": zb_rows},
        "bands_bridge": {"iters": BRIDGE_ITERS, "bands": bands,
                         "sum_band_m2": round(sum(b["area_m2"] for b in bands), 2),
                         "skipped_pairs": skipped,
                         "level_unconfirmed": {str(k): v for k, v in LEVEL_UNCONFIRMED.items()},
                         "provenance": "接触像素/长度/面积=MACHINE、标高=VLM_READ、"
                                       "未经互证域剔除=报告 §1 披露"},
        "dash_chains": {
            "params": {"lmin_pt": DASH_LMIN, "lmax_pt": DASH_LMAX, "gap_pt": DASH_GAP_PT,
                       "bucket_deg": DASH_BUCKET_DEG, "bucket_off_pt": DASH_BUCKET_OFF,
                       "min_n": DASH_MIN_N, "accept_med_pt": COVE_MED_MIN,
                       "accept_span_m": COVE_SPAN_MIN, "pt2m_span": PT2M_SPAN},
            "machine_rows": sorted(chs, key=lambda c: -c["span_m"]),
            "n_chains": len(chs), "n_accepted": len(acc),
            "machine_accepted": acc,
            "machine_sum_m": round(sum(c["span_m"] for c in acc), 2),
            "machine_sum_sensitivity": sens,
            "archive_accepted": archive,
            "archive_raw_sum_m": arch_raw, "archive_sealed_m": arch_sealed,
            "excluded_idx": ARCHIVE_COVE_EXCLUDED_IDX,
            "reproduced_idx": list(hit_idx), "reproduced_span_m": hit_span,
            "style_note": COVE_STYLE_NOTE,
            "gate_note": "门槛不是‘恰 12 条’的依据：本门槛在机器链上得 %d 条；存档 12 条是 "
                         "dashes 按 span 降序取前 %d 的截断约定。span≥0.5 在本页为平台区"
                         "（降至 0.15 结果不变），起作用的是 med≥2.5。"
                         % (len(acc), COVE_ARCHIVE_TOP_N),
            "rejected_causes": list(REJECTED_CAUSES),
            "verdict": "16.89m 为存档值（ARCHIVE），不是机器值：机制已复现但 12 条采信链"
                       "只逐字命中 9 条；机器 Σ 恒偏大且随池上限漂移，不得用来替换封账。",
            "provenance": "链几何=MACHINE、16.89m=存档值（不可复现部分已披露）、"
                          "#10 剔出=USER_RULING",
        },
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

    # 侧带链：周长 19 行逐字 + 6 条带逐字 + 总量 2.25
    zr = art["zone_bands"]["rows"]
    assert {r["lid"] for r in zr} == set(ARCHIVE_PERIM_M2), \
        "域集与存档不一致: %s" % sorted(r["lid"] for r in zr)
    bad = [r["lid"] for r in zr if abs(r["perim_m"] - ARCHIVE_PERIM_M2[r["lid"]]) > 0.005]
    print("  侧带下界（直接邻接）%.3f㎡；周长 %d/%d 行与存档逐字一致" % (
        art["zone_bands"]["sum_band_lower"], len(zr) - len(bad), len(zr)))
    assert not bad, "周长未复现（口径错）: %s" % bad
    got = [(b["z1"], b["z2"], b["v1"], b["v2"], b["len_m"], b["dh_m"], b["area_m2"])
           for b in art["bands_bridge"]["bands"]]
    print("  确证邻接侧带 %d 条 Σ=%.2f㎡（存档 %.2f）；剔除 %d 对（原因写于 skipped_pairs）" % (
        len(got), art["bands_bridge"]["sum_band_m2"], ARCHIVE_BAND_SUM_M2,
        len(art["bands_bridge"]["skipped_pairs"])))
    assert got == list(ARCHIVE_BANDS), \
        "侧带与存档不逐字相同:\n    得 %s\n    存 %s" % (got, ARCHIVE_BANDS)
    assert abs(art["bands_bridge"]["sum_band_m2"] - ARCHIVE_BAND_SUM_M2) < 0.005

    # 灯槽链：存档内部恒等式 + 固定复现名单（口径一飘就炸，包括“全命中”这种伪升级）
    dc = art["dash_chains"]
    assert abs(dc["archive_raw_sum_m"] - 17.73) < 0.005, dc["archive_raw_sum_m"]
    assert abs(dc["archive_sealed_m"] - ARCHIVE_COVE_SUM_M) < 0.005, \
        "16.89 ≡ 存档 Σ(12) − #10 这一恒等式不成立: %.2f" % dc["archive_sealed_m"]
    assert tuple(dc["reproduced_idx"]) == COVE_REPRO_IDX, \
        "逐字复现名单漂移: 得 %s 预期 %s（截断口径已变，需重新反推）" % (
            dc["reproduced_idx"], list(COVE_REPRO_IDX))
    assert abs(dc["reproduced_span_m"] - COVE_REPRO_SPAN_M) < 0.005, dc["reproduced_span_m"]
    got_anchor = {"n_chains": dc["n_chains"], "n_accepted": dc["n_accepted"],
                  "sum_m": dc["machine_sum_m"]}
    assert got_anchor == COVE_MACHINE_ANCHOR, \
        "灯槽机器口径漂移（桶宽/间隙/池窗/门槛任一被动过）: 得 %s 锚 %s" % (
            got_anchor, COVE_MACHINE_ANCHOR)
    assert dc["machine_sum_m"] > ARCHIVE_COVE_SUM_M, \
        "机器 Σ(%.2f) 竟≤封账 16.89：链变短了，截断口径可能被凑出来了，须人工重估" % dc["machine_sum_m"]
    print("  灯槽链：机器 n≥4 %d 条、过门槛 %d 条 Σ=%.2fm；存档 12 条逐字复现 %d/%d（Σ=%.2fm）"
          "→ 16.89m 仍为存档值" % (
              dc["n_chains"], dc["n_accepted"], dc["machine_sum_m"],
              len(dc["reproduced_idx"]), len(dc["archive_accepted"]), dc["reproduced_span_m"]))
    print("        池上限敏感度 %s（方向恒偏大）" % dc["machine_sum_sensitivity"])
    return s


def main():
    check = "--check" in sys.argv
    write_flood = "--write-flood" in sys.argv
    art = regenerate()
    print("=" * 62)
    print("P3b 复算链（源图纸哈希锁 %s…）" % art["meta"]["source_sha256"][:12])
    print("=" * 62)
    s = check_sealed(art)
    if not check:
        os.makedirs(OUTDIR, exist_ok=True)
        # 默认写三个工件：侧带两件与存档逐字段全等（已验）；灯槽一件是“机器链 ↔ 存档对照”。
        # p02_dash_chains.json 是本模块新写的灯槽工件：机行与存档对照同处一份。
        # **不覆盖** p02_dashes.json / p02_coves_accepted.json——它们是无源存档件，
        # 机器链与之不符（只 9/12 命中），覆盖了就丢掉封账参照。
        # p02_zones_flood.json 不默认覆盖：存档件里 ⊕14/⊕26 的 zone_m2 是**切分后**值
        # （4.37/1.52），_p3b_leader.py / _p3b_split.py 读的就是这一列；本模块 rows 是
        # 切分前原值 6.28（切分结果在 split_119 块）。要覆盖需显式 --write-flood。
        for name, key in (("p02_zone_bands.json", "zone_bands"),
                          ("p02_bands_bridge.json", "bands_bridge"),
                          ("p02_dash_chains.json", "dash_chains")):
            p = os.path.join(OUTDIR, name)
            with open(p, "w") as f:
                payload = dict(art[key])
                payload["meta"] = art["meta"]
                json.dump(payload, f, ensure_ascii=False, indent=1)
            print("  工件已写: %s" % p)
        if write_flood:
            p = os.path.join(OUTDIR, "p02_zones_flood.json")
            with open(p, "w") as f:
                json.dump({"dpi": DPI, "dilate": 2, "rows": art["rows"],
                           "split_119": ADJUDICATIONS["split_119"], "meta": art["meta"]},
                          f, ensure_ascii=False, indent=1)
            print("  工件已写（⚠ 切分前 rows，旧 _p3b_* 脚本依赖的 4.37/1.52 会变 6.28）: %s" % p)
    print("  ⚠ %s" % TODO_STAGES)
    return s


if __name__ == "__main__":
    main()
