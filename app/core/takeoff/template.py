"""行项模板层：词表归一化、模板加载、三档漏项判定、合理性区间。

纯包：只 import 标准库与 .types；不得 import flask / app.models / modeling。
"""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# 单位量纲：m²/㎡ 等别名统一到 m2；空单位按「项」处理
UNIT_ALIASES = {
    "m²": "m2", "㎡": "m2", "M2": "m2", "m2": "m2",
    "m³": "m3", "M3": "m3", "m3": "m3",
    "m": "m", "M": "m", "km": "km",
    "个": "个", "套": "套", "台": "台", "樘": "樘", "项": "项",
    "kg": "kg", "t": "t", "组": "组", "根": "根",
}

# 受控材料词表（spec §5.5：DIVISIONS 材料清单 + BASE_LAYER_RULES 键 + 图纸代号）
MATERIAL_TOKENS = (
    "岩板", "瓷砖", "石材", "木地板", "木饰面", "乳胶漆", "艺术涂料", "不锈钢",
    "金属饰面", "硬包", "玻璃", "石膏板", "铝扣板", "GRG", "墙纸", "软包", "铝板",
    "自流平", "地毯", "防水",
    "马桶", "面盆", "龙头", "花洒", "浴缸", "地漏", "角阀", "毛巾架", "纸巾架",
    "喷枪", "洗衣机",
    "配电箱", "插座", "开关", "射灯", "灯带", "灯槽", "窗帘盒", "浴霸",
    "抽气扇", "风口", "风机盘管", "空调",
    "衣柜", "橱柜", "浴室柜", "玄关柜", "餐厅柜", "电视柜", "书柜", "屏风",
    "洗手台", "门套",
)

# 「型」紧跟词后是修饰用法（如「开关型」），不算材料名
_MODIFIER_SUFFIX = "型"


def normalize_unit(raw):
    text = (raw or "").strip()
    if not text:
        return "项"
    return UNIT_ALIASES.get(text, text)


def material_of(name):
    """取名称中最靠右的受控词作为材料（中文中心语在后）。

    「洗衣机地漏」→「地漏」、「洗衣机龙头」→「龙头」、「马桶喷枪」→「喷枪」；
    同一位置有长短词重叠时取长词。括号（全角「（）」与半角「()」）内是定位语/
    修饰语而非材料，先整体剥掉再匹配——「石膏板吊顶 立面（灯槽位置）」→「石膏板」、
    「单相二、三极防溅插座(带开关)」→「插座」。紧跟「型」出现的是修饰用法
    （如「开关型」），跳过该次命中；若再无有效命中则返回 None，交由人工复核。
    """
    text = re.sub(r"[（(][^）)]*[）)]", "", name or "")
    best = None  # (起点, 词长, 词)
    for token in MATERIAL_TOKENS:
        start = text.find(token)
        while start != -1:
            end = start + len(token)
            if text[end:end + 1] != _MODIFIER_SUFFIX:
                if best is None or start > best[0] or (start == best[0] and len(token) > best[1]):
                    best = (start, len(token), token)
            start = text.find(token, start + 1)
    return best[2] if best else None
