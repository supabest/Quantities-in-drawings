"""DXF 矢量提取器（Phase 5b · 增量 1：全图计数）。

设计决策（见 docs/QUANTITY_TAKEOFF_MASTER_ARCHITECTURE.md §12.7）：
  - 保持块完整：设备计数只遍历 modelspace 顶层 INSERT，按 (图层, 块名) 计数，
    **不在识别前打散（EXPLODE）图形**——打散会摧毁可数单位并污染图层分类。
  - 线长：顶层导线多段线（LWPOLYLINE / LINE / POLYLINE）按图层求和，
    弧段（bulge）按真实弧长计入，对齐 AutoCAD 测量口径。

单位：DXF 图纸单位为毫米（mm）；本模块返回**原始绘图单位**，
换算到米由调用方 /1000 处理（与手算表"长度/1000"口径一致）。
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, Tuple

import ezdxf

# 导线/配线类图层前缀（用于线长统计的默认过滤）
WIRE_LAYER_PREFIXES = ('WIRE', 'LIRE', 'CABLE', 'E-FIRE', 'XT_WIRE', 'PSS-WIRE')


# ── 几何长度（含 bulge 弧段）──
def _lwpolyline_length(e) -> float:
    """LWPOLYLINE 精确长度：直线段用弦长，bulge≠0 的段用弧长。"""
    pts = list(e.get_points('xyb'))  # [(x, y, bulge), ...]
    n = len(pts)
    if n < 2:
        return 0.0
    closed = bool(e.closed)
    total = 0.0
    segs = n if closed else n - 1
    for i in range(segs):
        x1, y1, b1 = pts[i]
        x2, y2, _ = pts[(i + 1) % n]
        d = math.hypot(x2 - x1, y2 - y1)
        if d == 0:
            continue
        # 每段的 bulge 取该段起点处的 bulge（末段闭合回起点用最后一点的 bulge）
        bulge = b1
        if abs(bulge) < 1e-10:
            total += d
        else:
            theta = 4.0 * math.atan(bulge)          # 包含角
            r = d / (2.0 * math.sin(abs(theta) / 2.0))
            total += abs(r * theta)
    return total


def _line_length(e) -> float:
    s, t = e.dxf.start, e.dxf.end
    return math.hypot(t.x - s.x, t.y - s.y)


def _polyline_length(e) -> float:
    """老式 POLYLINE（顶点为 VERTEX 子实体），仅取 2D 直连长度。"""
    vs = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices
          if v.dxftype() == 'VERTEX']
    if len(vs) < 2:
        return 0.0
    total = sum(math.dist(vs[i], vs[i + 1]) for i in range(len(vs) - 1))
    if e.is_closed and len(vs) > 2:
        total += math.dist(vs[-1], vs[0])
    return total


# ── 设备块计数 ──
def count_blocks(doc, modelspace: str = 'layout') -> Counter:
    """遍历 modelspace 顶层 INSERT，按 (图层, 块名) 计数。不打散块。

    Returns:
        Counter，键为 (layer, block_name)，值为出现次数。
    """
    msp = doc.modelspace() if modelspace == 'layout' else doc.modelspace()
    counter: Counter = Counter()
    for e in msp.query('INSERT'):
        counter[(e.dxf.layer, e.dxf.name)] += 1
    return counter


# ── 导线线长统计 ──
def measure_wire_lengths(doc, prefixes: Iterable[str] = WIRE_LAYER_PREFIXES,
                         unit_to_m: float = 1.0) -> Dict[str, float]:
    """按图层求和顶层导线长度（LWPOLYLINE / LINE / POLYLINE）。

    Args:
        prefixes: 计入的图层名前缀（默认导线类图层）。
        unit_to_m: 绘图单位→米的换算系数（mm 图纸用 1/1000）。

    Returns:
        {layer: 总长度（已乘 unit_to_m）}
    """
    prefixes = tuple(prefixes)
    msp = doc.modelspace()
    acc: Dict[str, float] = defaultdict(float)
    for e in msp:
        layer = e.dxf.layer
        if not layer.startswith(prefixes):
            continue
        t = e.dxftype()
        if t == 'LWPOLYLINE':
            acc[layer] += _lwpolyline_length(e)
        elif t == 'LINE':
            acc[layer] += _line_length(e)
        elif t == 'POLYLINE':
            acc[layer] += _polyline_length(e)
    return {k: v * unit_to_m for k, v in acc.items()}


# 绘图单位（mm）→ 米
layer_length_units = {'mm_to_m': 1.0 / 1000.0}


# ── 标题框区域切分（增量 2）──
# 平面图标题："23A层展示区 照明平面图" / "25层展示区 应急照明平面图" …
PLAN_TITLE_RE = re.compile(
    r'(\d+[A-Z]?)\s*层.*?(应急照明|配电|照明|插座|火灾报警|弱电)\s*平面图')


def _entity_point(e):
    """实体代表点：INSERT 用插入点，线/多段线用 bbox 中心。"""
    t = e.dxftype()
    try:
        if t == 'INSERT':
            p = e.dxf.insert
            return (p.x, p.y)
        if t == 'LWPOLYLINE':
            pts = [(pt[0], pt[1]) for pt in e.get_points('xy')]
        elif t == 'LINE':
            pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        elif t == 'POLYLINE':
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
        elif e.dxf.hasattr('insert'):
            p = e.dxf.insert
            return (p.x, p.y)
        else:
            return None
    except Exception:
        return None
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


def find_plan_frames(doc) -> list:
    """识别 TEL_TITLE 图层上的 A1 平面图框，读标题→区域标签。

    Returns:
        [{'bbox': (x0, y0, x1, y1), 'region': '23A照明'}, ...]（仅保留能定标签的平面框）
    """
    msp = doc.modelspace()
    raw = []
    for e in msp.query('LWPOLYLINE'):
        if e.dxf.layer != 'TEL_TITLE':
            continue
        pts = list(e.get_points('xy'))
        if len(pts) != 4 or not e.closed:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if 55000 < min(w, h) < 62000 and 78000 < max(w, h) < 86000:  # A1 幅面（横竖皆可）
            raw.append((min(xs), min(ys), max(xs), max(ys)))
    # 去重（外/内框中心相近）
    frames = []
    for f in raw:
        cx, cy = (f[0] + f[2]) / 2, (f[1] + f[3]) / 2
        if not any(abs(cx - (g['bbox'][0] + g['bbox'][2]) / 2) < 3000
                   and abs(cy - (g['bbox'][1] + g['bbox'][3]) / 2) < 3000 for g in frames):
            frames.append({'bbox': f, 'region': None})
    # 读标题文字定区域标签
    titles = []
    for t in msp.query('TEXT MTEXT'):
        try:
            s = (t.dxf.text if t.dxftype() == 'TEXT' else t.text) or ''
            p = t.dxf.insert
        except Exception:
            continue
        m = PLAN_TITLE_RE.search(s)
        if m:
            titles.append((p.x, p.y, f'{m.group(1)}{m.group(2)}'))
    for g in frames:
        x0, y0, x1, y1 = g['bbox']
        for tx, ty, label in titles:
            if x0 <= tx <= x1 and y0 <= ty <= y1:
                g['region'] = label
                break
    return [g for g in frames if g['region']]


def region_of(pt, frames) -> str:
    """代表点落在哪个图框→区域标签（无则 None）。"""
    if pt is None:
        return None
    x, y = pt
    for g in frames:
        x0, y0, x1, y1 = g['bbox']
        if x0 <= x <= x1 and y0 <= y <= y1:
            return g['region']
    return None


def count_blocks_by_region(doc, frames=None) -> Counter:
    """按 (区域, 图层, 块名) 计数顶层 INSERT。不打散块。"""
    frames = frames or find_plan_frames(doc)
    c = Counter()
    for e in doc.modelspace().query('INSERT'):
        r = region_of(_entity_point(e), frames)
        if r:
            c[(r, e.dxf.layer, e.dxf.name)] += 1
    return c


def measure_wire_lengths_by_region(doc, frames=None,
                                   prefixes: Iterable[str] = WIRE_LAYER_PREFIXES,
                                   unit_to_m: float = 1.0) -> Dict[Tuple[str, str], float]:
    """按 (区域, 图层) 求和顶层导线长度。"""
    frames = frames or find_plan_frames(doc)
    prefixes = tuple(prefixes)
    acc = defaultdict(float)
    for e in doc.modelspace():
        layer = e.dxf.layer
        if not layer.startswith(prefixes):
            continue
        r = region_of(_entity_point(e), frames)
        if not r:
            continue
        t = e.dxftype()
        if t == 'LWPOLYLINE':
            acc[(r, layer)] += _lwpolyline_length(e)
        elif t == 'LINE':
            acc[(r, layer)] += _line_length(e)
        elif t == 'POLYLINE':
            acc[(r, layer)] += _polyline_length(e)
    return {k: v * unit_to_m for k, v in acc.items()}


def load(path: str):
    """读取 DXF（自动处理编码/代理实体）。"""
    return ezdxf.readfile(path)


if __name__ == '__main__':
    import sys, os
    # 默认自检图 = 227 户型平面 DXF（工控项目已退出算量范围，2026-10-12）
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
    default = os.path.join(root, '装修图纸/227户型dxf/05.227户型平面图 （施工图纸） rev-33.dxf')
    doc = load(sys.argv[1] if len(sys.argv) > 1 else default)
    bc = count_blocks(doc)
    print(f'顶层 INSERT 组合数（图层,块名）: {len(bc)}，总块数 {sum(bc.values())}')
    wl = measure_wire_lengths(doc, unit_to_m=layer_length_units['mm_to_m'])
    print('按图层线长（米）:')
    for layer, L in sorted(wl.items(), key=lambda kv: -kv[1]):
        print(f'  {layer:24s} {L:12.2f} m')
