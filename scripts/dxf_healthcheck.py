#!/usr/bin/env python3
"""DWG→DXF 转换自检：判断导出的 DXF 是否可用（尤其 XREF 墙是否丢失）。

背景（§12.7.9）：227 户型平面用 ODA 从 DWG 转 DXF 后，建筑专业墙所在的
外部参照（XREF，如 `1#` 号楼的 S-WALL 层）**只残留空图层壳、几何未嵌入**，
导致"从墙线围合房间"恒失败——但用户在 AutoCAD 里肉眼看到的墙是闭合的
（软件实时加载 XREF 渲染）。本脚本把"每次导完先自查"固化成一条命令。

检查项：
  1. 文件头版本、实体/图层规模
  2. XREF 状态：is_xref 块、`$0$` 绑定壳层的实体数（壳在但空 = 几何丢失）
  3. 关键墙层（S-WALL / WALL / 墙）实体数与总线长
  4. 围合体检：对墙层做端点吸附 polygonize，看能否得到房间尺度(5-60㎡)闭合面
  5. 判定：给出「可用 / 疑似XREF丢失需重导 / 需人工核查」结论

用法：
  venv/bin/python scripts/dxf_healthcheck.py [文件.dxf]
  # 不带参数时默认检查 227 户型平面图

退出码：0=可用；2=疑似XREF丢失/墙层空壳；3=围合失败需核查；4=文件读取失败。
"""
from __future__ import annotations

import os
import re
import sys
import math
from collections import defaultdict

import ezdxf
from shapely.geometry import LineString
from shapely.ops import unary_union, polygonize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DXF = os.path.join(ROOT, '装修图纸/227户型dxf/05.227户型平面图 （施工图纸） rev-33.dxf')

MIN_ROOM_M2, MAX_ROOM_M2 = 5.0, 60.0
# 墙层"非空"的最低实体数门槛：低于此值判为疑似丢失
WALL_EMPTY_THRESHOLD = 20


def _iter_entities(msp):
    """遍历模型空间实体, 并递归展开 INSERT 块内部几何。

    关键修正: 建筑墙常藏在块(含已绑定的 XREF 块)内部, 只扫顶层会漏。
    virtual_entities() 会应用 INSERT 变换, 给出世界坐标下的真实几何。
    """
    for e in msp:
        yield e
        if e.dxftype() == 'INSERT':
            try:
                for ve in e.virtual_entities():
                    yield ve
            except Exception:
                continue


def _iter_line_segments(msp, layers):
    """yield (x1,y1,x2,y2) 单位 m，取指定图层(含块内展开)的 LINE / LWPOLYLINE。"""
    for e in _iter_entities(msp):
        if layers is not None and e.dxf.layer not in layers:
            continue
        try:
            if e.dxftype() == 'LINE':
                yield (e.dxf.start.x / 1000, e.dxf.start.y / 1000,
                       e.dxf.end.x / 1000, e.dxf.end.y / 1000)
            elif e.dxftype() == 'LWPOLYLINE':
                pts = [(p[0] / 1000, p[1] / 1000) for p in e.get_points('xy')]
                for i in range(len(pts) - 1):
                    yield (*pts[i], *pts[i + 1])
                if e.closed and len(pts) > 2:
                    yield (*pts[-1], *pts[0])
        except Exception:
            continue


