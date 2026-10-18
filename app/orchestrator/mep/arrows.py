# -*- coding: utf-8 -*-
"""app/orchestrator.mep.arrows — 引线**末端标记普查**（归属降级判据的证据源）。

法理(M5 §8.4/§8.5): 箭头是引线终点的唯一法理凭证。追踪器遇长直线会骑线直走
(§8.2 失效模式)，所以"追通了的端点"未必是引线真实终点。本模块在打印 PDF 的矢量
图元上普查三类末端标记，逐行量出**端点到最近标记的距离**，供算量表把
"无末端标记证据"的房间归属如实降为待核（只降状态，不动任何数量）。

三形态(判据原样沿用 M5d 一次性探针 _m5d_arrows.py，不得静默调参):
  F1 实心三角  = 填充 + 恰 3 条线段 + 长 1.5~7pt + 细长比<0.62；同时记形心与尖端
                 (实测引线端点贴合**形心**，只存尖端会把命中率打回 3%)
  F2 开口两笔  = 同顶点 2 根短斜棒(夹角 10~60°, 长 1.2~6pt) + 1 根 ≥6pt 的杆在角平分线反向
  F3 端点圆点  = 小填充圆/方(0.4~2.6pt, 宽高差<1.2) 且贴近某线段端点(<1.2pt)
先取主图区(房间面 ∪ 12pt 缓冲)以排除注记列/图例的文字笔画噪声。

恒等纪律: 工件记录所依据的 rows 文件 md5；rows 一旦重封账而本工件未重跑，
读取方(build_mep_workbook)直接断言失败，逼重跑普查 —— 不允许拿过期证据判新的账。

运行: venv/bin/python -m app.orchestrator.mep.arrows
产物: outputs/preview/vlm/mep/m5h_arrows_P08.json / m5h_arrows_P09.json
"""
import hashlib
import json
import math
import os
from collections import defaultdict

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

ARTIFACT_DIR = os.path.join('outputs', 'preview', 'vlm', 'mep')
PAGE = {'P08': 18, 'P09': 19}
ZONE_BUF_PT = 12.0        # 主图区 = 房间面并集外扩(与引线归属的墙装容差同口径)
HIT_TOL_PT = 1.5          # 端点贴住末端标记的容差(M5d 普查定案值)
TRI_SEGS = 3              # F1: 恰 3 条线段
TRI_MIN, TRI_MAX = 1.5, 7.0
TRI_SLIM = 0.62           # F1 细长比上限(min/max 边长)
BAR_MIN, BAR_MAX = 1.2, 6.0     # F2 斜棒长度
ANG_MIN, ANG_MAX = 10, 60       # F2 两棒夹角(度)
SHAFT_MIN = 6.0                 # F2 杆长下限
DOT_MIN, DOT_MAX = 0.4, 2.6     # F3 圆点尺寸
DOT_SEGHIT = 1.2                # F3 贴线段端点的容差
FORM_OF = {'tri': 'F1三角', 'tri_tip': 'F1三角尖端', 'oblique': 'F2开口两笔', 'dot': 'F3圆点'}


def _rows_pool(art):
    """P08 = M2 引线归属行 + M5f 补账行(与 build_mep_workbook 的合并池**同序**)；P09 单源。"""
    def load(n):
        with open(os.path.join(art, n), encoding='utf-8') as f:
            return json.load(f)
    r8 = load('m5_P08_rows.json')
    s8 = load('m5f_supplement_P08.json')['rows']
    return {'P08': r8 + s8, 'P09': load('m5_P09_rows.json')}, \
           {'P08': ['m5_P08_rows.json', 'm5f_supplement_P08.json'], 'P09': ['m5_P09_rows.json']}


def _segs_of(page):
    """(原始 paths, 全部线段[(a,b,L)]) —— 普查用**全宽**线段, 不限引线上线宽。"""
    dr = page.get_drawings()
    segs = []
    for p in dr:
        for d in p['items']:
            if d[0] == 'l':
                a, b = (d[1].x, d[1].y), (d[2].x, d[2].y)
                L = math.dist(a, b)
                if L > 0.05:
                    segs.append((a, b, L))
    return dr, segs


def plan_zone(index, buf=ZONE_BUF_PT):
    """主图区多边形 = 房间面并集外扩 buf。多边形来源与归属判据同源(registration)。"""
    polys = [Polygon(r['poly_pt']) for r in index.rooms]
    return unary_union(polys).buffer(buf)


def _ang_between(v, a, b):
    t1 = math.atan2(a[1] - v[1], a[0] - v[0])
    t2 = math.atan2(b[1] - v[1], b[0] - v[0])
    d = abs(math.degrees(t1 - t2)) % 360
    return min(d, 360 - d)


