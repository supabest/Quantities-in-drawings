# 51.66㎡ 差额定性：把 P02 分级吊顶域配准到 05 平面房间，逐间验 吊顶 ⊆ 净顶
# 复用机电线封账配准（m2_attribution.json 含 calib + PDF pt 房间多边形），不重跑网格搜索
import sys, json, fitz
sys.path.insert(0, ".")
from app.orchestrator.mep import registration as R

PAGES = (12, 13, 14)
ZONES = "outputs/preview/vlm/elev/p02_zones_flood.json"
CEIL_MD = "outputs/model/ceiling_takeoff.md"          # 各间净顶（基层口径）

idx, calib = R.load_index()
print(f"标定（P01 封账）: tx={calib['tx']:.2f} ty={calib['ty']:.2f} "
      f"离墙={calib['wall_fit_px']:.2f}px  房间 {len(idx.rooms)} 个")

# ① 标定能否直接用于 idx12？—— 三页与 P01 同位则 wall_fit 代价应与 P01 同量级
doc = fitz.open(R.DEFAULT_PDF)
print("\n=== ① 同一标定在各页的贴墙代价（验证页面同位，不假设）===")
for i in (11,) + PAGES:
    dist, sc = R.wall_distance_map(doc[i])
    tot = n = 0.0
    for r in idx.rooms:
        poly = r["poly_pt"]
        for k in range(len(poly)):
            x1, y1 = poly[k]
            x2, y2 = poly[(k + 1) % len(poly)]
            L = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            for t in [j / max(int(L), 1) for j in range(0, int(L), 4)]:
                px, py = int((x1 + (x2 - x1) * t) * sc), int((y1 + (y2 - y1) * t) * sc)
                if 0 <= py < dist.shape[0] and 0 <= px < dist.shape[1]:
                    tot += min(dist[py, px], 20.0); n += 1
    print(f"  idx{i}: 房间轮廓离墙均值 = {tot / max(n, 1):.2f}px"
          + ("   ← 基准页" if i == 11 else ""))

# ② 逐域归属（只取 state=closed；leak_bg 的 565.42 是背景泄漏带哨兵值非面积；同域去重）
rows = json.load(open(ZONES))["rows"]
leak = [r for r in rows if r.get("state") != "closed"]
clo = {}
for r in rows:
    if r.get("state") == "closed":
        key = (r.get("split_lid") or r["lid"], r["v"])
        clo[key] = max(clo.get(key, 0.0), r.get("zone_m2") or 0.0)
print(f"\n=== ② P02 吊顶域 → 房间归属 ===")
print(f"  原 {len(rows)} 行：closed {len([r for r in rows if r.get('state')=='closed'])} 行"
      f"（去重得 {len(clo)} 域，Σ={sum(clo.values()):.2f}㎡）｜"
      f"leak_bg {len(leak)} 行（背景泄漏带，不计入）")
SEAL = {121: 0.34, 513: 1.25, 791: 0.45}      # 封账有而 flood 工件无的域（后续引线/人工补）
print(f"  封账 21 域 Σ=97.78㎡；flood 缺 {'/'.join(map(str, SEAL))} 共 {sum(SEAL.values()):.2f}㎡"
      f"（由 §3b 引线/裁定补）→ 97.78－{sum(SEAL.values()):.2f}="
      f"{97.78 - sum(SEAL.values()):.2f} 应≈{sum(clo.values()):.2f}")

by_room, orphan = {}, []
for (lid, v), area in clo.items():
    z = next(r for r in rows if (r.get("split_lid") or r["lid"]) == lid
             and r.get("v") == v and r.get("state") == "closed")
    x, y = z["xy"]
    room = idx.room_of(x, y)
    tag = f"域{lid}@{v}"
    if room is None:
        orphan.append((tag, area, (round(x), round(y))))
    else:
        by_room.setdefault(room, []).append((tag, area))
print(f"  未归属（连 12pt 墙带容差都没落进）: {len(orphan)} 个"
      f" Σ={sum(a for _, a, _ in orphan):.2f}㎡")
for t, a, c in orphan:
    print(f"      {t} {a:.2f}㎡ @{c}")
m2net = {r["name"]: r["area"] / 1e6 for r in idx.rooms}   # 配准工件自带的房间面积（mm²→㎡）

# ③ 逐间：净顶 vs 吊顶 Σ（净顶取 ceiling_takeoff.md 表内值）
import re
net = {}
for ln in open(CEIL_MD, encoding="utf-8"):
    if "Σ天棚(=房间净顶)" in ln:
        cell = [c for c in ln.split("|") if "Σ天棚" in c][0].split(":", 1)[1]
        for nm, v in re.findall(r"([^\s,]+)\[([\d.]+)\]", cell):   # 名内允许 + 与 /
            net[nm] = float(v)          # 逐空间取数（开放区行含 3 个空间）
NET_REF = 162.16   # P3 净顶基数：2026-10-12 裁定补计电梯厅/过厅后的封账值（旧 149.44 作废）
sum_net = sum(net.values())
print(f"  抽得空间 {len(net)} 个，Σ净顶={sum_net:.2f}㎡（应={NET_REF}；旧基准 149.44 已作废）")
assert abs(sum_net - NET_REF) < 0.05, f"净顶基数漂移 {sum_net:.2f} ≠ {NET_REF}，逐间对比口径须重新确认"
print(f"\n=== ③ 逐间对比（净顶合计 {sum_net:.2f}㎡）===")
print(f"{'空间':<26}{'净顶㎡':>8}{'吊顶㎡':>8}{'差(非吊顶区)':>12}  域")
tot_net = tot_z = 0.0
for name, nv in sorted(net.items(), key=lambda kv: -kv[1]):
    zs = [v for r, vs in by_room.items() if r in name or name in r for v in vs]
    zsum = sum(a for _, a in zs)
    tot_net += nv; tot_z += zsum
    print(f"{name[:24]:<26}{nv:>8.2f}{zsum:>8.2f}{nv - zsum:>12.2f}  "
          + ",".join(t for t, _ in zs))
unmapped = sum(a for r, vs in by_room.items() for t, a in vs
               if not any(r in n or n in r for n in net))
print(f"\n  Σ净顶 {tot_net:.2f}㎡ ｜ 已落间吊顶 {tot_z:.2f}㎡ ｜ "
      f"落在上述空间之外 {unmapped:.2f}㎡ ｜ 未归属 {sum(a for _, a, _ in orphan):.2f}㎡")
print(f"  P02 封账分级水平面 Σ=97.78㎡（ceiling_zones.md §1）")
