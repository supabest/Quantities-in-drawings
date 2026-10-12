"""modeling.floor_takeoff — 地面算量(几何权威 + 计算式)。

原则: 楼面完成面积一律由房间多边形几何独立算出, 每一项带 Σ(长×宽) 计算式与追溯。
BOQ 只作开发期校验(按清单分组比对), 不作数值来源。生产环境剔除校验列后即为成品算量。

用法: ./venv/bin/python -m modeling.floor_takeoff
产出: outputs/model/floor_takeoff.md
"""
import os
import numpy as np
from . import base_extract as B
from . import quant_table as Q
from . import boq_ref

OUTDIR = "outputs/model"
DXF_NO = "05平面图rev-33"


def decompose_mask(mask, X0, Y1, RES, quant=100.0, tol=100.0):
    """对轴对齐栅格 mask 做竖条分解, 容差模糊合并相邻相近列 → 矩形。
    斜向墙体在栅格上产生阶梯, 逐列签名会碎成多个10cm微矩形; 用 tol 内
    的模糊合并把同一面(近似)矩形归一, 公式可读且不丢面积。
    返回 (rects=[(w,h),...], pix_area_m2)。"""
    ys_idx, xs_idx = np.where(mask)
    if len(xs_idx) == 0:
        return [], 0.0
    pix_area = mask.sum() * RES * RES / 1e6
    gx0, gx1 = int(xs_idx.min()), int(xs_idx.max())

    def col_segs(gx):
        rows = np.where(mask[:, gx])[0]
        if len(rows) == 0:
            return ()
        segs = []
        s = p = rows[0]
        for r in rows[1:]:
            if r == p + 1:
                p = r
            else:
                segs.append((s, p)); s = p = r
        segs.append((s, p))
        out = []
        for a, b in segs:
            yhi = round((Y1 - a * RES) / quant) * quant
            ylo = round((Y1 - (b + 1) * RES) / quant) * quant
            if yhi - ylo > 1:
                out.append((ylo, yhi))
        return tuple(out)

    def match(sa, sb):
        if not sa or not sb or len(sa) != len(sb):
            return False
        return all(abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol for a, b in zip(sa, sb))

    rects = []
    gx = gx0
    while gx <= gx1:
        sig = col_segs(gx)
        ge = gx
        while ge + 1 <= gx1 and sig and match(col_segs(ge + 1), sig):
            ge += 1
        if sig:
            w = (ge - gx + 1) * RES
            for ylo, yhi in sig:
                rects.append((w, yhi - ylo))
        gx = ge + 1
    return rects, pix_area


def room_rows(data):
    """每户内房间 → 一行楼面几何算量(基于栅格 mask 分解)。"""
    lab = data["lab"]
    X0, Y0, X1, Y1 = data["bounds"]
    RES = data["res"]
    rows = []
    for r in data["rooms"]:
        if r["is_out"]:
            continue
        nm = "+".join(r["names"]) if r["names"] else "(无标注@%.0f,%.0f)" % (r["cx"] / 1e3, r["cy"] / 1e3)
        mask = (lab == r["comp_id"])
        rects, pix_area = decompose_mask(mask, X0, Y1, RES)
        if not rects:
            rows.append(Q.row(nm, "楼面完成面积", "m2", "栅格计数(%dpx)" % int(mask.sum()),
                              pix_area, "%s·comp%d" % (DXF_NO, r["comp_id"])))
            continue
        # 主矩形(两边≥100mm)入公式; 其余+取整差归"收边"。value 取精确栅格面积(不缩水)
        major = [(w, h) for w, h in rects if w >= 100 and h >= 100]
        agg = {}
        for w, h in major:
            key = (round(w / 10) * 10, round(h / 10) * 10)
            agg[key] = agg.get(key, 0) + 1
        terms = []
        disp_sum = 0.0
        for (w, h), n in sorted(agg.items(), key=lambda kv: -kv[0][0] * kv[0][1] * kv[1]):
            disp_sum += w * h * n / 1e6
            t = "%.2f×%.2f" % (w / 1e3, h / 1e3)
            terms.append(t if n == 1 else "%d×%s" % (n, t))
        formula = "Σ矩形: " + " + ".join(terms[:6])
        if len(terms) > 6:
            formula += " +…(%d类)" % len(terms)
        minor_area = pix_area - disp_sum
        if abs(minor_area) > 0.05:
            formula += (" + 收边%.2f" % minor_area) if minor_area >= 0 else (" − 去重%.2f" % -minor_area)
        value = pix_area
        rows.append(Q.row(nm, "楼面完成面积", "m2", formula, value,
                          "%s·围合%dpx" % (DXF_NO, int(mask.sum()))))
    return rows


def group_check(rows, rooms):
    """按 BOQ 分组把几何房间面积汇总 → 与清单值比对(校验, 非来源)。"""
    by_name = {}
    for r in rooms:
        for nm in r["names"]:
            by_name[nm] = r
    geo_by_key = {}
    for row in rows:
        geo_by_key[row["part"]] = row["value"]
    out = []
    for g, spec in boq_ref.BOQ_GROUPS.items():
        seen = set()
        geo = 0.0
        members = []
        for n in spec["rooms"]:
            r = by_name.get(n)
            if r is not None and r["comp_id"] not in seen:
                seen.add(r["comp_id"])
                nm = "+".join(r["names"])
                geo += geo_by_key.get(nm, 0.0)
                members.append(nm)
        out.append({"group": g, "geo": round(geo, 2), "boq": spec["boq"],
                    "src": spec["src"], "members": members})
    return out


def main():
    data = B.build()
    rooms = data["rooms"]
    rows = room_rows(data)
    note = ("**数值来源** = 房间围合栅格几何正交分解(Σ矩形, 可独立复算)  "
            "**单位** = m²(楼面完成面积)  "
            "**BOQ校验** = 按清单分组比对(见下节), 非取数来源。\n")
    path = Q.write_md(os.path.join(OUTDIR, "floor_takeoff.md"),
                      "227户型 地面算量(几何权威+计算式)", note, rows)
    # 追加: 分组 BOQ 校验表
    gchecks = group_check(rows, rooms)
    extra = ["\n## 分组几何 vs 清单BOQ(开发期校验)\n",
             "| 清单分组 | 几何Σ(m2) | BOQ(m2) | 偏差 | 判定 |",
             "|---|---:|---:|---:|---|"]
    for gc in gchecks:
        d = Q.diff_pct(gc["geo"], gc["boq"])
        vd = Q.verdict(gc["geo"], gc["boq"], tol=8.0)
        extra.append("| %s | %.2f | %.2f | %s | %s |" % (
            gc["group"], gc["geo"], gc["boq"],
            ("%+.1f%%" % d) if d is not None else "—", vd))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(extra) + "\n")

    print("=" * 62)
    print("地面算量(几何权威) — 房间数 %d" % len(rows))
    print("=" * 62)
    for r in rows:
        print("  %-22s %6.2f m2  | %s" % (r["part"][:22], r["value"], r["formula"][:52]))
    tot = sum(r["value"] for r in rows)
    print("  几何楼面合计 %.2f m2" % tot)
    print("\n分组校验:")
    for gc in gchecks:
        print("  %-24s geo=%6.2f boq=%6.2f %s" % (
            gc["group"], gc["geo"], gc["boq"],
            ("(%+.1f%%)" % Q.diff_pct(gc["geo"], gc["boq"]))))
    print("\n产出 -> %s" % path)


if __name__ == "__main__":
    main()
