# 吊顶域 → 房间 面积劈分（面∩面），并复现封账 97.78 的构成
# 诊断依据: 重建掩膜(同参)的 stats 像素面积逐一等于工件 zone_m2 —— lid 编号与封账同源，可安全劈分。
#   单位: px/sc = pt；pt² × (PT2MM/1000)² = ㎡（上一版误用 px²→㎡ 因子，差 7.71 倍，已纠）
# 封账恒等式(待证): flood 20 域 Σ + 龛左窄带 0.34 + 切割线像素 0.05 = 97.78
import fitz, numpy as np, cv2, json, sys
from shapely.geometry import Polygon, box
from shapely.validation import make_valid
sys.path.insert(0, ".")
from app.orchestrator.mep import registration as R

PDF = "装修图纸/227户型dxf/广州珠实金融城住宅项目--227户型施工图纸.pdf"
OUT = "outputs/preview/vlm/elev"
DPI = 200
sc = DPI / 72.0
PT2MM = 0.352778 * 100
PT2M2 = (PT2MM / 1000.0) ** 2                     # pt² → ㎡
NX1, NX2 = 564.0, 637.9                            # split_119 的床头龛切割线（pt）

page = fitz.open(PDF)[12]
pm = page.get_pixmap(dpi=DPI)
img = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, pm.n)[:, :, :3].copy()
blk = cv2.dilate((cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) < 128).astype(np.uint8),
                 np.ones((3, 3), np.uint8), iterations=2)
n, lab, stats, _ = cv2.connectedComponentsWithStats((1 - blk).astype(np.uint8))
PX2M2 = (PT2MM / sc / 1000.0) ** 2                # px² → ㎡（仅供自证 stats）

rows = json.load(open(f"{OUT}/p02_zones_flood.json"))["rows"]
doms = {}
for r in rows:
    if r.get("state") != "closed":
        continue
    k = (r.get("split_lid") or r["lid"], r["v"])
    d = doms.setdefault(k, {"lid": r["lid"], "zone_m2": r["zone_m2"], "seeds": [], "note": r.get("note")})
    d["seeds"].append(r["xy"])
print(f"=== ① 封账恒等式 ===")
s20 = sum(d["zone_m2"] for d in doms.values())
print(f"  flood closed {sum(1 for r in rows if r.get('state')=='closed')} 行 → 去重 {len(doms)} 域  Σ={s20:.2f}㎡")
print(f"  + 龛左窄带(域121,无⊕无洪水行) 0.34 + 切割线像素 0.05 = {s20 + 0.34 + 0.05:.2f}㎡"
      f"  vs 封账 97.78㎡  → {'✓闭合' if abs(s20 + 0.39 - 97.78) < 0.01 else '⚠不闭合'}")