def _room_faces(segs, snap=0.005):
    """把线段端点吸附到 snap 网格后 polygonize，返回房间尺度闭合面数与面积。"""
    def sn(p):
        return (round(p[0] / snap) * snap, round(p[1] / snap) * snap)
    lines = []
    for x1, y1, x2, y2 in segs:
        a, b = sn((x1, y1)), sn((x2, y2))
        if a != b:
            lines.append(LineString([a, b]))
    if not lines:
        return 0, 0.0
    faces = list(polygonize(unary_union(lines)))
    rooms = [f.area for f in faces if MIN_ROOM_M2 <= f.area <= MAX_ROOM_M2]
    return len(rooms), sum(rooms)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DXF
    print('=' * 68)
    print(f'DXF 转换自检：{os.path.basename(path)}')
    print('=' * 68)
    if not os.path.exists(path):
        print(f'[错误] 文件不存在：{path}')
        return 4
    try:
        doc = ezdxf.readfile(path)
    except Exception as ex:
        print(f'[错误] 读取失败（若是 DWG 请先转 DXF）：{ex}')
        return 4
    msp = doc.modelspace()

    # 1) 规模
    ents = list(msp)
    try:
        ver = doc.header.get('$ACADVER', '?')
    except Exception:
        ver = '?'
    print(f'\n[1] 版本 {ver} | '
          f'模型空间实体 {len(ents)} | 图层 {len(doc.layers)}')

    # 图层 → 实体计数（全类型）
    ent_by_layer = defaultdict(int)
    for e in ents:
        ent_by_layer[e.dxf.layer] += 1

    # 2) XREF 状态
    xref_blocks = 0
    try:
        for b in doc.blocks:
            if getattr(b, 'is_xref', False):
                xref_blocks += 1
    except Exception:
        pass
    shell_layers = {l: n for l, n in ent_by_layer.items() if '$' in l}
    print(f'\n[2] XREF 状态')
    print(f'    is_xref 块：{xref_blocks} 个（0 且存在 $0$ 壳层 = ODA 已拍平）')
    if shell_layers:
        print(f'    $0$ 绑定壳层 {len(shell_layers)} 个，实体数：')
        for l, n in sorted(shell_layers.items(), key=lambda x: -x[1])[:12]:
            print(f'      {n:5d}  {l}')
    else:
        print('    未发现 $0$ 壳层')

    # 3) 关键墙层（含块内展开，因为建筑墙常藏在块里）
    wall_like = sorted({e.dxf.layer for e in _iter_entities(msp)
                        if re.search(r'(S-WALL|WALL|墙)', e.dxf.layer, re.I)})
    print(f'\n[3] 墙相关图层（S-WALL/WALL/墙，含块内展开）：')
    if not wall_like:
        print('    [警告] 未找到任何墙层')
    # 按层统计展开后的线长/线数
    len_by_layer = defaultdict(float)
    cnt_by_layer = defaultdict(int)
    for l in wall_like:
        for (x1, y1, x2, y2) in _iter_line_segments(msp, {l}):
            len_by_layer[l] += math.hypot(x2 - x1, y2 - y1)
            cnt_by_layer[l] += 1
    total_wall_len = sum(len_by_layer.values())
    total_wall_lines = sum(cnt_by_layer.values())
    for l in sorted(wall_like, key=lambda x: -len_by_layer[x]):
        flag = '  <-- 空!' if cnt_by_layer[l] == 0 else ''
        print(f'    线{cnt_by_layer[l]:5d} 总长{len_by_layer[l]:7.1f}m  {l}{flag}')
    print(f'    墙层展开后合计：{total_wall_lines} 段 / {total_wall_len:.1f}m')

    # 4) 围合体检
    print(f'\n[4] 围合体检（墙层含块展开、端点吸附 5mm 后 polygonize）：')
    wall_segs = list(_iter_line_segments(msp, set(wall_like) or None))
    nroom, rsum = _room_faces(wall_segs) if wall_segs else (0, 0.0)
    print(f'    房间尺度(5-60㎡)闭合面：{nroom} 个，面积和 {rsum:.1f}㎡')

    # 5) 判定
    print('\n' + '=' * 68)
    print('判定')
    print('=' * 68)
    if nroom >= 3:
        print('  [PASS] 墙层可围合出房间，DXF 几何完整，可进入面积对账。')
        return 0
    if total_wall_len < 50:
        print(f'  [FAIL] 墙几何缺失/过少（展开块后仅 {total_wall_len:.0f}m 墙线）。')
        print('         若原图墙在外部参照里，请在 AutoCAD 内 XREF→Bind 后重导 DXF。')
        return 2
    # 墙线充足但围不出房间 = 双线墙/门洞算法问题，非数据问题
    print(f'  [WARN] 墙几何完整（展开块后 {total_wall_len:.0f}m 墙线），但简单围合未成功。')
    print('         这是双线墙+门洞断开的算法难题（非数据丢失，无需重导）。')
    print('         需专门算法：沿墙中心线追踪 或 用门块(DorLib2D)封口后再围合。')
    return 3


if __name__ == '__main__':
    sys.exit(main())
