"""modeling.ceiling_levels — 逐间吊顶标高提取(几何权威 + 计算式)。

背景: P3b 造型吊顶"展开面积"缺闭合分区不可自动(见手册); 但平面 05 的 CEILING 系图层
带**每间吊顶完成面标高(+2xxx mm)**, 其符号落在所属房间内 → 可按几何(点在多边形内)
可靠映射到房间。本模块提取吊顶标高, 为墙面净高/天花/IFC 体块提供**真实吊顶底标高**,
替换此前"一刀切结构净高 3020、ceiling_drop=0"的假设。

语义: +2xxx = 吊顶完成面相对本层 ±0.000(=地面完成面)的标高(mm) → 即该间净高(到吊顶底)。
一间可有多个标高(主顶 + 跌级/灯槽): max=主吊顶(覆盖多数墙面, 用于墙净高),
min=最低跌级(局部下沉), 二者之差=最大跌级下沉量(诚实披露, 不并入主顶面积——分区边界缺)。
无标高命中的房间兜底为结构净高 V["clear_to_soffit"]。

用法: ./venv/bin/python -m modeling.ceiling_levels
产出: outputs/model/ceiling_levels.md
"""
import os
import re
import math
import ezdxf
from shapely.geometry import Point, Polygon
from . import base_extract as B
from . import quant_table as Q

DXF = B.DXF
X0, Y0, X1, Y1 = B.X0, B.Y0, B.X1, B.Y1
LEVEL_RE = re.compile(r"^\+\s?(2\d{3})$")        # +2xxx(mm) 吊顶完成面标高
MAP_TOL = 3000.0                                  # mm 标高落点→房间质心兜底容差


def _dec(s):
    try:
        return B._decode(s or "")
    except Exception:
        return re.sub(r"\\M\+5[0-9A-Fa-f]{4}", "?", s or "")


def harvest_levels():
    """从平面 05 收集所有 +2xxx 标高(含 INSERT 块属性/TEXT/MTEXT)及其坐标(mm)。"""
    doc = ezdxf.readfile(DXF)
    msp = doc.modelspace()
    out = []

    def add(text, x, y, layer):
        t = _dec((text or "").strip())
        mm = LEVEL_RE.match(t)
        if not mm:
            return
        e = float(mm.group(1))
        if not (X0 - 200 <= x <= X1 + 200 and Y0 - 200 <= y <= Y1 + 200):
            return
        out.append({"elev_mm": e, "x": x, "y": y, "layer": layer})

    def walk(ents, depth):
        for e in ents:
            t = e.dxftype()
            if t == "INSERT" and depth < 6:
                ins = e.dxf.insert
                for a in e.attribs:
                    add(a.dxf.text, ins.x, ins.y, e.dxf.layer)
                try:
                    walk(list(e.virtual_entities()), depth + 1)
                except Exception:
                    pass
            elif t == "TEXT":
                p = e.dxf.insert
                add(e.dxf.text, p.x, p.y, e.dxf.layer)
            elif t == "MTEXT":
                p = e.dxf.insert
                try:
                    add(e.plain_text(), p.x, p.y, e.dxf.layer)
                except Exception:
                    add(e.text, p.x, p.y, e.dxf.layer)
    walk(msp, 0)
    return out


def _in_poly(x, y, poly):
    try:
        return Polygon(poly).contains(Point(x, y))
    except Exception:
        return False


def map_to_rooms(rooms, levels):
    """把每个标高分配到包含它的房间(点在多边形内); 无 poly 时兜底最近质心(≤MAP_TOL)。"""
    for r in rooms:
        r["_lv"] = []
    for lv in levels:
        hit = None
        for r in rooms:
            if r.get("poly") and _in_poly(lv["x"], lv["y"], r["poly"]):
                hit = r
                break
        if hit is None:
            bd, cand = 1e18, None
            for r in rooms:
                d = math.hypot(lv["x"] - r["cx"], lv["y"] - r["cy"])
                if d < bd:
                    bd, cand = d, r
            if cand is not None and bd <= MAP_TOL:
                hit = cand
        if hit is not None:
            hit["_lv"].append(lv["elev_mm"])
    return rooms