def census(page, zone):
    """→ (F1, F2, F3) 三形态标记 [(kind, (x, y), size)]，未去重。"""
    dr, segs = _segs_of(page)
    F1, F2, F3 = [], [], []
    # ── F1 实心三角(形心与尖端都留) ──
    for p in dr:
        if p['fill'] is None:
            continue
        ls = [d for d in p['items'] if d[0] == 'l']
        if len(ls) != TRI_SEGS:
            continue
        pts = [(d[1].x, d[1].y) for d in ls] + [(ls[-1][2].x, ls[-1][2].y)]
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        mx = max(w, h)
        if TRI_MIN < mx < TRI_MAX and min(w, h) / mx < TRI_SLIM:
            c = (sum(xs) / 4, sum(ys) / 4)
            tip = max(pts, key=lambda q: math.dist(q, c))
            if zone.contains(Point(c)):
                F1.append(('tri', c, round(mx, 2)))
                F1.append(('tri_tip', tip, round(mx, 2)))
    # ── F2 开口两笔(带杆) ──
    byv = defaultdict(list)
    for a, b, L in segs:
        for v, o in ((a, b), (b, a)):
            byv[(round(v[0], 2), round(v[1], 2))].append((o, L))
    for (vx, vy), lst in byv.items():
        if len(lst) < 3:
            continue
        v = (vx, vy)
        bars = [(o, L) for o, L in lst if BAR_MIN <= L <= BAR_MAX]
        shafts = [(o, L) for o, L in lst if L >= SHAFT_MIN]
        for i in range(len(bars)):
            for j in range(i + 1, len(bars)):
                th = _ang_between(v, bars[i][0], bars[j][0])
                if not (ANG_MIN <= th <= ANG_MAX):
                    continue
                bis = (bars[i][0][0] - v[0] + bars[j][0][0] - v[0],
                       bars[i][0][1] - v[1] + bars[j][0][1] - v[1])
                n = math.hypot(*bis) or 1e-9
                for o, L in shafts:                       # 杆在角平分线反向
                    nn = math.hypot(o[0] - v[0], o[1] - v[1]) or 1e-9
                    d = ((o[0] - v[0]) / nn, (o[1] - v[1]) / nn)
                    if (d[0] * bis[0] + d[1] * bis[1]) / n < -math.cos(math.radians(40)):
                        if zone.contains(Point(v)):
                            F2.append(('oblique', v, round(th)))
                        break
                else:
                    continue
                break
    # ── F3 端点圆点 ──
    eps = set()
    for a, b, L in segs:
        eps.add((round(a[0], 1), round(a[1], 1)))
        eps.add((round(b[0], 1), round(b[1], 1)))
    for p in dr:
        if p['fill'] is None:
            continue
        r = p['rect']
        w, h = r.width, r.height
        if not (DOT_MIN < max(w, h) < DOT_MAX and abs(w - h) < 1.2):
            continue
        c = ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        if any(math.dist(c, (x, y)) < DOT_SEGHIT for x, y in eps) and zone.contains(Point(c)):
            F3.append(('dot', c, round(max(w, h), 2)))
    return F1, F2, F3


def dedupe(marks, tol=1.2):
    out = []
    for m in sorted(marks, key=lambda q: (round(q[1][0], 1), round(q[1][1], 1))):
        if out and math.dist(out[-1][1], m[1]) < tol:
            continue
        out.append(m)
    return out


def nearest(marks, pt):
    """→ (距离pt, 形态名)。空标记集 → (inf, None)。"""
    best, form = float('inf'), None
    for k, p, _s in marks:
        d = math.dist(pt, p)
        if d < best:
            best, form = d, k
    return round(best, 2), FORM_OF.get(form, form)


def survey(doc, index, art=ARTIFACT_DIR, tol=HIT_TOL_PT, write=True):
    """逐页普查 + 逐端点量距 → 写 m5h_arrows_{tag}.json。返回 {tag: 工件dict}。"""
    pool, prov = _rows_pool(art)
    zone = plan_zone(index)
    out = {}
    for tag, pi in PAGE.items():
        F1, F2, F3 = census(doc[pi], zone)
        raw = {'F1': F1, 'F2': F2, 'F3': F3}
        marks = dedupe(F1 + F2 + F3)
        rows = []
        for r in pool[tag]:
            e = r.get('endpoint')
            rec = {'kind': r['kind'], 'anchor': r['anchor'],
                   'endpoint': e, 'room': r['room'], 'n': r['n']}
            if e:
                d, form = nearest(marks, tuple(e))
                rec.update(dist_pt=d, form=form, hit=bool(d < tol))
            rows.append(rec)
        hits = sum(1 for x in rows if x.get('hit'))
        placed = [x for x in rows if x.get('endpoint') and x['room'] != '未能归属']
        md5 = {f: hashlib.md5(open(os.path.join(art, f), 'rb').read()).hexdigest() for f in prov[tag]}
        art_obj = {
            'meta': {
                'generated_by': 'app/orchestrator/mep/arrows.py (python -m app.orchestrator.mep.arrows)',
                'page_index': pi, 'zone_buf_pt': ZONE_BUF_PT, 'hit_tol_pt': tol,
                'forms': {k: len(dedupe(v)) for k, v in raw.items()},
                'n_triangles': sum(1 for k, _p, _s in dedupe(F1) if k == 'tri'),
                'n_marks': len(marks),
                'rows_md5': md5, 'rows_sources': prov[tag],
                'note': f'{tag}: 末端标记 {len(marks)} 个(其中实心三角 {len([1 for k,_p,_s in dedupe(F1) if k == "tri"])} 个); '
                        f'端点命中(<{tol}pt) {hits}/{len(rows)} 锚; 已落域且有端点 {len(placed)} 行中命中 '
                        f'{sum(1 for x in placed if x.get("hit"))} 行。'
                        f'未命中≠归属一定错, 而是"无末端标记可证"(§8.2 骑线行与检测器漏检不可分)。',
            },
            'marks': [[k, round(p[0], 2), round(p[1], 2), s] for k, p, s in marks],
            'rows': rows,
        }
        out[tag] = art_obj
        if write:
            p = os.path.join(art, f'm5h_arrows_{tag}.json')
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(art_obj, f, ensure_ascii=False, indent=1)
            print(f'{tag}: 标记{len(marks)} 三角{art_obj["meta"]["n_triangles"]} '
                  f'命中{hits}/{len(rows)} → {p}')
    return out


def main(art=ARTIFACT_DIR):
    import fitz
    from app.orchestrator.mep.registration import DEFAULT_PDF, load_index
    index, _calib = load_index(os.path.join(art, 'm2_attribution.json'))
    doc = fitz.open(DEFAULT_PDF)
    return survey(doc, index, art=art)


if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.getcwd())
    main()
