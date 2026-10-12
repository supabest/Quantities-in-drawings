"""modeling.ceiling_takeoff — 天花算量(几何权威 + 计算式)。

天棚(刮腻子/涂料/吊顶平面)投影面积 = 房间围合净面积(与楼面同一围合)。
数值由几何独立算出(复用 floor_takeoff 的栅格正交分解), BOQ 天棚项仅作开发期校验。
石膏板造型吊顶的"吊顶面积≠净面积"部分需天花布置图(P02~04/CD)精确, 本模块按净顶给基层量并标注待精确。

用法: ./venv/bin/python -m modeling.ceiling_takeoff
产出: outputs/model/ceiling_takeoff.md
"""
import os
import openpyxl
from . import base_extract as B
from . import quant_table as Q
from . import floor_takeoff as F
from . import wall_boq  # 复用 COL_ROOMS / BOQ_PATH / SHEET
from . import wall_geometry  # 复用 ROOM_MAP(与天花 BOQ 分组名同源)

OUTDIR = "outputs/model"
CEIL_KW = ("天棚", "吊顶", "顶棚")
CEIL_EXCLUDE = ("增减", "灯槽", "立面", "检修口")


def load_ceiling_boq():
    """返回 {room_col: 顶面投影面积}。同一顶面有多层(刮腻子/涂料/吊顶平面)属重复工序,
    不可加总; 取每间最大单层作为顶面投影面积代理。"""
    wb = openpyxl.load_workbook(wall_boq.BOQ_PATH, data_only=True)
    ws = wb[wall_boq.SHEET]
    rows = list(ws.iter_rows(values_only=True))
    hdr = None
    for i, r in enumerate(rows[:10]):
        if any("衣帽" in str(c or "") for c in r):
            hdr = i
            break
    mx = {v: 0.0 for v in wall_boq.COL_ROOMS.values()}
    for r in rows[hdr + 1:]:
        name = str(r[1] or "").replace("\n", "").strip()
        unit = str(r[3] or "").replace("\n", "").strip()
        if not name or unit != "m2":
            continue
        if not any(k in name for k in CEIL_KW):
            continue
        if any(k in name for k in CEIL_EXCLUDE):
            continue
        for ci, room in wall_boq.COL_ROOMS.items():
            v = r[ci]
            if isinstance(v, (int, float)) and v > mx[room]:
                mx[room] = float(v)
    wb.close()
    return {k: round(v, 2) for k, v in mx.items() if v}


def rows(data=None):
    """P5 汇总入口: 天棚基层(=房间净顶投影) Q.row 列表, 带BOQ天棚校验。"""
    if data is None:
        data = B.build()
    rooms = data["rooms"]
    frows = F.room_rows(data)
    by_part = {r["part"]: r for r in frows}
    by_name = {}
    for r in rooms:
        for nm in r["names"]:
            by_name[nm] = r
    cboq = load_ceiling_boq()
    out = []
    for boq_room, model_names in wall_geometry.ROOM_MAP.items():
        seen = set()
        geo = 0.0
        sub = []
        for n in model_names:
            r = by_name.get(n)
            if r is None or r["comp_id"] in seen:
                continue
            seen.add(r["comp_id"])
            part = "+".join(r["names"])
            fp = by_part.get(part)
            if fp:
                geo += fp["value"]
                sub.append("%s[%.2f]" % (part[:10], fp["value"]))
        if not geo:
            continue
        geo = round(geo, 2)
        formula = "Σ天棚(=房间净顶): " + " + ".join(sub[:3]) + (" +…" if len(sub) > 3 else "")
        out.append(Q.row(boq_room, "天棚刮腻子/涂料(基层)", "m2", formula,
                         geo, "05平面围合", check=cboq.get(boq_room)))
    return out


def main():
    rows_ = rows()
    path = Q.write_md(os.path.join(OUTDIR, "ceiling_takeoff.md"),
                      "227户型 天花算量(几何权威+计算式)",
                      "**数值来源**=房间围合净顶面积(与楼面同围合, 复用栅格正交分解)  **单位**=m²  "
                      "**BOQ校验**=天棚类m²项(刮腻子/涂料/吊顶平面), 非取数来源。\n\n"
                      "> 造型吊顶净投影若≠房间净顶, 需天花布置图(P02~04/CD)精确; 本表按净顶给基层量。\n",
                      rows_)

    print("=" * 62)
    print("天花算量(几何权威) — 分组数 %d" % len(rows_))
    print("=" * 62)
    for r in rows_:
        d = Q.diff_pct(r["value"], r["check"])
        print("  %-26s 几何=%6.2f  BOQ天棚=%6s  %s" % (
            r["part"][:26], r["value"],
            ("%.2f" % r["check"]) if r["check"] else "—",
            ("(%+.1f%%)" % d) if d is not None else "无天棚项"))
    print("\n产出 -> %s" % path)


if __name__ == "__main__":
    main()
