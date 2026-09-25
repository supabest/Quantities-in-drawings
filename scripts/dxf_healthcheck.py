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


def _iter_line_segments(msp, layers):
    """yield (x1,y1,x2,y2) 单位 m，仅取指定图层上的 LINE / LWPOLYLINE。"""
    for e in msp:
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

    # 3) 关键墙层
    wall_like = [l for l in ent_by_layer
                 if re.search(r'(S-WALL|WALL|墙)', l, re.I)]
    print(f'\n[3] 墙相关图层（S-WALL/WALL/墙）：')
    if not wall_like:
        print('    [警告] 未找到任何墙层')
    # 按层统计线长/线数
    len_by_layer = defaultdict(float)
    cnt_by_layer = defaultdict(int)
    for l in wall_like:
        for (x1, y1, x2, y2) in _iter_line_segments(msp, {l}):
            len_by_layer[l] += math.hypot(x2 - x1, y2 - y1)
            cnt_by_layer[l] += 1
    total_wall_ents = sum(ent_by_layer[l] for l in wall_like)
    for l in sorted(wall_like, key=lambda x: -len_by_layer[x]):
        flag = '  <-- 空壳!' if ent_by_layer[l] < WALL_EMPTY_THRESHOLD else ''
        print(f'    实体{ent_by_layer[l]:5d} 线{cnt_by_layer[l]:4d} '
              f'总长{len_by_layer[l]:7.1f}m  {l}{flag}')
    print(f'    墙层实体合计 {total_wall_ents} 个')

    # 4) 围合体检
    print(f'\n[4] 围合体检（墙层端点吸附 5mm 后 polygonize）：')
    wall_segs = list(_iter_line_segments(msp, set(wall_like) or None))
    nroom, rsum = _room_faces(wall_segs) if wall_segs else (0, 0.0)
    print(f'    房间尺度(5-60㎡)闭合面：{nroom} 个，面积和 {rsum:.1f}㎡')

    # 5) 判定
    print('\n' + '=' * 68)
    print('判定')
    print('=' * 68)
    # 结构墙层（S-WALL 或 $0$ 绑定壳）是否非空——区别于装修团队自绘的裸 WALL
    struct_layers = [l for l in wall_like
                     if ('S-WALL' in l.upper() or '$' in l)]
    struct_ents = sum(ent_by_layer[l] for l in struct_layers)
    if nroom >= 3:
        print('  [PASS] 墙层可围合出房间，DXF 几何完整，可进入面积对账。')
        return 0
    if shell_layers and struct_layers and struct_ents < WALL_EMPTY_THRESHOLD:
        print(f'  [FAIL] 疑似 XREF 墙几何丢失：存在 $0$ 壳层但结构墙层仅 {struct_ents} 实体')
        print('         （装修裸 WALL 非空属正常，建筑墙在参照里），且围合房间数不足。')
        print('         请在 AutoCAD 内 XREF→Bind 绑定所有外部参照后重导 DXF（见 §12.7.9）。')
        return 2
    if shell_layers and nroom < 3:
        print('  [FAIL] 存在 $0$ 外部参照壳层但墙未能围合房间，疑似参照几何未嵌入。')
        print('         请在 AutoCAD 内 XREF→Bind 绑定所有外部参照后重导 DXF（见 §12.7.9）。')
        return 2
    if total_wall_ents < WALL_EMPTY_THRESHOLD:
        print('  [WARN] 墙层实体过少，无法围合房间。请核查原图墙是否在参照/其他图层。')
        return 3
    print('  [WARN] 墙层非空但围合失败（可能门洞断开/坐标不闭合），需人工核查画法。')
    return 3


if __name__ == '__main__':
    sys.exit(main())
