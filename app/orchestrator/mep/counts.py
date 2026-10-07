"""MEP 图纸文字锚计数 / 回路注记解析（M2/M3 封账逻辑沉淀）。

纪律：文字锚是矢量 PDF 内机读文本，属几何层证据；
      计数一律带页码与屏蔽区参数（图例列 x<=262、图签 y>=815 不入账）。
"""
import math
import re

MAIN_XMIN = 262            # 主图区左界（屏蔽左侧图例列）
MAIN_YMAX = 815            # 主图区下界（屏蔽图签）
LD_RE = re.compile(r"^(2w)?LD-\d{1,2}$", re.I)
VENT_MAP = {"S": "出风口", "R": "回风口", "E": "抽气扇"}


def spans(page):
    """页内全部文字 span：[(去空白文本, 中心x, 中心y)]。"""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                t = re.sub(r"\s+", "", s["text"])
                if t:
                    out.append((t, (s["bbox"][0] + s["bbox"][2]) / 2,
                                (s["bbox"][1] + s["bbox"][3]) / 2))
    return out


def _in_main(x, y):
    return x > MAIN_XMIN and y < MAIN_YMAX


def ld_labels(page):
    """P05 灯具回路标签（去 2W 前缀去重）：{LD-xx: (x, y)}。"""
    out = {}
    for t, x, y in spans(page):
        if LD_RE.match(t) and _in_main(x, y):
            out[re.sub(r"(?i)^2w", "", t.upper())] = (x, y)
    return out


def vent_counts(page):
    """空调风口文字锚计数：{"S": n, "R": n, "E": n}（仅主图区）。"""
    cnt = {"S": 0, "R": 0, "E": 0}
    for t, x, y in spans(page):
        if t in cnt and _in_main(x, y):
            cnt[t] += 1
    return cnt


def mep_text_anchors(page):
    """P14 智能化文字锚：[(代码, x, y)]，代码=KY/Mc/Q/B/WiFi。"""
    out = []
    for t, x, y in spans(page):
        if not _in_main(x, y):
            continue
        if t in ("KY", "Mc", "Q", "B") or t.startswith("WiFi"):
            out.append((t, x, y))
    return out


def expand_token(tok):
    """回路编号展开：LD-01 / LD-26~LD27 / LD-14~LD16 / ALD-01~ALD-06 → 代码列表。

    范围写法后半段可省略前缀与横线（图纸实测变体），如 LD-14~LD16、LD-12~13。
    """
    tok = tok.strip()
    if not tok:
        return []
    m = re.match(r"^([A-Z]+)-?(\d+)(?:~(?:[A-Z]+-?)?(\d+))?$", tok)
    if not m:
        return []
    prefix, a = m.group(1), int(m.group(2))
    b = int(m.group(3)) if m.group(3) else a
    return [f"{prefix}-{i:02d}" for i in range(a, b + 1)]


def circuit_items(expr):
    """一条"控制回路"注记原文 → 受控代码列表（、分隔）。"""
    return [c for part in re.split("、", expr) for c in expand_token(part)]


def dedup(points, tol=15.0):
    """锚点去重（同一构件双重注记）：贪心，保留先到点。tol 单位 pt。"""
    kept = []
    for x, y in points:
        if not any(math.hypot(x - kx, y - ky) < tol for kx, ky in kept):
            kept.append((x, y))
    return kept
