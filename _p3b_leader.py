# P02 标高符号⊕ → 引线(leader)追踪 → 目标域归属（确定性程序化）
# 方法：从⊕中心出发找伸出线段，共线串链至尖端；尖端沿方向探测首个白像素所在洪水域lid；
#       无引线者按符号邻域环多数lid直判。输出 p02_leader_attribution.json + 验证overlay。
import fitz, numpy as np, cv2, json, math, os
from PIL import Image, ImageDraw

PT2MM = 0.352778 * 100
PDF = "装修图纸/227户型dxf/广州珠实金融城住宅项目--227户型施工图纸.pdf"
OUT = "outputs/preview/vlm/elev"
DPI = 200; sc = DPI / 72.0; PX2M = 1.0 / sc * PT2MM / 1000

doc = fitz.open(PDF); page = doc[12]
cands = json.load(open(f"{OUT}/idx12_cands.json"))["cands"]
fl = json.load(open(f"{OUT}/p02_zones_flood.json"))["rows"]
vmap = {r["m"]: r.get("v") for r in fl}

# 洪水填充标签矩阵（与 T2 完全同参）
pm = page.get_pixmap(dpi=DPI)
img = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, pm.n)[:, :, :3].copy()
gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
blk = cv2.dilate((gray < 128).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2)
n, lab, stats, cent = cv2.connectedComponentsWithStats((1 - blk).astype(np.uint8))

# 线段库：仅细线（引线 w≈0.24；符号十字线 0.36/墙线更粗均被排除）
segs=[]; arrows=[]
for dr in page.get_drawings():
    w=dr.get("width") or 0
    if dr.get("type")=="s" and dr.get("fill") is None and 0.18<=w<=0.30:
        for it in dr["items"]:
            if it[0]=="l":
                p,q=it[1],it[2]
                L=math.dist((p.x,p.y),(q.x,q.y))
                if L>1: segs.append((p.x,p.y,q.x,q.y,L))
    # 箭头候选：小填充三角
    if dr.get("fill") is not None and dr.get("type") in ("f","fs"):
        r=dr["rect"]
        if 1.5<=r.width<=7 and 1.5<=r.height<=7:
            arrows.append(((r.x0+r.x1)/2,(r.y0+r.y1)/2))
segs=np.array(segs); arrows=np.array(arrows)
print("thin segs:",len(segs),"arrow cands:",len(arrows))

def probe_lid(x, y, max_r=10, min_m2=0.02):
    """点邻域最近白像素lid；排除背景与碎面(<min_m2)"""
    xi, yi = int(x * sc), int(y * sc)
    r1 = int(max_r * sc)
    y0s, x0s = max(0, yi - r1), max(0, xi - r1)
    win = blk[y0s:yi + r1, x0s:xi + r1]
    lbw = lab[y0s:yi + r1, x0s:xi + r1]
    ys, xs = np.where(win == 0)
    if len(ys) == 0: return None
    d2 = (ys - (yi - y0s)) ** 2 + (xs - (xi - x0s)) ** 2
    order = np.argsort(d2)
    for k in order[:400]:
        lid = int(lbw[ys[k], xs[k]])
        a = stats[lid, cv2.CC_STAT_AREA] * PX2M * PX2M
        if 0.02 <= a <= 100: return lid
        if a > 100: return None
    return None

def probe_ring(cx, cy, rad=12.0):
    """符号环探：8方向半径rad处取白像素，选最大面积有效域"""
    cands = []
    for adeg in range(0, 360, 45):
        x = cx + rad * math.cos(math.radians(adeg)); y = cy + rad * math.sin(math.radians(adeg))
        lid = probe_lid(x, y, max_r=6)
        if lid is not None: cands.append(lid)
    if not cands: return None
    return max(set(cands), key=lambda l: (cands.count(l), stats[l, cv2.CC_STAT_AREA]))

def v_arrow(tipx, tipy, ang):
    """开口V箭头：尖端附近1~4pt短斜线对"""
    hit = 0
    for x0, y0, x1, y1, L in segs:
        if L < 0.8 or L > 4.5: continue
        for p, q in (((x0, y0), (x1, y1)), ((x1, y1), (x0, y0))):
            if math.dist(p, (tipx, tipy)) > 5: continue
            sa = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
            da = abs((sa - ang + 90) % 180 - 90)
            if 8 <= da <= 45: hit += 1
    return hit >= 2

