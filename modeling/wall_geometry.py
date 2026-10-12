"""modeling.wall_geometry — 墙面几何引擎(几何权威 + 计算式)。

原则(用户指令): 墙面面积一律由几何从图纸独立算出, 不抄 BOQ。
每间净墙面 = Σ(墙边长 × 有效净高) − Σ(门洞口宽×门高) − Σ(窗洞口宽×窗高)
  · 墙边长: 房间围合多边形各边(内墙面线), 来自 05平面 rev-33 几何。
  · 洞口宽: 把 DOOR/WINDOW 线段投影到墙边方向做"一维区间并集"去重(修复上次多平行线重复累加→负面积的坑)。
  · 高度: 净高3020 + 门2100/窗1500(参数, 待立面/SD精确; 立面DIM可后续替换)。
BOQ 仅作开发期校验。生产环境剔除校验列即为成品。

用法: ./venv/bin/python -m modeling.wall_geometry
"""
import math
import numpy as np
from . import base_extract as B
from . import quant_table as Q

V = B.V
H_NET = V["clear_to_soffit"]   # 3020mm
H_DOOR = 2100.0                # mm 参数
H_WIN = 1500.0                 # mm 参数
TOL = 400.0                    # mm 洞口中点到墙边容差
PAR_TOL = 25.0                 # mm 窗线段与墙边平行容差


def _edges(poly):
    P = np.array(poly)
    n = len(P)
    out = []
    for i in range(n):
        x0, y0 = P[i]
        x1, y1 = P[(i + 1) % n]
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 100:                     # 跳过碎边
            continue
        out.append((float(x0), float(y0), float(x1), float(y1), L))
    return out


def _seg_ang(x0, y0, x1, y1):
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180


def _para(a, b, tol=PAR_TOL):
    d = abs(a - b) % 180
    return min(d, 180 - d) <= tol


