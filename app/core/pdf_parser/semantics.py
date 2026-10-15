"""PDF 文字语义提取。

从 fitz page 的矢量文本提取：
- 全部文字 span（坐标 + 文本）
- 房间名标签（平面布置图）
- 材料代号（立面图/物料表）
- 尺寸标注（DIM 数值）
"""
import re


def extract_text_spans(page):
    """页内全部文字 span：[(text, cx, cy, fontsize, fontname)]。

    text 已去除首尾空白；坐标为 span bbox 中心。
    """
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b.get("lines", []):
            for s in l["spans"]:
                t = s["text"].strip()
                if not t:
                    continue
                cx = (s["bbox"][0] + s["bbox"][2]) / 2
                cy = (s["bbox"][1] + s["bbox"][3]) / 2
                out.append((t, cx, cy, s.get("size", 0), s.get("font", "")))
    return out


def extract_text_anchors(page):
    """去重文字锚：{text: (cx, cy)}（首次出现优先）。"""
    out = {}
    for t, cx, cy, fs, fn in extract_text_spans(page):
        t_clean = re.sub(r"\s+", "", t)
        if t_clean and t_clean not in out:
            out[t_clean] = (cx, cy)
    return out


ROOM_PATTERNS = [
    re.compile(r"^(客厅|餐厅|厨房|卧室[一二三四五六七八]?|主卧|次卧|书房|浴室[一二三四五六七八]?|卫生间|过厅|电梯厅|阳台|储物间|衣帽间|玄关|走廊|过道)"),
    re.compile(r"^(P\d+[AB]?)[- ]"),
]

MATERIAL_CODE_RE = re.compile(r"^[A-Z]{1,3}[-/]?\d{1,3}[A-Za-z]?$")
DIM_RE = re.compile(r"^(\d+)$")
DIM_WITH_UNIT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(mm|m|㎡|m²)$", re.I)


def extract_room_labels(page, x_min=0, y_max=None):
    """平面布置图房间名标签：[(name, x, y)]。

    过滤条件：
    - 匹配房间名模式
    - x > x_min（屏蔽左侧图例列）
    - y < y_max（屏蔽底部图签）
    """
    if y_max is None:
        y_max = page.rect.height
    out = []
    for t, cx, cy, fs, fn in extract_text_spans(page):
        if cx < x_min or cy > y_max:
            continue
        for pat in ROOM_PATTERNS:
            if pat.match(t):
                out.append((t, cx, cy))
                break
    return out


def extract_material_codes(page, x_min=0, y_max=None):
    """材料代号提取：[(code, x, y)]。

    匹配模式：如 A-01、BC12、D/03 等。
    """
    if y_max is None:
        y_max = page.rect.height
    out = []
    for t, cx, cy, fs, fn in extract_text_spans(page):
        t_clean = re.sub(r"\s+", "", t)
        if cx < x_min or cy > y_max:
            continue
        if MATERIAL_CODE_RE.match(t_clean):
            out.append((t_clean, cx, cy))
    return out


def extract_dimensions(page, x_min=0, y_max=None):
    """尺寸标注提取：[(value_mm, x, y)]。

    匹配纯数字（默认 mm）或带单位（mm/m/㎡）。
    """
    if y_max is None:
        y_max = page.rect.height
    out = []
    for t, cx, cy, fs, fn in extract_text_spans(page):
        t_clean = re.sub(r"\s+", "", t)
        if cx < x_min or cy > y_max:
            continue
        m = DIM_WITH_UNIT_RE.match(t_clean)
        if m:
            val = float(m.group(1))
            unit = m.group(2).lower() if m.group(2) else "mm"
            if unit in ("m", "㎡", "m²"):
                out.append((val, cx, cy, unit))
            else:
                out.append((val, cx, cy, "mm"))
            continue
        m = DIM_RE.match(t_clean)
        if m and fs < 14:
            out.append((int(m.group(1)), cx, cy, "mm"))
    return out


def extract_page_title(page, title_label='图纸名称', max_dy=30.0):
    """图签标题栏中的图名。

    定位「图纸名称」标签，取其正下方最近的一条文字作为图名。
    找不到标签时退回字号最大的靠右文字。

    返回 str，无图名返回 ''。
    """
    spans = extract_text_spans(page)
    label = next(((cx, cy) for t, cx, cy, fs, fn in spans
                  if title_label in t), None)

    if label is not None:
        lx, ly = label
        cands = [(t, cx, cy) for t, cx, cy, fs, fn in spans
                 if 2.0 < cy - ly <= max_dy and abs(cx - lx) < 60
                 and len(t) >= 2 and title_label not in t]
        if cands:
            cands.sort(key=lambda c: c[2] - ly)
            return cands[0][0]

    right = [(t, fs) for t, cx, cy, fs, fn in spans
             if cx > page.rect.width * 0.8 and len(t) >= 2]
    if right:
        return max(right, key=lambda r: r[1])[0]
    return ''


def extract_leader_text(page, leader_segments, text_radius=8.0):
    """引线旁文字：引线尖端附近 text → [(leader_seg_idx, text, x, y)]。

    用于提取标高数值、材料注记等引线指向的文本。
    """
    import math
    anchors = extract_text_anchors(page)
    out = []
    for idx, (x0, y0, x1, y1, L) in enumerate(leader_segments):
        tip_x, tip_y = x1, y1
        for text, (tx, ty) in anchors.items():
            if math.dist((tx, ty), (tip_x, tip_y)) <= text_radius:
                out.append((idx, text, tx, ty))
    return out