def annotate(data):
    """提取标高→映射到每间, 挂 r['soffit_mm'](主顶=最高, 用于墙净高)、
    r['ceiling_drop_mm']、r['ceiling_levels']。返回汇总统计。"""
    rooms = data["rooms"]
    structural = data["V"]["clear_to_soffit"]
    levels = harvest_levels()
    map_to_rooms(rooms, levels)
    n_mapped = 0
    for r in rooms:
        lvs = sorted(set(r.pop("_lv", [])))
        r["ceiling_levels"] = lvs
        if lvs:
            r["soffit_mm"] = max(lvs)          # 主吊顶完成面标高 = 到吊顶底净高
            r["ceiling_drop_mm"] = round(structural - max(lvs), 1)  # 主顶相对结构板底下沉
            r["ceiling_min_mm"] = min(lvs)      # 最低跌级
            n_mapped += 1
        else:
            r["soffit_mm"] = structural          # 兜底: 无吊顶信息→结构净高
            r["ceiling_drop_mm"] = 0.0
            r["ceiling_min_mm"] = structural
    return {"n_level": len(levels), "n_mapped": n_mapped,
            "n_room": len([r for r in rooms if not r["is_out"]]), "structural": structural}


def main():
    data = B.build()
    st = annotate(data)
    rooms = [r for r in data["rooms"] if not r["is_out"] and r.get("poly")]
    structural = st["structural"]
    rows = []
    for r in sorted(rooms, key=lambda x: -(x["area"] or 0)):
        nm = "+".join(r["names"]) or "(无标注)"
        lvs = r["ceiling_levels"]
        if lvs:
            formula = "主顶 max(+%d) 共%d级%s; 下沉=结构%d−%d=%dmm" % (
                int(max(lvs)), len(lvs), ["+%d" % int(v) for v in lvs][:6],
                int(structural), int(max(lvs)), int(structural - max(lvs)))
            rows.append(Q.row(nm, "吊顶完成面净高(主顶)", "mm", formula, r["soffit_mm"],
                              "面积%.2fm2; 最低跌级+%d(局部下沉%dm未并入主顶)" % (
                                  r["area"], int(r["ceiling_min_mm"]),
                                  int(max(lvs) - r["ceiling_min_mm"])), check=None))
        else:
            rows.append(Q.row(nm, "吊顶完成面净高(主顶)", "mm",
                              "无吊顶标高命中→结构净高兜底", r["soffit_mm"],
                              "面积%.2fm2; 待补天花布置图" % r["area"], check=None))
    n_has = sum(1 for r in rooms if r["ceiling_levels"])
    n_drop = sum(1 for r in rooms if r["ceiling_levels"] and r["ceiling_drop_mm"] > 0)
    rows.append(Q.row("覆盖", "实测到吊顶标高的房间", "个",
                      "命中%d / 户内%d" % (n_has, len(rooms)), n_has,
                      "标高实体%d个; 有下沉(主顶<结构)房间%d间" % (st["n_level"], n_drop),
                      check=len(rooms)))
    note = ("> 语义: +2xxx = 吊顶完成面标高(mm, 相对本层±0.000=地面完成面) = 该间净高(到吊顶底)。"
            " 一间多级=主顶(max)+跌级/灯槽(min); **主顶用于墙面净高**, 跌级局部下沉单列披露(无分区边界→不并入)。"
            " 无命中房间兜底结构净高 %dmm。\n" % int(structural))
    out = Q.write_md(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "outputs", "model", "ceiling_levels.md"),
                     "227户型 · 逐间吊顶标高(P3b 可机读部分)", note, rows)
    print("标高实体 %d 个; 命中房间 %d/%d (有下沉 %d)" % (st["n_level"], n_has, len(rooms), n_drop))
    for r in sorted(rooms, key=lambda x: -(x["area"] or 0)):
        print("  %-18s 主顶+%d 结构%d 下沉%dm 跌级%s" % (
            ("+".join(r["names"]))[:18], int(r["soffit_mm"]), int(structural),
            int(r["ceiling_drop_mm"]), r["ceiling_levels"]))
    print("→", out)


if __name__ == "__main__":
    main()
