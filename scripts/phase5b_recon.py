"""Phase 5b 增量1 对账：DXF 全图总量 vs 手算表分层统计（跨区域求和）。

用法:
    venv/bin/python scripts/phase5b_recon.py

口径说明:
  - 提取器统计的是**全图**（所有 INSERT / 所有导线图层）。
  - 手算表 `分层统计` 是按标题框**分区域**逐行列出的；本脚本把同一
    (图层,块名) 的跨区域行求和，得到"手算表覆盖到的总量"。
  - 二者差异来源 = 手算表未纳入的区域（其它楼层/图框）。增量2 做区域
    切分后，逐区域行应能精确对上。
"""
import os
import sys
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import openpyxl  # noqa: E402
from app.core.cad_parser import dxf_extractor as dx  # noqa: E402

DXF = os.path.join(BASE, 'electrical.dxf')
XLSX = os.path.join(BASE, 'outputs', '营销中心售楼部电气工程量计算表.xlsx')


def load_excel_layerstats(path):
    """解析 `分层统计`：返回 (块计数 {(layer,block):count}, 线长 {layer:meters})。"""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb['分层统计']
    block_counts = Counter()
    wire_lengths = defaultdict(float)
    for r in range(2, ws.max_row + 1):
        region = ws.cell(r, 1).value
        cat = ws.cell(r, 2).value
        layer = ws.cell(r, 3).value
        key = ws.cell(r, 4).value
        qty = ws.cell(r, 5).value
        if not layer or qty in (None, ''):
            continue
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            continue
        if cat and '块' in str(cat):
            block_counts[(str(layer).strip(), str(key or '').strip())] += qty
        elif cat and '线' in str(cat):
            wire_lengths[str(layer).strip()] += qty
    return block_counts, wire_lengths


def load_excel_region(path):
    """解析 `分层统计` 逐区域行。返回 (块计数 {(region,block):n}, 线长 {(region,layer):m})。"""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb['分层统计']
    blocks, wires = Counter(), {}
    for r in range(2, ws.max_row + 1):
        region = ws.cell(r, 1).value
        cat = ws.cell(r, 2).value
        layer = ws.cell(r, 3).value
        key = ws.cell(r, 4).value
        qty = ws.cell(r, 5).value
        if not region or qty in (None, ''):
            continue
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            continue
        region = str(region).strip()
        if cat and '块' in str(cat):
            blocks[(region, str(key or '').strip())] += qty
        elif cat and '线' in str(cat):
            wires[(region, str(layer).strip())] = qty
    return blocks, wires


def recon_by_region():
    """增量 2 验收：标题框区域切分 vs 手算表逐区域行。"""
    doc = dx.load(DXF)
    frames = dx.find_plan_frames(doc)
    mine_regions = sorted(f['region'] for f in frames)
    xl_blocks, xl_wires = load_excel_region(XLSX)
    xl_regions = sorted({reg for reg, _ in xl_blocks} | {reg for reg, _ in xl_wires})

    print('=' * 72)
    print('增量 2 · 标题框区域切分对账')
    print('=' * 72)
    print(f'切出平面框 {len(frames)} 个: {mine_regions}')
    print(f'手算表区域 {len(xl_regions)} 个; 区域集合一致: {set(mine_regions) == set(xl_regions)}')

    # ── 逐块跨区域总量（消除人工跨系统子分配差异后的公平比对）──
    my_by_block = Counter()
    for (reg, layer, bn), n in dx.count_blocks_by_region(doc, frames).items():
        my_by_block[bn] += n
    xl_by_block = Counter()
    for (reg, bn), n in xl_blocks.items():
        xl_by_block[bn] += n
    tot = ok = 0
    print('\n逐块跨区域总量（xl=手算表, mine=我仅平面框）:')
    for bn, xn in xl_by_block.most_common(50):
        dn = my_by_block.get(bn, 0)
        dev = (dn - xn) / xn * 100 if xn else float('nan')
        tot += 1
        ok += abs(dev) < 2
        flag = '✓' if abs(dev) < 2 else ' '
        print(f'  {flag} {bn[:32]:32s} xl={xn:5.0f} mine={dn:5.0f} Δ={dev:+6.1f}%')
    print(f'\n逐块总量命中<2%: {ok}/{tot}')
    print('注: 手算表块总量 << 平面框内总 INSERT，因人工只 tally 映射到清单的 ~69 种块，'
          '其余装饰/参照/图例块未计——留待增量 3 的图层/块名→清单映射过滤。')


def main():
    doc = dx.load(DXF)
    my_blocks = dx.count_blocks(doc)
    my_wires = dx.measure_wire_lengths(doc, unit_to_m=dx.layer_length_units['mm_to_m'])

    xl_blocks, xl_wires = load_excel_layerstats(XLSX)

    # ── 块计数对账（按块名汇总，忽略图层差异做粗对；再按(layer,块名)细看）──
    print('=' * 72)
    print('块计数对账：手算表(跨区域求和) vs DXF 全图')
    print('=' * 72)
    # 按块名汇总双方（图层名可能有细微差异，先按块名对齐）
    my_by_block = Counter()
    for (layer, bn), n in my_blocks.items():
        my_by_block[bn] += n
    xl_by_block = Counter()
    for (layer, bn), n in xl_blocks.items():
        xl_by_block[bn] += n
    print(f'手算表覆盖块名 {len(xl_by_block)} 种；DXF 全图块名 {len(my_by_block)} 种')
    print(f'手算表块计数合计 {sum(xl_by_block.values()):.0f}；DXF 全图 INSERT 合计 {sum(my_by_block.values())}')
    print('\n手算表出现的块名逐项对比（xl=手算, dxf=全图, Δ%=偏差）:')
    matched = 0
    for bn, xn in xl_by_block.most_common(30):
        dn = my_by_block.get(bn, 0)
        dev = (dn - xn) / xn * 100 if xn else float('nan')
        flag = '✓' if abs(dev) < 2 else ' '
        if abs(dev) < 2:
            matched += 1
        print(f'  {flag} {bn[:34]:34s} xl={xn:6.0f}  dxf={dn:6.0f}  Δ={dev:+6.1f}%')
    print(f'\n前 30 项中偏差<2% 者: {matched}（其余差异主要来自手算表未覆盖的区域）')

    # ── 线长对账 ──
    print('\n' + '=' * 72)
    print('线长对账：手算表(跨区域求和, m) vs DXF 全图(m)')
    print('=' * 72)
    for layer, xm in sorted(xl_wires.items(), key=lambda kv: -kv[1]):
        dm = my_wires.get(layer, 0.0)
        dev = (dm - xm) / xm * 100 if xm else float('nan')
        flag = '✓' if abs(dev) < 2 else ' '
        print(f'  {flag} {layer:22s} xl={xm:9.2f}  dxf={dm:9.2f}  Δ={dev:+6.1f}%')


if __name__ == '__main__':
    if '--region' in sys.argv:
        recon_by_region()
    else:
        main()
