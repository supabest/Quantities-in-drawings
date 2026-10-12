"""modeling.wall_vs_ifc — 三维模型 ↔ 平面算量 交叉校验(查漏算立面/房间)。

原则: 工程量数值仍由几何(plan)权威给出; 本模块把**导出的 IFC 模型**当独立校验源,
从 IfcSpace 周长×净高 反算每间墙毛面积, 与 modeling.wall_geometry 按房间对齐比对:
  · 一致性: 模型(空间)gross vs 算量gross —— 同源于 base_extract, 差应≈0; 一旦某侧
    改了净高/围合会立即暴露(参数漂移/口径不一致)。
  · 覆盖(核心·查漏算): 模型每个 IfcSpace 是否都有算量行; 反之算量有而模型无也报。
  · 独立枚举: IfcWall(真墙段)数量/总长 —— 模型侧对"墙"的独立盘点, 与算量周长汇总对照。
随着建模细化(洞口/饰面/吊顶入模), 模型侧将与平面侧产生**有意义的分歧**, 届时以本表
定位漏算; 这也是"以后直接用模型完成大部分算量"的落地入口。

用法: ./venv/bin/python -m modeling.wall_vs_ifc
产出: outputs/model/wall_vs_ifc.md
"""
import os
import math
from . import base_extract as B
from . import wall_geometry as W
from . import quant_table as Q

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IFC_PATH = os.path.join(ROOT, "outputs", "model", "227_model_ifc4.ifc")


def _ensure_ifc():
    if not os.path.exists(IFC_PATH):
        from . import ifc_export
        ifc_export.export_all()


def _solid(space_or_wall):
    return space_or_wall.Representation.Representations[0].Items[0]


def _poly_xz(solid):
    return [(p.Coordinates[0], p.Coordinates[1]) for p in solid.SweptArea.OuterCurve.Points]


def _perimeter_m(P):
    return sum(math.dist(P[i], P[(i + 1) % len(P)]) for i in range(len(P)))


def _long_side_m(P):
    Q2 = P[:-1] if (len(P) > 1 and P[0] == P[-1]) else P
    if not Q2:
        return 0.0
    n = len(Q2)
    return max(math.dist(Q2[i], Q2[(i + 1) % n]) for i in range(n))


def _centroid_m(el):
    c = el.ObjectPlacement.RelativePlacement.Location.Coordinates
    return (float(c[0]), float(c[1]))


def model_side():
    """读 IFC 几何(不依赖 Pset) → [space...{name,perim_m,h_m,gross_m2,npts,cx_m,cy_m}] + meta。"""
    import ifcopenshell
    _ensure_ifc()
    f = ifcopenshell.open(IFC_PATH)
    spaces, walls = f.by_type("IfcSpace"), f.by_type("IfcWall")
    # 模型侧规范墙高 = IfcWall 挤出 Depth(米); 取首个有效值
    wall_h = 0.0
    for w in walls:
        try:
            wall_h = float(_solid(w).Depth)
            if wall_h > 0:
                break
        except Exception:
            pass
    out = []
    for s in spaces:
        P = _poly_xz(_solid(s))
        per = _perimeter_m(P)
        cx, cy = _centroid_m(s)
        # 该 IfcSpace 自身挤出顶高(=主吊顶净高): placement.z + Depth
        try:
            z0 = float(s.ObjectPlacement.RelativePlacement.Location.Coordinates[2])
        except Exception:
            z0 = 0.0
        try:
            dep = float(_solid(s).Depth)
        except Exception:
            dep = 0.0
        h_top = z0 + dep                       # m(吊顶完成面)
        out.append({"name": s.Name or "(无标注)", "perim_m": per, "h_m": wall_h,
                    "space_h_m": h_top, "gross_m2": per * wall_h,
                    "space_gross_m2": per * h_top,
                    "npts": len(P), "cx_m": cx, "cy_m": cy})
    wall_len = 0.0
    for w in walls:
        try:
            wall_len += _long_side_m(_poly_xz(_solid(w)))
        except Exception:
            pass
    # 洞口清点: 从 IfcOpeningElement 几何回读(宽=矩形长边, 高=挤出 Depth)
    ops = f.by_type("IfcOpeningElement")
    n_void = len(f.by_type("IfcRelVoidsElement"))
    n_door = n_win = 0
    op_area = 0.0
    op_width = 0.0
    for op in ops:
        try:
            sol = _solid(op)
            w = _long_side_m(_poly_xz(sol))
            h = float(sol.Depth)
        except Exception:
            continue
        op_area += w * h
        op_width += w
        if h >= 1.8:                       # 门高2.1 / 窗高1.5
            n_door += 1
        else:
            n_win += 1
    return out, {"n_space": len(spaces), "n_wall": len(walls),
                 "wall_len_m": wall_len, "wall_h_m": wall_h,
                 "n_opening": len(ops), "n_void": n_void, "n_orphan": len(ops) - n_void,
                 "n_door": n_door, "n_win": n_win,
                 "opening_area_m2": op_area, "opening_width_m": op_width}


