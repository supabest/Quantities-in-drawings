"""modeling.wall_boq — 从工程量清单提取墙面(各材料×各房间)面积, 作为算量权威基准。

与 boq_ref.py(楼面面积) 同构: 数据取自 金融城227户型样板房工程量清单.xlsx
sheet "ZS02-A-精装修工程-227户型", 列=房间分组, 行=清单项。
筛选 m2 单位的墙面类行(硬包/木饰面/瓷砖/石材/乳胶漆/玻璃/金属饰面),
组织为 {room_col: [{item, material_code, area_m2}]}。
"""
import openpyxl
import os

BOQ_PATH = os.path.join(
    os.path.dirname(__file__), "..",
    "装修图纸", "227户型dxf", "金融城227户型样板房工程量清单.xlsx")
SHEET = "ZS02-A-精装修工程-227户型"
# 列索引(0-based) → 房间分组名
COL_ROOMS = {
    4: "开放区(玄关/餐厅/厨房/客厅/安全间)",
    5: "主卧+衣帽间",
    6: "卧室一",
    7: "卧室二",
    8: "收纳间",
    9: "主卫",
    10: "浴室一",
    11: "浴室二",
    12: "浴室三",
}
# 墙面关键字(行名含以下关键词之一视为墙面项)
WALL_KW = ("墙柱", "墙面", "踢脚线", "墙身")
# 天花/地面排除词(避免混入非墙面行)
EXCLUDE_KW = ("地面", "天棚", "吊顶", "找平", "门槛", "背胶")
# 项目特征列(col2)提取材料代号的正则
import re
MAT_CODE = re.compile(r"((?:UP|WD|ST|TL|PT|WC|MT|GL|MR|GR|FL|LT|CD|GD|SD)-\d{2})")


def load_wall_boq():
    """返回 {room_name: [{item, spec, material, unit, area_m2}]} 墙面项清单。"""
    wb = openpyxl.load_workbook(BOQ_PATH, data_only=True)
    ws = wb[SHEET]
    rows = list(ws.iter_rows(values_only=True))
    # 找表头(含房间分组名的行)
    hdr_idx = None
    for i, r in enumerate(rows[:10]):
        if any("衣帽" in str(c or "") for c in r):
            hdr_idx = i
            break
    if hdr_idx is None:
        raise ValueError("找不到表头行")
    result = {v: [] for v in COL_ROOMS.values()}
    for r in rows[hdr_idx + 1:]:
        name = str(r[1] or "").replace("\n", "").strip()
        spec = str(r[2] or "").replace("\n", " ").strip()
        unit = str(r[3] or "").replace("\n", "").strip()
        if not name or unit not in ("m2", "m"):
            continue
        if not any(k in name for k in WALL_KW):
            continue
        if any(k in name for k in EXCLUDE_KW):
            continue
        # 提材料代号
        mat = MAT_CODE.search(name + " " + spec)
        material = mat.group(1) if mat else ""
        for ci, room_name in COL_ROOMS.items():
            v = r[ci]
            if isinstance(v, (int, float)) and v > 0:
                result[room_name].append({
                    "item": name, "spec": spec[:80],
                    "material": material, "unit": unit,
                    "area": round(float(v), 2),
                })
    wb.close()
    return result


def wall_total_per_room(boq=None):
    """汇总每间墙面面积(m2项, 不含m单位的线条)。返回 {room: m2_total}"""
    if boq is None:
        boq = load_wall_boq()
    out = {}
    for room, items in boq.items():
        out[room] = round(sum(it["area"] for it in items if it["unit"] == "m2"), 2)
    return out


if __name__ == "__main__":
    boq = load_wall_boq()
    print("=== 各房间墙面清单项 ===")
    for room, items in sorted(boq.items()):
        if not items:
            continue
        total = sum(it["area"] for it in items if it["unit"] == "m2")
        print("\n【%s】 共 %d 项, m2合计 %.2f" % (room, len(items), total))
        for it in items:
            print("  [%s] %-30s %s %.2f %s" % (
                it["material"] or "--", it["item"][:30],
                it["unit"], it["area"], ""))
