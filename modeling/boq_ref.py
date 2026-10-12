"""modeling.boq_ref — 工程量清单(BOQ)楼面面积参照, 作为面积对账的权威口径。

数据取自 金融城227户型样板房工程量清单.xlsx / sheet "ZS02-A-精装修工程-227户型",
按清单自身的房间分组(与 p11 不同: 主卧+衣帽间合一、开放区含客/餐/厨/玄关/安全间)。
每间楼面完成面积 = 干区"地面水泥砂浆找平层" / 湿区"石材地面铺贴(+淋浴间地面)" / 开放区"客厅石材+玄关拼花"。
"""

# 组名 -> {boq: 清单楼面面积(m2), parts: 该组含的 P-TEXT 房间名, src: 取数说明}
BOQ_GROUPS = {
    "开放区(客/餐/厨/玄关/安全间)": {"boq": 106.61, "src": "客厅石材102.16+玄关拼花4.45",
        "rooms": ["客厅/茶室", "餐厅", "玄关", "中厨", "储物间"]},
    "主卧+衣帽间": {"boq": 22.01, "src": "地面找平=吊顶平面22.01", "rooms": ["主人卧室", "主人衣帽间"]},
    "卧室一": {"boq": 10.86, "src": "地面找平10.86", "rooms": ["卧室一"]},
    "卧室二": {"boq": 13.87, "src": "地面找平13.87", "rooms": ["卧室二"]},
    "收纳间": {"boq": 2.77, "src": "石材地面2.77", "rooms": ["收纳间"]},
    "主卫": {"boq": 9.27, "src": "主卫地面7.45+淋浴间1.82", "rooms": ["主人浴室"]},
    "浴室一": {"boq": 3.93, "src": "卫生间地面2.69+淋浴间1.24", "rooms": ["浴室一"]},
    "浴室二": {"boq": 4.27, "src": "卫生间地面2.95+淋浴间1.32", "rooms": ["浴室二"]},
    "浴室三": {"boq": 4.99, "src": "卫生间地面3.67+淋浴间1.32", "rooms": ["浴室三"]},
    "生活阳台": {"boq": 5.62, "src": "瓷砖地面铺贴阳台5.62", "rooms": ["生活阳台"]},
}


def reconcile(rooms):
    """按 BOQ 分组, 汇总模型各成员的净面积/含墙面积, 与清单楼面面积对账。
    返回 [{group, rooms, boq, clear, gross, d_clear, d_gross, note}]。"""
    by_name = {}
    for r in rooms:
        for nm in r["names"]:
            by_name[nm] = r
    out = []
    for g, spec in BOQ_GROUPS.items():
        seen = set(); mem = []
        for n in spec["rooms"]:
            r = by_name.get(n)
            if r is not None and r["comp_id"] not in seen:   # 合并房间多锚点只计一次
                seen.add(r["comp_id"]); mem.append(r)
        missing = [n for n in spec["rooms"] if n not in by_name]
        clear = round(sum(m["area"] for m in mem), 2)
        gs = [m.get("gross_area") for m in mem]
        gross = round(sum(gs), 2) if all(g is not None for g in gs) and gs else None  # 成员无未定界才计含墙
        boq = spec["boq"]
        # 判定: BOQ 落在[净,含墙]带内=口径一致; 明显超含墙=模型偏小(需查); 低于净=BOQ偏小; 含墙不可定=单独标注
        if gross is None:
            note = "含墙未定界(开放区); BOQ较净 %+.1f%%" % ((boq - clear) / clear * 100)
        elif boq > gross * 1.05:
            note = "BOQ>含墙→模型偏小,查(飘窗/围合)"
        elif boq < clear * 0.95:
            note = "BOQ低于净→查"
        else:
            note = "净~含墙带内(口径一致)"
        out.append({"group": g, "rooms": spec["rooms"], "src": spec["src"], "boq": boq,
                    "clear": clear, "gross": gross, "missing": missing, "note": note,
                    "d_clear": round(clear - boq, 2),
                    "d_gross": round(gross - boq, 2) if gross else None})
    return out


if __name__ == "__main__":
    from . import base_extract as B
    d = B.build()
    print("%-26s %8s %8s %8s  %s" % ("BOQ分组", "清单", "模型净", "模型含墙", "判定"))
    for row in reconcile(d["rooms"]):
        print("  %-24s %8.2f %8.2f %8s  %s" % (
            row["group"], row["boq"], row["clear"],
            ("%.2f" % row["gross"]) if row["gross"] else "--", row["note"]))