def _pair_by_centroid(takeoff, spaces, tol_m=2.0):
    """按质心最近邻把每个算量行配对到一个模型空间(对重名鲁棒)。"""
    used = [False] * len(spaces)
    for tk in takeoff:
        best, bd = -1, 1e18
        for i, md in enumerate(spaces):
            if used[i]:
                continue
            d = math.hypot(tk["cx_m"] - md["cx_m"], tk["cy_m"] - md["cy_m"])
            if d < bd:
                bd, best = d, i
        if best >= 0 and bd <= tol_m:
            tk["md"] = spaces[best]
            used[best] = True
        else:
            tk["md"] = None
    matched = {id(tk["md"]) for tk in takeoff if tk["md"]}
    return [md for md in spaces if id(md) not in matched]


def main():
    data = B.build()
    ox, oy = data["bounds"][0], data["bounds"][1]
    spaces, meta = model_side()
    takeoff = []
    for r in data["rooms"]:
        if r["is_out"] or not r.get("poly"):
            continue
        w = W.extract_one(r, data["doors"], data["windows"])
        if not w:
            continue
        w["cx_m"] = (r["cx"] - ox) / 1e3
        w["cy_m"] = (r["cy"] - oy) / 1e3
        takeoff.append(w)
    leftover = _pair_by_centroid(takeoff, spaces)

    rows = []
    for tk in takeoff:
        md = tk["md"]
        if md is None:
            rows.append(Q.row("漏算核对·%s" % tk["name"], "算量有墙行·模型无对应 IfcSpace", "m",
                              "算量周长%.2fm" % (tk["perimeter_mm"] / 1e3), tk["perimeter_mm"] / 1e3,
                              "→ 检查该房间是否未入模", check=None))
            continue
        rows.append(Q.row(
            "模型↔算量·%s" % tk["name"], "围合周长(高度无关护栏)", "m",
            "算量Σ边%.2fm ; 模型 IfcSpace%.2fm" % (
                tk["perimeter_mm"] / 1e3, md["perim_m"]),
            round(tk["perimeter_mm"] / 1e3, 2),
            "净高%.3fm(主吊顶)/毛墙%.2f 净墙%.2f; 模型空顶高%.3fm; IfcSpace%d点" % (
                tk["h_net_mm"] / 1e3, tk["gross_m2"], tk["net_m2"], md["space_h_m"], md["npts"]),
            check=round(md["perim_m"], 2)))
    for md in leftover:
        rows.append(Q.row("漏算核对·%s" % md["name"], "模型有 IfcSpace·算量无墙行", "m2",
                          "模型 gross=%.2f" % md["gross_m2"], 0.0,
                          "质心%.1f,%.1f → 检查算量是否跳过该间" % (md["cx_m"], md["cy_m"]), check=None))

    sum_tk_gross = round(sum(r["gross_m2"] for r in takeoff), 2)          # 主吊顶净高 gross
    sum_tk_net = round(sum(r["net_m2"] for r in takeoff), 2)
    sum_md_gross = round(sum(s["gross_m2"] for s in spaces), 2)           # 结构净高 gross(IfcWall)
    sum_md_sgross = round(sum(s["space_gross_m2"] for s in spaces), 2)    # 模型 IfcSpace 顶高 gross(吊顶)
    sum_tk_per = round(sum(r["perimeter_mm"] for r in takeoff) / 1e3, 2)
    sum_md_per = round(sum(s["perim_m"] for s in spaces), 2)
    rows.append(Q.row("总量", "围合周长合计(高度无关护栏)", "m",
                      "算量Σ边 / 模型ΣIfcSpace边", sum_tk_per,
                      "房间数 %d; 与净高无关→纯验围合一致" % len(takeoff), check=sum_md_per))
    rows.append(Q.row("总量", "算量毛墙合计(主吊顶净高)", "m2", "Σ各间 边×主吊顶净高", sum_tk_gross,
                      "验吊顶标高已传播到模型(模型空顶高 gross=%.2f)" % sum_md_sgross, check=sum_md_sgross))
    rows.append(Q.row("总量", "算量净墙合计(扣洞口)", "m2", "毛墙 − 门/窗洞口", sum_tk_net,
                      "洞口合计=%.2f m²" % (sum_tk_gross - sum_tk_net), check=None))
    rows.append(Q.row("吊顶下沉", "结构毛墙 − 主吊顶毛墙", "m2",
                      "Σ边×(结构净高−主吊顶)", round(sum_md_gross - sum_tk_gross, 2),
                      "结构gross%.2f − 吊顶gross%.2f = 吊顶以上墙面(不入饰面)" % (
                          sum_md_gross, sum_tk_gross), check=None))
    rows.append(Q.row("覆盖核对", "IfcSpace 数 vs 算量墙行数", "个",
                      "模型 %d / 算量 %d" % (meta["n_space"], len(takeoff)), meta["n_space"],
                      "差=%d(应0)" % (meta["n_space"] - len(takeoff)), check=len(takeoff)))
    rows.append(Q.row("模型独立枚举", "IfcWall 真墙段总长", "m",
                      "Σ IfcWall 矩形最长边", round(meta["wall_len_m"], 2),
                      "段数 %d; 结构净高 %.3fm; 算量周长汇总=%.2fm(内墙双面计)" % (
                          meta["n_wall"], meta["wall_h_m"], sum_tk_per), check=None))

    # D. 洞口(门/窗)清点与开洞覆盖
    tk_op_area = round(sum(r["door_area_m2"] + r["win_area_m2"] for r in takeoff), 2)
    rows.append(Q.row("洞口清点", "模型洞口面积(门%d/窗%d)" % (meta["n_door"], meta["n_win"]), "m2",
                      "Σ IfcOpeningElement 宽×高", round(meta["opening_area_m2"], 2),
                      "宽度合计 %.1fm; 算量每间扣(共享墙双计)=%.2f m²→口径差异非错误" % (
                          meta["opening_width_m"], tk_op_area), check=None))
    rows.append(Q.row("开洞覆盖", "已 voids / 总洞口", "个",
                      "void %d / 总 %d" % (meta["n_void"], meta["n_opening"]), meta["n_void"],
                      "孤儿 %d (垭口/外窗/非strict边界)→待查或需建筑门窗表" % meta["n_orphan"],
                      check=meta["n_opening"]))

    note = ("> 交叉校验口径: 数值以平面算量为权威, 模型侧为独立校验列, 按质心最近邻配对。"
            "围合(周长)为高度无关护栏→应≈一致; 墙面毛墙已改按**逐间主吊顶净高**(P3b), 模型 IfcSpace 顶高=同一吊顶标高→二者 gross 相等即验证吊顶已入模。"
            "结构净高(IfcWall)与主吊顶之差=吊顶下沉量, 单列披露。\n")
    out = Q.write_md(os.path.join(ROOT, "outputs", "model", "wall_vs_ifc.md"),
                     "227户型 · 三维模型↔墙面算量 交叉校验", note, rows)
    miss_tk = sum(1 for r in takeoff if r["md"] is None)
    print("模型 IfcSpace %d, 算量墙行 %d, IfcWall %d(总长%.1fm,结构净高%.3fm)" % (
        meta["n_space"], len(takeoff), meta["n_wall"], meta["wall_len_m"], meta["wall_h_m"]))
    print("围合周长: 算量 %.2fm / 模型 %.2fm (差 %.2f); 配对: 未入模 %d, 无算量空间 %d" % (
        sum_tk_per, sum_md_per, sum_tk_per - sum_md_per, miss_tk, len(leftover)))
    print("毛墙(主吊顶净高): 算量 %.2f / 模型空顶高 %.2f (差 %.2f); 净墙 %.2f; 吊顶下沉 %.2f m²" % (
        sum_tk_gross, sum_md_sgross, sum_tk_gross - sum_md_sgross, sum_tk_net, sum_md_gross - sum_tk_gross))
    print("洞口: 总 %d (门%d/窗%d), 已开洞 %d, 孤儿 %d, 模型洞口面积 %.2f m²" % (
        meta["n_opening"], meta["n_door"], meta["n_win"], meta["n_void"], meta["n_orphan"], meta["opening_area_m2"]))
    print("→", out)


if __name__ == "__main__":
    main()
