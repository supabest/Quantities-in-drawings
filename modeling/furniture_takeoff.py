"""modeling.furniture_takeoff — 固定家具/柜台算量(按"个", 大样驱动 + 平立面关联)。

现场定制固定柜/台: 数量属设计排程(平面柜为无名块、大样逐柜标注稀疏→几何不可靠计数),
本模块按 BOQ"个"制列项(定制件数), 关联所属房间的大样图号作追溯, 饰面材料=同该房间墙面(P2)。
柜体展开/台面 m² 因大样 DIMENSION 被图框/标题栏污染, 单列 P4b 待逐图去噪精算。

用法: ./venv/bin/python -m modeling.furniture_takeoff
产出: outputs/model/furniture_takeoff.md
"""
import os
import openpyxl
from . import quant_table as Q
from . import wall_boq

OUTDIR = "outputs/model"
QTY_COL = 13          # "工程数量" 列(整户总量)
# 柜/台/家具类关键词(排除装饰线等 m 制项)
FURN_KW = ("柜", "橱", "屏风", "层架", "中岛", "台", "榻", "梳妆", "圆柱")
NON_FURN = ("装饰线", "金属线", "木线", "线条")
# 房间 → 固装大样图号(无独立大样者标"无大样")
ROOM_GD = {
    "开放区": "GD01~05(客厅固装)",
    "主卧": "GD06(主卧固装)",
    "衣帽间": "GD06(主卧固装)",
    "卧室一": "GD07(卧室一固装)",
    "卧室二": "GD08(卧室二固装)",
    "收纳间": "GD09~10(储物阳台柜)",
    "生活阳台": "GD09~10(储物阳台柜)",
    "主卫": "无大样",
    "浴室一": "无大样",
    "浴室二": "无大样",
    "浴室三": "无大样",
    "厨房": "无大样",
}
# 名称 → 房间(按关键词就近归属)
NAME_ROOM = [
    ("玄关", "开放区"), ("餐厅", "开放区"), ("茶室", "开放区"), ("电视", "开放区"),
    ("安全间", "开放区"), ("橱柜", "厨房"), ("中岛", "厨房"), ("岛台", "厨房"),
    ("主卧", "主卧"), ("玻璃屏风", "主卧"), ("衣帽", "衣帽间"),
    ("卧室一", "卧室一"), ("卧室二", "卧室二"), ("收纳", "收纳间"),
    ("主卫", "主卫"), ("浴室一", "浴室一"), ("浴室二", "浴室二"), ("浴室三", "浴室三"),
    ("阳台", "生活阳台"),
]


def infer_room(name):
    for kw, room in NAME_ROOM:
        if kw in name:
            return room
    return "其他"


def load_furniture():
    wb = openpyxl.load_workbook(wall_boq.BOQ_PATH, data_only=True)
    ws = wb[wall_boq.SHEET]
    rows = list(ws.iter_rows(values_only=True))
    out = []
    for r in rows:
        name = str(r[1] or "").replace("\n", "").strip()
        unit = str(r[3] or "").replace("\n", "").strip()
        if unit != "个" or not name:
            continue
        if not any(k in name for k in FURN_KW) or any(k in name for k in NON_FURN):
            continue
        qty = r[QTY_COL] if QTY_COL < len(r) else None
        qty = float(qty) if isinstance(qty, (int, float)) else 1.0
        out.append((name, int(round(qty))))
    wb.close()
    return out


def rows():
    """P5 汇总入口: 固定家具(个) Q.row 列表。"""
    items = load_furniture()
    out = []
    for name, qty in items:
        room = infer_room(name)
        gd = ROOM_GD.get(room, "无大样")
        has_gd = gd != "无大样"
        formula = "定制件 ×%d（数量=设计排程; 柜为无名块/大样逐柜标注稀疏, 不可几何计数）" % qty
        trace = "大样:%s; 饰面:同%s墙面(见wall_geometry); 柜体/台面m²:%s" % (
            gd, room, "待P4b去噪精算" if has_gd else "无大样→按现场定制")
        out.append(Q.row(room, name, "个", formula, qty, trace))
    return out


def main():
    rows_ = rows()
    agg = {}
    for r in rows_:
        room = r["part"]
        gd = ROOM_GD.get(room, "无大样")
        a = agg.setdefault(room, [0, 0, gd != "无大样"])
        a[0] += r["value"]
        a[1] += 1
    items = [(r["item"], r["value"]) for r in rows_]

    path = Q.write_md(os.path.join(OUTDIR, "furniture_takeoff.md"),
                      "227户型 固定家具/柜台算量(按个, 大样驱动)",
                      "**列项/单位**=BOQ个制(定制柜件数, 作列项模板)  **数量**=设计排程值"
                      "(几何不可靠计数, 已如实标注)  **追溯**=关联各房间固装大样图号  "
                      "**饰面材料**=同该房间墙面(用户指示)。\n\n"
                      "> 柜体展开/台面 m² 需大样尺寸, 但当前 DIMENSION 被图框污染→单列 P4b 待逐图去噪精算; "
                      "浴室柜/橱柜无独立大样→按现场定制。\n",
                      rows_)

    print("=" * 62)
    print("固定家具/柜台 — 件数合计 %d, 覆盖房间 %d" %
          (sum(q for _, q in items), len(agg)))
    print("=" * 62)
    for room, (cnt, kind, has_gd) in agg.items():
        print("  %-8s 件数=%2d  类项=%2d  大样=%s" % (room, cnt, kind, "有" if has_gd else "无(现场定制)"))
    print("\n产出 -> %s" % path)


if __name__ == "__main__":
    main()
