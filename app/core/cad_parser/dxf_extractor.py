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


def load(path: str):
    """读取 DXF（自动处理编码/代理实体）。"""
    return ezdxf.readfile(path)


if __name__ == '__main__':
    import sys
    doc = load(sys.argv[1] if len(sys.argv) > 1 else 'electrical.dxf')
    bc = count_blocks(doc)
    print(f'顶层 INSERT 组合数（图层,块名）: {len(bc)}，总块数 {sum(bc.values())}')
    wl = measure_wire_lengths(doc, unit_to_m=layer_length_units['mm_to_m'])
    print('按图层线长（米）:')
    for layer, L in sorted(wl.items(), key=lambda kv: -kv[1]):
        print(f'  {layer:24s} {L:12.2f} m')
