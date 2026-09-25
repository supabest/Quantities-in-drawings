#!/usr/bin/env python3
"""Phase 5b 装修支路 · 面积对账（增量 5b-D2a）。

用途：对 227 户型平面 DXF 提取 floor / CEILING 图层净面积，与
`金融城227户型样板房工程量清单.xlsx` 的地面/天棚 m² 项做对照。

诚实声明（重要）：
  本脚本输出的是**图层级净面积**（几何口径），与 BOQ 的**分项工程量**
  （材料/房间/增减项口径）**不是同一量纲**。地面饰面按材料被切成多块、
  房间含多个饰面区、清单含"每增减"正负对——因此逐房间/逐材料对账需要先
  从墙体重建房间区域（增量 5b-D2b），本脚本仅提供**量级诊断**，不声明达标。

用法：
  venv/bin/python scripts/phase5b_decoration_recon.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ezdxf  # noqa: E402
import openpyxl  # noqa: E402

from app.core.cad_parser.decoration import net_area_by_layer, room_polygons  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DXF = os.path.join(ROOT, '装修图纸/227户型dxf/05.227户型平面图 （施工图纸） rev-33.dxf')
BOQ = os.path.join(ROOT, '装修图纸/227户型dxf/金融城227户型样板房工程量清单.xlsx')
SHEET = 'ZS02-A-精装修工程-227户型清单'


def boq_m2_items(path: str, sheet: str):
    """抽取带 m² 工程量的清单项：返回 [(分部, 编码, 名称, 工程量)]。"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]
    sec = ''
    out = []
    for row in ws.iter_rows(min_col=1, max_col=7, values_only=True):
        r = [('' if c is None else str(c).strip()) for c in row]
        if r[1] in ('一', '二', '三', '四', '五', '六', '七', '八', '九', '十') and not r[4]:
            sec = r[2]
            continue
        if r[4].lower().replace(' ', '') in ('m2', '㎡') and r[6]:
            try:
                out.append((sec, r[1], r[2], float(r[6])))
            except ValueError:
                pass
    return out


def main() -> None:
    doc = ezdxf.readfile(DXF)
    print('=' * 68)
    print('装修面积提取（几何净面积，非对账达标声明）')
    print('=' * 68)
    for layer in ('floor', 'CEILING'):
        net = net_area_by_layer(doc, layer)
        rooms = room_polygons(doc, layer)
        print(f'[{layer}] 闭合多边形 {net["count"]} 个 | '
              f'naive求和 {net["naive"]}㎡ | union净面积 {net["union"]}㎡ | '
              f'房间级(>1㎡) {len(rooms)} 个')

    items = boq_m2_items(BOQ, SHEET)
    from collections import defaultdict
    by_sec = defaultdict(lambda: [0, 0.0])
    for sec, _code, _name, qty in items:
        by_sec[sec][0] += 1
        by_sec[sec][1] += qty
    print('\nBOQ m² 项按分部（含增减正负，naive 求和仅供量级参考）:')
    for sec, (n, tot) in by_sec.items():
        print(f'  {sec}: {n} 项, 代数和 {tot:.1f}㎡')
    print(f'\n  m² 清单项合计 {len(items)} 项')
    print('\n注：union 净面积是几何口径，BOQ 是材料/房间/增减口径，'
          '逐项目标对账需增量 5b-D2b 从墙体重建房间区域。')


if __name__ == '__main__':
    main()