def _pt_seg_dist(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L2))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def opening_width(segs, e, parallel_only):
    """洞口线段投影到墙边方向, 一维区间并集 → 真实洞口宽度(mm)。"""
    ex0, ey0, ex1, ey1, L = e
    dx, dy = (ex1 - ex0) / L, (ey1 - ey0) / L
    eang = _seg_ang(ex0, ey0, ex1, ey1)
    ivs = []
    for sx0, sy0, sx1, sy1 in segs:
        mx, my = (sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0
        if _pt_seg_dist(mx, my, ex0, ey0, ex1, ey1) > TOL:
            continue
        if parallel_only and not _para(_seg_ang(sx0, sy0, sx1, sy1), eang):
            continue                       # 剔除窗的垂直封口线
        t0 = (sx0 - ex0) * dx + (sy0 - ey0) * dy
        t1 = (sx1 - ex0) * dx + (sy1 - ey0) * dy
        a, b = max(min(t0, t1), -20.0), min(max(t0, t1), L + 20.0)
        if b - a > 1:
            ivs.append((a, b))
    if not ivs:
        return 0.0
    ivs.sort()
    cov = 0.0
    cs, ce = ivs[0]
    for a, b in ivs[1:]:
        if a <= ce + 60:
            ce = max(ce, b)
        else:
            cov += ce - cs
            cs, ce = a, b
    cov += ce - cs
    return min(cov, L)                     # 上限: 不超墙边长


def opening_intervals(segs, e, parallel_only, min_w=200.0):
    """与 opening_width 同逻辑, 但返回合并后的**逐樘区间** [(t0,t1)](沿墙边参数, mm)。
    用于模型开洞: 每个区间 = 一个物理洞口。min_w 以下视为碎线丢弃。"""
    ex0, ey0, ex1, ey1, L = e
    dx, dy = (ex1 - ex0) / L, (ey1 - ey0) / L
    eang = _seg_ang(ex0, ey0, ex1, ey1)
    ivs = []
    for sx0, sy0, sx1, sy1 in segs:
        mx, my = (sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0
        if _pt_seg_dist(mx, my, ex0, ey0, ex1, ey1) > TOL:
            continue
        if parallel_only and not _para(_seg_ang(sx0, sy0, sx1, sy1), eang):
            continue
        t0 = (sx0 - ex0) * dx + (sy0 - ey0) * dy
        t1 = (sx1 - ex0) * dx + (sy1 - ey0) * dy
        a, b = max(min(t0, t1), -20.0), min(max(t0, t1), L + 20.0)
        if b - a > 1:
            ivs.append((a, b))
    if not ivs:
        return []
    ivs.sort()
    merged = []
    cs, ce = ivs[0]
    for a, b in ivs[1:]:
        if a <= ce + 60:
            ce = max(ce, b)
        else:
            merged.append((cs, ce))
            cs, ce = a, b
    merged.append((cs, ce))
    out = []
    for s, t in merged:
        s2, t2 = max(0.0, s), min(L, t)
        if t2 - s2 >= min_w:
            out.append((s2, t2))
    return out


def extract_one(room, doors, windows):
    poly = room.get("poly")
    if not poly or len(poly) < 3:
        return None
    edges = _edges(poly)
    if not edges:
        return None
    h_net = float(room.get("soffit_mm", H_NET))   # 逐间主吊顶净高(缺则结构净高3020)
    per = sum(e[4] for e in edges)
    door_w = sum(opening_width(doors, e, parallel_only=False) for e in edges)
    win_w = sum(opening_width(windows, e, parallel_only=True) for e in edges)
    door_w = min(door_w, per)              # 防超界
    win_w = min(win_w, per)
    gross = per * h_net / 1e6
    a_door = door_w * H_DOOR / 1e6
    a_win = win_w * H_WIN / 1e6
    net = max(gross - a_door - a_win, 0.0)
    nm = "+".join(room["names"]) or "(无标注@%.0f,%.0f)" % (room["cx"] / 1e3, room["cy"] / 1e3)
    hsrc = "吊顶净高" if room.get("ceiling_levels") else "结构净高"
    formula = ("净墙=Σ边%.2f×%.3f(%s) − 门%.2f×%.2f − 窗%.2f×%.2f" % (
        per / 1e3, h_net / 1e3, hsrc, door_w / 1e3, H_DOOR / 1e3, win_w / 1e3, H_WIN / 1e3))
    return {"name": nm, "comp_id": room["comp_id"], "perimeter_mm": round(per),
            "h_net_mm": round(h_net), "ceiling_levels": room.get("ceiling_levels", []),
            "gross_m2": round(gross, 2), "door_w_mm": round(door_w), "win_w_mm": round(win_w),
            "door_area_m2": round(a_door, 2), "win_area_m2": round(a_win, 2),
            "net_m2": round(net, 2), "formula": formula}


def extract_all(data=None):
    d = data if data is not None else B.build()
    out = []
    for r in d["rooms"]:
        if r["is_out"]:
            continue
        w = extract_one(r, d["doors"], d["windows"])
        if w:
            out.append(w)
    return out


# ---- 复用 wall_boq 的房间分组做校验(非来源) ----
from . import wall_boq  # noqa: E402

ROOM_MAP = {
    "开放区(玄关/餐厅/厨房/客厅/安全间)": ["客厅/茶室", "餐厅", "玄关", "中厨", "储物间"],
    "主卧+衣帽间": ["主人卧室", "主人衣帽间"],
    "卧室一": ["卧室一"], "卧室二": ["卧室二"], "收纳间": ["收纳间"],
    "主卫": ["主人浴室"], "浴室一": ["浴室一"], "浴室二": ["浴室二"],
    "浴室三": ["浴室三"], "生活阳台": ["生活阳台"],
}


def group_geo(results, names):
    """把模型房间按名(子串)汇总几何, comp_id 去重。返回 {net, gross, door_w_mm, win_w_mm, per_mm}。"""
    seen = set()
    acc = {"net": 0.0, "gross": 0.0, "door_w_mm": 0.0, "win_w_mm": 0.0, "perimeter_mm": 0.0}
    for res in results:
        for n in names:
            if n in res["name"] and res["comp_id"] not in seen:
                seen.add(res["comp_id"])
                acc["net"] += res["net_m2"]
                acc["gross"] += res["gross_m2"]
                acc["door_w_mm"] += res["door_w_mm"]
                acc["win_w_mm"] += res["win_w_mm"]
                acc["perimeter_mm"] += res["perimeter_mm"]
                break
    for k in ("net", "gross"):
        acc[k] = round(acc[k], 2)
    for k in ("door_w_mm", "win_w_mm", "perimeter_mm"):
        acc[k] = round(acc[k])
    return acc


def build_rows(results, boq):
    """按 BOQ 分组把几何产出→墙面基层(m2)+踢脚线(m)行。单一真源, main/rows 共用。"""
    base_rows, bboard_rows = [], []
    covered = set()
    for boq_room, model_names in ROOM_MAP.items():
        g = group_geo(results, model_names)
        # covered = “已被该 BOQ 组考虑过”，与是否成行无关——置于 net 判空之前，
        # 否则几何为 0 的组会漏标 covered，其空间跌入下方补计分支产出幻影行。
        covered.update(id(r) for r in results if any(n in r["name"] for n in model_names))
        if not g["net"]:
            continue
        nface = len([r for r in results if any(n in r["name"] for n in model_names)])
        havg = (g["gross"] * 1e6 / g["perimeter_mm"] / 1e3) if g["perimeter_mm"] else 0.0
        gformula = ("Σ各间(边×主吊顶净高)=%.2fm²毛; 均高%.3fm; 扣门%.2f×%.2f 窗%.2f×%.2f" % (
            g["gross"], havg, g["door_w_mm"] / 1e3, H_DOOR / 1e3,
            g["win_w_mm"] / 1e3, H_WIN / 1e3))
        base_rows.append(Q.row(boq_room, "墙面抹灰/腻子/涂料基层", "m2",
                               gformula + " (%d间)" % nface,
                               g["net"], "05平面围合", check=None))
        bb_m = (g["perimeter_mm"] - g["door_w_mm"]) / 1000.0
        bboard_rows.append(Q.row(boq_room, "踢脚线", "m",
                                 "净周长%.2f − 门洞%.2f" % (g["perimeter_mm"] / 1e3, g["door_w_mm"] / 1e3),
                                 round(bb_m, 2), "05平面围合", check=None))
    # 同上: BOQ 无对应列的空间不得静默丢弃。**2026-10-12 用户裁定**: 电梯厅/过厅属精装范围,
    # 墙面与天花同口径补计。图上无房间名的窄条不出行——它紧靠电梯厅, 两侧会把同一道墙重复计一次。
    for res in results:
        if id(res) in covered or res["name"].startswith("("):
            continue
        havg = (res["gross_m2"] * 1e6 / res["perimeter_mm"] / 1e3) if res["perimeter_mm"] else 0.0
        base_rows.append(Q.row(res["name"], "墙面抹灰/腻子/涂料基层", "m2",
                               "Σ各间(边×主吊顶净高)=%.2fm²毛; 均高%.3fm; 扣门%.2f×%.2f 窗%.2f×%.2f"
                               " (BOQ无此列, 纯几何; 1间)" % (
                                   res["gross_m2"], havg, res["door_w_mm"] / 1e3, H_DOOR / 1e3,
                                   res["win_w_mm"] / 1e3, H_WIN / 1e3),
                               res["net_m2"], "05平面围合", check=None))
        bboard_rows.append(Q.row(res["name"], "踢脚线", "m",
                                 "净周长%.2f − 门洞%.2f (BOQ无此列)" % (
                                     res["perimeter_mm"] / 1e3, res["door_w_mm"] / 1e3),
                                 round((res["perimeter_mm"] - res["door_w_mm"]) / 1000.0, 2),
                                 "05平面围合", check=None))
    return base_rows, bboard_rows


def rows(data=None):
    """P5 汇总入口: 返回墙面全部 Q.row(基层+踢脚线)。"""
    results = extract_all(data)
    try:
        boq = wall_boq.wall_total_per_room()
    except Exception:
        boq = {}
    base_rows, bboard_rows = build_rows(results, boq)
    return base_rows + bboard_rows


def main():
    results = extract_all()
    geo_net = sum(r["net_m2"] for r in results)
    geo_gross = sum(r["gross_m2"] for r in results)
    print("=" * 66)
    print("墙面几何(权威) — 房间数 %d  毛%.2f → 净%.2f m2" % (len(results), geo_gross, geo_net))
    print("=" * 66)
    for r in sorted(results, key=lambda x: -x["net_m2"]):
        print("  %-20s 毛%6.2f 门%5.2f 窗%5.2f → 净%6.2f | %s" % (
            r["name"][:20], r["gross_m2"], r["door_area_m2"], r["win_area_m2"],
            r["net_m2"], r["formula"]))

    # 分组: 几何产出 墙面基层(m2) + 踢脚线(m); BOQ饰面仅作下限校验
    try:
        boq = wall_boq.wall_total_per_room()
    except Exception:
        boq = {}
    base_rows, bboard_rows = build_rows(results, boq)
    path = "outputs/model/wall_geometry.md"
    Q.write_md(path, "227户型 墙面算量(几何权威+计算式)",
               "**数值来源**=模型围合边长×净高−洞口(可独立复算)  **高度**=逐间主吊顶净高(实测标高, 缺则结构3020)/门2100/窗1500(参数)  "
               "**几何口径**=整面内墙(抹灰/腻子/涂料基层全数)。\n\n"
               "> 特殊装饰面层(硬包/瓷砖/石材/木饰面背景墙)为全墙的**局部子集**, 需立面分区/人工命名, 见文末校验。\n",
               base_rows + bboard_rows)
    # 追加: 几何全墙 vs BOQ饰面(局部) 的方向性校验(几何应 ≥ 饰面, 否则查)
    lines = ["\n## 校验: 几何全墙(基层) vs BOQ装饰面层(局部)\n",
             "BOQ墙柱面项=局部饰面(背景墙/瓷砖/硬包); 几何=整面内墙。合理关系: 几何全墙 ≥ BOQ饰面。\n",
             "| 房间 | 几何全墙净(m2) | BOQ饰面(m2) | 饰面占比 | 判定 |",
             "|---|---:|---:|---:|---|"]
    for r in base_rows:
        g = r["value"]
        b = boq.get(r["part"])
        if b and g < b * 0.98:
            vd = "查: 几何<饰面(高度/围合不足?)"
        elif b:
            vd = "饰面占全墙%.0f%%(合理)" % (b / g * 100)
        else:
            vd = "无饰面项"
        lines.append("| %s | %.2f | %s | %s | %s |" % (
            r["part"], g, ("%.2f" % b) if b else "—",
            ("%.0f%%" % (b / g * 100)) if b else "—", vd))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n校验: 几何全墙(基层) vs BOQ饰面(局部)")
    for r in base_rows:
        b = boq.get(r["part"])
        print("  %-24s 几何全墙=%6.2f  BOQ饰面=%6s  饰面占比=%s" % (
            r["part"], r["value"], ("%.2f" % b) if b else "—",
            ("%.0f%%" % (b / r["value"] * 100)) if b else "—"))
    print("\n产出 -> %s" % path)


if __name__ == "__main__":
    main()
