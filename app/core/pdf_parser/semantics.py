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


ROOM_WORD_RE = re.compile(
    r'(卧室|浴室|卫生间|盥洗室|客厅|餐厅|厨房|中厨|西厨|衣帽间|储物间|收纳间'
    r'|电梯厅|过厅|玄关|阳台|书房|走廊|过道|茶室)'
)
# 说明性文字里也会出现房间词（「电梯厅天花与平面仅为示意」），必须排除
ROOM_LABEL_NOISE = ('示意', '仅为', '天花', '吊顶', '平面', '铺装', '说明',
                    '标注', '见图', '详见', '做法', '与')
ROOM_PATTERNS = [ROOM_WORD_RE, re.compile(r"^(P\d+[AB]?)[- ]")]

MATERIAL_CODE_RE = re.compile(r"^[A-Z]{1,3}[-/]?\d{1,3}[A-Za-z]?$")
DIM_RE = re.compile(r"^(\d+)$")
DIM_WITH_UNIT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(mm|m|㎡|m²)$", re.I)


def extract_room_labels(page, x_min=0, y_max=None, max_len=8):
    """平面布置图房间名标签：[(name, x, y)]。

    判定：含房间词、长度受限、且不含说明性词。
    「^锚定」会漏掉带定语的命名（主人卧室/生活阳台/中厨），故改为词命中；
    代价是说明句也会命中，用 ROOM_LABEL_NOISE 与 max_len 排除。
    """
    if y_max is None:
        y_max = page.rect.height
    out = []
    for t, cx, cy, fs, fn in extract_text_spans(page):
        if cx < x_min or cy > y_max:
            continue
        t = t.strip()
        if len(t) > max_len or any(w in t for w in ROOM_LABEL_NOISE):
            continue
        for pat in ROOM_PATTERNS:
            if pat.search(t):
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


ROOM_TOKENS = [
    '客厅', '餐厅', '厨房', '中厨', '西厨', '书房', '主卧', '次卧', '玄关',
    '走廊', '过道', '储物间', '收纳间', '衣帽间', '卫生间', '盥洗室', '电梯厅',
    '过厅', '阳台', '茶室',
]
ROOM_TOKENS += ['卧室' + w for w in '一二三四五六七八']
ROOM_TOKENS += ['浴室' + w for w in '一二三四五六七八']
AREA_RE = re.compile(r"^(\d+(?:\.\d+)?)(s\.m\.|sq\.?m\.?|㎡|m2|m²)$", re.I)


def room_from_text(text):
    """从任意文本（图名/注记）解析房间名：取最长命中词。

    立面页的「最近房间标签」易串味（如标题「浴室一立面图」旁标着「浴室三」），
    页面标题是更可靠的房间归属源，故这里支持对标题做子串匹配而非行首匹配。
    """
    hits = [tok for tok in ROOM_TOKENS if tok in text]
    if not hits:
        return ''
    return max(hits, key=len)


DRAWING_TYPE_WORDS = [
    '立面图', '平面图', '天花布置图', '吊顶布置图', '地面铺装图', '插座布置图',
    '开关布置图', '连线图', '开线图', '大样图', '节点图', '索引图', '布置图',
    '材料表', '做法表', '说明',
]
PAREN_NUM_RE = re.compile(r"[（(][一二三四五六七八九十\d]*[)）]")
# 剥完图型词后剩下的这些是「非房间」限定词，不能当房间名用
NON_ROOM_RESIDUALS = {
    '平面', '通用', '天花', '地面', '吊顶', '索引', '立面', '户型', '整体',
    '标准', '节点', '大样', '布置', '装修', '材料', '做法', '给水', '排水',
    '电气', '智能', '空调', '消防', '修订',
}
# 兜底残余必须像房间名，否则「修修修」这类图名噪声会被当成归属
ROOM_HINTS = ('室', '厅', '房', '间', '廊', '厨', '卫', '阳台', '玄关', '衣帽', '储物')


def room_from_title(title):
    """从图名解析房间归属：先查词表，未命中则剥掉图型词与括号编号取残余。

    词表无法穷举各项目命名（如「主人浴室」=主卫），故提供通用兜底：
    「主人浴室立面图（一）」→ 去 立面图/（一） → 「主人浴室」。
    """
    tok = room_from_text(title)
    if tok:
        return tok
    residual = title
    for w in DRAWING_TYPE_WORDS:
        residual = residual.replace(w, '')
    residual = PAREN_NUM_RE.sub('', residual)
    residual = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", '', residual)
    if residual in NON_ROOM_RESIDUALS or not residual.endswith(ROOM_HINTS):
        return ''
    return residual if 2 <= len(residual) <= 8 else ''


def extract_area_annotations(page, x_min=0, y_max=None):
    """面积注记：[(面积 m², x, y)]。

    认 `12.4 s.m.` 这类 WPS/CAD 导出写法（s.m.=平方米），以及 ㎡/m2/m²。
    每一次出现都是独立量（立面图上一个注记=一面墙的饰面面积），不做去重。
    """
    if y_max is None:
        y_max = page.rect.height
    out = []
    for t, cx, cy, fs, fn in extract_text_spans(page):
        if cx < x_min or cy > y_max:
            continue
        m = AREA_RE.match(re.sub(r"\s+", "", t))
        if m:
            out.append((float(m.group(1)), cx, cy))
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