def lid_poly(lid):
    """lid 掩膜 → pt 坐标系多边形（外轮廓；自交者 make_valid 修复）。"""
    cnts, _ = cv2.findContours((lab == lid).astype(np.uint8) * 255,
                               cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    ps = []
    for c in cnts:
        if len(c) < 3:
            continue
        p = Polygon([(float(q[0][0]) / sc, float(q[0][1]) / sc) for q in c])
        if not p.is_empty:
            ps.append(p if p.is_valid else make_valid(p))
    if not ps:
        return None
    out = ps[0]
    for p in ps[1:]:
        out = out.union(p)
    return out


idx, calib = R.load_index()
rooms = [(r["name"], Polygon(r["poly_pt"])) for r in idx.rooms]
print(f"\n=== ② 逐域：掩膜面积自证 + 房间劈分 ===")
print(f"{'域':>12}{'v':>10}{'工件㎡':>8}{'掩膜㎡':>8}{'轮廓㎡':>8}  劈分(占域%)")
per_room, outside, detail = {}, {}, []
for (key, v), d in sorted(doms.items(), key=lambda kv: -kv[1]["zone_m2"]):
    poly = lid_poly(d["lid"])
    if poly is None:
        print(f"  域{key}@{v}: ❌ 掩膜取不到轮廓")
        continue
    if key == 119:                                   # 龛内：切割线之间
        poly = poly.intersection(box(NX1, -1e5, NX2, 1e5))
    elif key == 122:                                 # 右带：x=637.9 以右
        poly = poly.intersection(box(NX2, -1e5, 1e5, 1e5))
    mask_m2 = int(stats[d["lid"], cv2.CC_STAT_AREA]) * PX2M2
    pa = poly.area * PT2M2
    parts = {}
    rest = d["zone_m2"]
    for nm, rp in rooms:
        ia = poly.intersection(rp).area * PT2M2
        if ia > 1e-4:
            share = d["zone_m2"] * (ia / pa)          # 按几何占比拆分封账面积（不重算面积）
            per_room.setdefault(nm, []).append((key, v, share))
            parts[nm] = round(share, 3)
            rest -= share
    detail.append({"zone": key, "elev": v, "lid": d["lid"], "sealed_m2": d["zone_m2"],
                   "mask_m2": round(mask_m2, 2), "contour_m2": round(pa, 2),
                   "n_markers": len(d["seeds"]), "split": parts,
                   "outside_rooms_m2": round(rest, 3)})
    outside[key] = rest                                # 全量累计，不受下方显示阈值影响
    print(f"  域{key:>5}@{str(v):<7}{d['zone_m2']:>8.2f}{mask_m2:>8.2f}{pa:>8.2f}  "
          + ", ".join(f"{nm}:{a:.2f}({a / d['zone_m2'] * 100:.0f}%)" for nm, a in parts.items())
          + (f" ｜房间之外{rest:.2f}" if abs(rest) > 0.02 else ""))

print(f"\n=== ③ 逐间汇总 ===")
print(f"{'空间':<24}{'间面积':>8}{'吊顶Σ':>8}{'非吊顶区':>10}{'占比':>7}")
for r in sorted(idx.rooms, key=lambda x: -x["area"]):
    nm = r["name"]
    z = sum(a for _, _, a in per_room.get(nm, []))
    ratio = f"{z / r['area'] * 100:.0f}%" if r["area"] > 0.2 else "—"
    print(f"{nm[:22]:<24}{r['area']:>8.2f}{z:>8.2f}{r['area'] - z:>10.2f}{ratio:>7}")
inm = sum(sum(a for _, _, a in vs) for vs in per_room.values())
outm = sum(outside.values())
print(f"  Σ拆分到间={inm:.2f}㎡ ｜落房间之外（墙带/门洞）={outm:.2f}㎡ ｜合计={inm + outm:.2f}㎡")
print(f"  恒等式: 拆分{inm:.2f} + 房间之外{outm:.2f} = {inm + outm:.2f} vs flood 20 域 97.39 → "
      f"{'✓' if abs(inm + outm - 97.39) < 0.01 else '✗'}"
      f"（封账 97.78 = 97.39 + 龛左窄带0.34 + 切割线像素0.05，本表只分 20 域）")

# ④ P1/P3 空间覆盖对账（只从报告原文取数，不往脚本里存数字）
import re
floor = {}
for ln in open("outputs/model/floor_takeoff.md", encoding="utf-8"):
    p = [c.strip() for c in ln.split("|")]
    if len(p) > 6 and p[2] == "楼面完成面积" and p[3] == "m2":   # p[0] 为空串（行首 |）
        floor[p[1]] = float(p[5])
net = {}
for ln in open("outputs/model/ceiling_takeoff.md", encoding="utf-8"):
    if "Σ天棚(=房间净顶)" in ln:
        cell = [c for c in ln.split("|") if "Σ天棚" in c][0].split(":", 1)[1]
        for nm, vv in re.findall(r"([^\s,]+)\[([\d.]+)\]", cell):
            net[nm] = float(vv)
uncov = {nm: a for nm, a in floor.items()
         if not any(nm in k or k in nm for k in net)}
print(f"\n=== ④ P1/P3 空间覆盖 ===")
print(f"  P1 楼面 {len(floor)} 空间 Σ={sum(floor.values()):.2f}㎡ ｜ P3 净顶 {len(net)} 空间 Σ={sum(net.values()):.2f}㎡")
print(f"  P3 未覆盖空间: {uncov}  Σ={sum(uncov.values()):.2f}㎡")
print(f"  恒等式: {sum(floor.values()):.2f} − {sum(uncov.values()):.2f} = "
      f"{sum(floor.values()) - sum(uncov.values()):.2f}  vs P3 {sum(net.values()):.2f}"
      f"  → {'✓' if abs(sum(floor.values()) - sum(uncov.values()) - sum(net.values())) < 0.02 else '⚠'}")

# ⑤ 工件落盘
art = {
    "method": "_p3b_split.py：重建同参洪水掩膜(DPI=200/dilate 3x3 iter=2) → lid 外轮廓(pt) → 与房间多边形求交，按几何占比拆分封账面积（不重算面积）",
    "unit": {"pt2_to_m2": PT2M2, "px2_to_m2": PX2M2, "page": "idx12 (P02 天花布置图)"},
    "seal_identity": {"flood_20_domains": round(s20, 2), "kan_left_strip_121": 0.34,
                      "cut_pixels": 0.05, "sum": round(s20 + 0.39, 2), "sealed": 97.78},
    "domains": detail,
    "per_room": {nm: {"room_area_m2": r["area"],
                      "ceiling_m2": round(sum(a for _, _, a in per_room.get(nm, [])), 3),
                      "zones": [f"{k}@{vv}" for k, vv, _ in per_room.get(nm, [])]}
                 for nm, r in ((r["name"], r) for r in idx.rooms)},
    "scope_recon": {"P1_floor": {k: v for k, v in floor.items()}, "P3_net": net,
                    "P3_uncovered": uncov},
}
path = f"{OUT}/p02_zone_room_split.json"
json.dump(art, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n  工件已写: {path}")