results = []
for i, (cx, cy) in enumerate(cands):
    # 1) 找伸出段：一端近符号(1.5~25pt)、中点不在符号上、朝外
    stems = []
    for x0, y0, x1, y1, L in segs:
        for a, b in (((x0, y0), (x1, y1)), ((x1, y1), (x0, y0))):
            da = math.dist(a, (cx, cy))
            if da <= 8.0 and math.dist(b, (cx, cy)) > da:   # 引线始于符号边缘≤8pt
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                if math.dist(mid, (cx, cy)) < 3: continue  # 十字线本身
                ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
                stems.append((da, a, b, ang, L))
    best = None
    for da, a, b, ang, L in sorted(stems):
        if L > 200: continue
        # 2) 共线串链向外延伸（仅细线，总长封顶 250pt）
        chain = [(a, b)]; cur = b; ca = ang; total = L
        while total < 250:
            nx = None
            for x0, y0, x1, y1, l2 in segs:
                for p, q, d2 in (((x0, y0), (x1, y1), 0), ((x1, y1), (x0, y0), 1)):
                    g = math.dist(p, cur)
                    if g < 0.4 or g > 8: continue
                    da2 = min(abs(math.degrees(math.atan2(q[1]-p[1], q[0]-p[0])) - ca + 90) % 180 - 90, 180)
                    if abs(da2) > 2.5: continue
                    # 共线：p 到过 cur 方向线的垂距
                    ux, uy = math.cos(math.radians(ca)), math.sin(math.radians(ca))
                    perp = abs((p[0]-cur[0]) * uy - (p[1]-cur[1]) * ux)
                    if perp > 0.8: continue
                    if nx is None or g < nx[0]: nx = (g, q, math.degrees(math.atan2(q[1]-p[1], q[0]-p[0])), l2)
            if nx is None: break
            chain.append((cur, nx[1])); cur = nx[1]; ca = nx[2]; total += nx[0] + nx[3]
        if best is None or total > best[4]:
            best = (a, chain[0][0], cur, ca, total)
    # 3) 归属：引线硬确认 = 尖端V箭头 或 尖端落在有效域；否则回落环探
    mode = "direct"; tlid = None; harrow = False; ll = 0.0; tipxy = (cx, cy)
    if best and 8 <= best[4] <= 250:
        tipx, tipy = best[2]
        harrow = v_arrow(tipx, tipy, best[3])
        lid = probe_lid(tipx, tipy)
        if lid is None:
            ux, uy = math.cos(math.radians(best[3])), math.sin(math.radians(best[3]))
            for off in (2, 4, 8, 12):
                lid = probe_lid(tipx + ux * off, tipy + uy * off)
                if lid is not None: break
        if harrow or lid is not None:
            mode, tlid = "leader", lid
            ll, tipxy = best[4], (tipx, tipy)
    if mode == "direct":
        harrow = False
        tlid = probe_ring(cx, cy)
    zl = None; zv = None; ta = None
    if tlid is not None:
        ta = round(stats[tlid, cv2.CC_STAT_AREA] * PX2M * PX2M, 2)
        for r in fl:
            if r.get("lid") == tlid and r.get("state") == "closed":
                zl = r["zone_m2"]; zv = r["v"]; break
    results.append(dict(m=i, xy=[round(cx,1),round(cy,1)], v=vmap.get(i),
                        mode=mode, arrow=harrow, leader_len=round(ll,1),
                        tip=[round(tipxy[0],1),round(tipxy[1],1)],
                        target_lid=tlid, target_area_m2=ta, target_zone_m2=zl, target_zone_v=zv))

json.dump(results, open(f"{OUT}/p02_leader_attribution.json", "w"), ensure_ascii=False, indent=1)
for r in results:
    print(f"⊕{r['m']:2d} v={r['v']} {r['mode']:6s} arr={int(r['arrow'])} L={r['leader_len']:5.1f}pt tip={r['tip']} → lid={r['target_lid']} A={r['target_area_m2']}㎡ 域标高={r['target_zone_v']}")

# 4) overlay 验证图
vis = img.copy()
for r in results:
    x, y = int(r["xy"][0] * sc), int(r["xy"][1] * sc)
    tx, ty = int(r["tip"][0] * sc), int(r["tip"][1] * sc)
    cv2.arrowedLine(vis, (x, y), (tx, ty), (255, 0, 0), 3, tipLength=0.15)
    lid = r["target_lid"]
    if lid is not None:
        m = (lab == lid)
        for c in range(3):
            ch = vis[:, :, c].astype(float)
            vis[:, :, c] = np.where(m, ch * 0.5 + [0, 255, 0][c] * 0.5, ch).astype(np.uint8)
    cv2.putText(vis, str(r["m"]), (tx + 6, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
Image.fromarray(vis).save(f"{OUT}/review/p02_leader_overlay.png")
print("overlay saved")
