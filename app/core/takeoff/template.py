"""行项模板层：词表归一化、模板加载、三档漏项判定、合理性区间。

纯包：只 import 标准库与 .types；不得 import flask / app.models / modeling。
"""
from __future__ import annotations

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

# 长词优先：避免「灯」先命中「灯带」
_MATERIALS_LONGEST_FIRST = tuple(sorted(MATERIAL_TOKENS, key=len, reverse=True))


def normalize_unit(raw):
    text = (raw or "").strip()
    if not text:
        return "项"
    return UNIT_ALIASES.get(text, text)


def material_of(name):
    text = name or ""
    for token in _MATERIALS_LONGEST_FIRST:
        if token in text:
            return token
    return None
