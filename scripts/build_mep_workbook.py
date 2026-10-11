# -*- coding: utf-8 -*-
"""scripts/build_mep_workbook.py — 227户型 M2~M5 机电封账数字 → 工程量计算表(xlsx)。

纪律(与 golden 测试同构, 违反即构建失败):
  1. 只读封账工件(m2_counts_clean / m2_textcounts / m5_*_rows / m3_circuits /
     p24_sprinkler_split / mep_cost_indicators), 本脚本不新增不修改任何数字;
  2. 恒等断言: 每类归属 Σ ≡ M2 封账计数、面板 Σ控制线 ≡ 93、喷淋 24+9 ≡ 33,
     破账直接 AssertionError, 不产出文件;
  3. 三态如实: "未能归属"行照列挂账(状态=未能读取), 不硬落域;
     "图上没有"项(烟感/水浸/管网)走 D 层披露, 不臆造;
  4. A 层图纸实测账与 D 层单方估算账分 sheet 并列, 永久不混
     (口径纪律见 docs/MEP_COST_INDICATORS.md)。

每行带计算式与图纸追溯, 可独立复算审计。
运行: venv/bin/python scripts/build_mep_workbook.py
产物: outputs/227户型机电工程量计算表.xlsx
"""
import json
import os
from collections import defaultdict, OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, 'outputs', 'preview', 'vlm', 'mep')
IND_PATH = os.path.join(ROOT, 'app', 'static', 'mep_cost_indicators.json')
OUT_PATH = os.path.join(ROOT, 'outputs', '227户型机电工程量计算表.xlsx')

# D 层估算规模: P3b 天花域封账口径(=装修净面积), 来源披露见 docs/MEP_COST_INDICATORS.md 规则4
DEC_AREA_S = 97.78
TYPE_ID = 'show_flat'          # 住宅样板房
BOX_UNIT_KINDS = ('强电箱', '弱电箱', '等电位箱')   # 计量单位=台, 其余=个


def _load(name):
    with open(os.path.join(ART, name), encoding='utf-8') as f:
        return json.load(f)


def _unit(kind):
    return '台' if any(b in kind for b in BOX_UNIT_KINDS) else '个'


def _agg_rows(rows, flags=None):
    """rows → OrderedDict[(kind, room, 待核)] = {'n': Σn, 'parts': [每行n]}，排序稳定。

    按"是否待核"拆行聚合(同一 kind+room 内部分行撞车时不会误标全组)，总量恒等不变。"""
    flags = flags or [False] * len(rows)
    g = defaultdict(lambda: {'n': 0, 'parts': []})
    for r, f in zip(rows, flags):
        k = (r['kind'], r['room'], bool(f))
        g[k]['n'] += r['n']
        g[k]['parts'].append(r['n'])
    out = OrderedDict()
    for k in sorted(g, key=lambda x: (x[0], x[1], x[2])):
        out[k] = g[k]
    return out


def _collision_flags(rows):
    """行→ bool: 该引线端点是否与其**他专业类别**的行共用同一落点。

    失效模式(M5c §8): 行走器遇长直线(墙线/轴网/尺寸界线)会骑上去直走，
    同一注记簇里多条文字都骑同一条线 → 收敛到同一落点。不同类别的点位
    物理上不可能共点 → 这些行的房间归属不可信(数量仍可信)。
    未追通行无端点 → False(已是"未能读取"挂账, 优先级更高)。
    同类共用的行不算在内(可能为 X2 乘数表达), 另作附加披露。
    """
    g = defaultdict(list)
    for r in rows:
        if r.get('endpoint'):
            g[tuple(round(v, 1) for v in r['endpoint'])].append(r['kind'])
    return [bool(r.get('endpoint'))
            and len(set(g[tuple(round(v, 1) for v in r['endpoint'])])) > 1
            for r in rows]


def _state(room, collide=False):
    if room == '未能归属':
        return '未能读取(挂账)'
    if collide:
        return '归属待核(端点与其类引线共用)'
    if room.startswith('(无名'):
        return '落域·房间名待核'
    return '已确认'


def build():
    """组装四张表的数据并做恒等断言。返回 dict(totals/sheets...)；不写文件。"""
    m2 = _load('m2_counts_clean.json')
    rows8 = _load('m5_P08_rows.json')
    rows9 = _load('m5_P09_rows.json')
    t9 = _load('m2_textcounts.json')['19']['counts']
    circ = _load('m3_circuits.json')
    spr = _load('p24_sprinkler_split.json')
    with open(IND_PATH, encoding='utf-8') as f:
        ind = json.load(f)

    # ── 恒等断言(与 tests/test_mep_golden.py::test_m5_attribution_totals_match_m2 同构) ──
    got8 = defaultdict(int)
    for r in rows8:
        got8[r['kind']] += r['n']
    for kind, v in got8.items():
        assert v == m2['18'][kind]['total'], f'P08 {kind} 归属Σ{v} != M2封账{m2["18"][kind]["total"]}'
    got9 = defaultdict(int)
    for r in rows9:
        got9[r['kind']] += r['n']
    for kind, v in got9.items():
        assert v == t9[kind], f'P09 {kind} 归属Σ{v} != M2封账{t9[kind]}'
    n_wires = sum(p['n_wires'] for p in circ['panels'])
    assert n_wires == circ['total_control_wires'], f'面板Σ控制线{n_wires} != 封账{circ["total_control_wires"]}'
    assert len(spr['down']) + len(spr['side']) == 33, '喷淋 M4 封账=33(下24+侧9)'
    assert len(spr['down']) == 24 and len(spr['side']) == 9

    totals = {
        'P08': sum(got8.values()),
        'P09': sum(got9.values()),
        'hvac': sum(v['total'] for v in m2['23'].values()),
        'elv': sum(v['total'] for v in m2['24'].values()),
        'sprinkler': 33,
        'control_wires': n_wires,
    }

    # ── Sheet 点位数量账(A 层图纸实测) ──
    s1 = []   # (专业, kind, room, unit, n, formula, trace, state)
    pend = {}     # tag -> {'rows': 待核行数, 'n': 待核件数}
    by_tag = {}   # tag -> (rows, flags)
    for tag, rows, closed, page in (('P08', rows8, m2['18'], 'P08 强电点位平面图'),
                                    ('P09', rows9, t9, 'P09 给排水点位平面图')):
        flags = _collision_flags(rows)
        by_tag[tag] = (rows, flags)
        kept = [(r, f) for r, f in zip(rows, flags) if f and r['room'] != '未能归属']
        pend[tag] = {'rows': len(kept), 'n': sum(r['n'] for r, _ in kept),
                     'rows_all': sum(1 for f in flags if f)}
        agg = _agg_rows(rows, flags)
        kinds = sorted({k for k, _, _ in agg})
        for kind in kinds:
            sub = {(r, pd): v for (k, r, pd), v in agg.items() if k == kind}
            for (room, is_pend), v in sub.items():
                expr = f"{v['n']}" if len(v['parts']) == 1 else 'Σ' + '+'.join(map(str, v['parts']))
                s1.append(('强电' if tag == 'P08' else '给排水', kind, room, _unit(kind),
                           v['n'], f'{expr}（{tag}归属行{len(v["parts"])}条聚合）',
                           page, _state(room, is_pend)))
    # 拆行聚合(新增待核维度)不得丢量/重量: 每页拆后Σ仍须等于封账
    for tag, page in (('P08', 'P08 强电点位平面图'), ('P09', 'P09 给排水点位平面图')):
        got = sum(r[4] for r in s1 if r[6] == page)
        assert got == totals[tag], f'{tag} 待核拆行后聚合Σ{got} != 封账{totals[tag]}(丢量或重量)'
    for pg, disc, note in (('23', '空调通风', 'M4已逐点落域,明细见m2_attribution.json'),
                           ('24', '智能化弱电', 'M4已逐点落域,明细见m2_attribution.json')):
        for kind, v in sorted(m2[pg].items()):
            s1.append((disc, kind, '全户型(分房间见' + note.split('见')[1].rstrip('，,') + ')',
                       _unit(kind), v['total'], f"M2符号计数封账={v['total']}",
                       f'P{int(pg)-7:02d} 机房/天花图(idx{pg})', '已确认'))
    s1.append(('消防', '喷淋头(下喷)', '全户型', '个', len(spr['down']),
               '24=33-9; 逐头contact sheet 33/33确认', 'P24消防图 M4封账', '已确认'))
    s1.append(('消防', '喷淋头(侧喷)', '全户型', '个', len(spr['side']),
               '9; 藏灯虚线旁方框尾形态判读(M4§3遗留5:下/侧型号拆分待深化)',
               'P24消防图 M4封账', '已确认'))
    s1.append(('消防', '烟感/水浸探测器', '—', '个', 0,
               '0; 全主图区矢量扫描0命中+200dpi目视 → 三态"图上没有"',
               'M4§1定案', '图上没有(走D层/缺图披露)'))

    # ── Sheet② 回路与配电箱(M3 拓扑封账) ──
    s2 = []
    for p in circ['panels']:
        s2.append((p['panel'], p['expr'], len(p['LD']), len(p['ALD']), len(p['keys']),
                   p['n_wires'], f"Σ受控对象={len(p['LD'])}+{len(p['ALD'])}+{len(p['keys'])}={p['n_wires']}"))
    assert sum(r[5] for r in s2) == totals['control_wires']

    # ── Sheet③ D 层单方估算(非图纸量) ──
    t = ind['types'][TYPE_ID]
    f_ = t['scaleFactor']
    s3 = []
    for dkey, dname in (('electrical', '强电'), ('plumbing', '给排水'), ('hvac', '空调通风'),
                        ('fire', '消防'), ('elv', '智能化弱电')):
        c = t['cost'][dkey]
        s3.append((dname, c['mid'], f_, DEC_AREA_S,
                   f"{c['mid']}×{f_}×{DEC_AREA_S}={c['mid']*f_*DEC_AREA_S:,.0f}",
                   c['mid'] * f_ * DEC_AREA_S,
                   c['range'][0] * f_ * DEC_AREA_S, c['range'][1] * f_ * DEC_AREA_S))
    s3_len = []
    for k, spec in ind['meta']['lengthCoefficients'].items():
        if k == 'unit':
            continue
        s3_len.append((k, spec['mid'], f"{spec['mid']}×{DEC_AREA_S}={spec['mid']*DEC_AREA_S:.0f}m",
                       spec['mid'] * DEC_AREA_S, spec.get('note', '')))

    # ── Sheet④ 对账 + 挂账清单 ──
    s4_recon = [
        ('P08 强电点位', totals['P08'], m2['18'] and sum(v['total'] for v in m2['18'].values()), '锚点口径(USB双注2锚=1件,定案6件见M5通注)'),
        ('P09 给排水点位', totals['P09'], sum(t9.values()), 'm2_textcounts["19"]'),
        ('空调风口', totals['hvac'], totals['hvac'], 'm2_counts_clean["23"] S7/R7/E4'),
        ('智能化弱电', totals['elv'], totals['elv'], 'm2_counts_clean["24"]'),
        ('喷淋头', 33, len(spr['down']) + len(spr['side']), 'M4定案 下24+侧9'),
        ('控制线', totals['control_wires'], n_wires, 'M3定案 Σ受控对象(保守上界,注⑪以华为深化为准)'),
    ]
    s4_open = []
    open8 = defaultdict(int)
    for r in rows8:
        if r['room'] == '未能归属':
            open8[r['kind']] += r['n']
    for kind, n in sorted(open8.items()):
        s4_open.append((f'P08 {kind} ×{n}', '归属未达(引线未追通/端点超容差)', 'M5报告§2/§6'))
    open9 = defaultdict(int)
    for r in rows9:
        if r['room'] == '未能归属':
            open9[r['kind']] += r['n']
    for kind, n in sorted(open9.items()):
        s4_open.append((f'P09 {kind} ×{n}', '归属未达', 'M5报告§3'))
    s4_open += [
        ('LD-22', '有标签无回路注记,疑与LD-03编号笔误配对', 'M4§2 / m3_circuits.LD_missing'),
        ('ALD 定位', '需灯具明细表(图纸集无机读源)', 'M4§3'),
        ('水浸传感器 / USB双注', 'M5c 人工看图维持挂账:端点落柱角无落域依据 / 引线链末回折端点不可信', 'M5§7.1'),
        ('P08 口径漏项 25 条(中厨/西厨/卫浴图面内直接标注 + 4 条注记未含类)',
         '图上明确标注但 M2 未入账;①并类口径已裁(5孔→二三极插座,3孔单列),待②③后一次性重封账(86→86+N)', 'M5§7.2/§7.2.1'),
        ('烟感/水浸布置、给水管网、冷媒/喷淋/弱电管网、配电系统图', '图上没有 → 走D层单方或缺图披露', 'M4§4 三态总账'),
    ]

    # 归属待核清单: 数量已计入账, 但引线端点与其类共用 → 房间归属不可信(M5c §8)
    s4_pend = []
    for tag in ('P08', 'P09'):
        rows, flags = by_tag[tag]
        grp = defaultdict(lambda: {'n': 0, 'rooms': set(), 'pts': set()})
        for r, f in zip(rows, flags):
            if f and r['room'] != '未能归属':
                k = (r['kind'], r['room'])
                grp[k]['n'] += r['n']
                grp[k]['rooms'].add(r['room'])
                grp[k]['pts'].add(tuple(round(v) for v in r['endpoint']))
        for (kind, room), v in sorted(grp.items()):
            pts = ', '.join(f'({x},{y})' for x, y in sorted(v['pts']))
            s4_pend.append((f'{tag} {kind} ×{v["n"]}', room, pts))
    pend_n = pend['P08']['n'] + pend['P09']['n']
    s4_pend.append(('合计', f'{pend["P08"]["rows"] + pend["P09"]["rows"]} 行 / {pend_n} 件',
                    f'另有撞车但已属挂账 {pend["P08"]["rows_all"] + pend["P09"]["rows_all"] - pend["P08"]["rows"] - pend["P09"]["rows"]} 行(计在上方挂账清单); '
                    '数量已计且计入Σ恒等, 仅房间归属存疑, 修好引线追踪后自动消除'))

    return {
        'totals': totals,
        'pending': pend,
        'sheet1': s1,
        'sheet2': s2, 'sheet3': s3, 'sheet3_len': s3_len,
        'sheet4_recon': s4_recon, 'sheet4_open': s4_open, 'sheet4_pend': s4_pend,
        'd_meta': {'type': t['label'], 'factor': f_, 'S': DEC_AREA_S,
                   'disclaimer': ind['meta']['disclaimer'], 'version': ind['meta']['version']},
    }


# ══════════════════════════ xlsx 渲染 ══════════════════════════
def write_xlsx(data, path=OUT_PATH):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    thin = Side(style='thin', color='B0B0B0')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill('solid', fgColor='4472C4')
    sect_fill = PatternFill('solid', fgColor='D9E1F2')
    open_fill = PatternFill('solid', fgColor='FFF2CC')
    pend_fill = PatternFill('solid', fgColor='FCE4D6')   # 归属待核(区别于挂账)
    bold = Font(bold=True)
    wrap = Alignment(vertical='top', wrap_text=True)

    wb = Workbook()
    wb.remove(wb.active)

    def sheet(name, headers, widths):
        ws = wb.create_sheet(name)
        ws.append(headers)
        for i, h in enumerate(headers, 1):
            c = ws.cell(1, i)
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = head_fill
            c.alignment = Alignment(horizontal='center', vertical='center')
            ws.column_dimensions[chr(64 + i) if i <= 26 else 'A' + chr(38 + i)].width = widths[i - 1]
        ws.freeze_panes = 'A2'
        return ws

    def finish(ws, row_idx, ncols):
        for r in ws.iter_rows(min_row=2, max_row=row_idx - 1, max_col=ncols):
            for c in r:
                c.border = border
                c.alignment = wrap

    # ① 点位数量账
    ws = sheet('①点位数量账A层', ['序号', '专业', '项目名称', '部位(房间)', '单位', '数量', '计算式', '图纸追溯', '三态'],
               [6, 10, 20, 26, 6, 8, 40, 26, 22])
    i = 2
    for disc, kind, room, unit, n, expr, trace, state in data['sheet1']:
        ws.append([i - 1, disc, kind, room, unit, n, expr, trace, state])
        if state != '已确认':
            for c in ws[i]:
                c.fill = open_fill
        i += 1
    ws.append([])
    ws.append(['', '合计(A层点位)', '', '', '', sum(r[4] for r in data['sheet1']),
               'P08锚点+P09+风口+智能化+喷淋(烟感0)', 'Σ≡各页M2/M4封账(见④对账)', ''])
    for c in ws[ws.max_row]:
        c.font = bold
    finish(ws, ws.max_row + 1, 9)

    # ② 回路与配电箱
    ws = sheet('②回路与配电箱', ['序号', '配电箱/面板', '回路注记(封账原文)', 'LD数', 'ALD数', '键控数', '控制线数', '计算式'],
               [6, 30, 40, 8, 8, 8, 10, 30])
    for j, (panel, expr, nld, nald, nkey, nw, f_) in enumerate(data['sheet2'], 1):
        ws.append([j, panel, expr, nld, nald, nkey, nw, f_])
    i = len(data['sheet2']) + 2
    ws.append(['', '合计', f"面板{len(data['sheet2'])}个", '', '', '', data['totals']['control_wires'],
               'Σn_wires≡M3封账93(断言通过)'])
    for c in ws[i]:
        c.font = bold
    finish(ws, i + 1, 8)

    # ③ D层估算
    ws = sheet('③D层单方估算·非图纸量', ['专业', '单方中值(元/㎡)', '规模系数', '装修面积S(㎡)', '算式', '估算金额(元)', '区间低(元)', '区间高(元)'],
               [14, 14, 10, 14, 30, 14, 14, 14])
    for row in data['sheet3']:
        ws.append(list(row))
    i = len(data['sheet3']) + 2
    tot_mid = sum(r[5] for r in data['sheet3'])
    ws.append(['合计', '', '', '', f"{tot_mid / data['d_meta']['S']:.1f}×{data['d_meta']['S']}={tot_mid:,.0f}",
               tot_mid, sum(r[6] for r in data['sheet3']), sum(r[7] for r in data['sheet3'])])
    for c in ws[i]:
        c.font = bold
    ws.append([])
    ws.append(['管线长度估算(系数×S)'])
    ws[ws.max_row][0].font = bold
    for k, mid, expr, val, note in data['sheet3_len']:
        ws.append([k, mid, '', '', expr, val, '', note])
    ws.append([])
    ws.append([f"口径: {data['d_meta']['type']}·指标库v{data['d_meta']['version']}·"
               f"S={data['d_meta']['S']}(P3b天花域封账) | {data['d_meta']['disclaimer']}"])
    ws[ws.max_row][0].font = Font(italic=True, color='808080')
    finish(ws, ws.max_row, 8)

    # ④ 对账与挂账
    ws = sheet('④分账对账+挂账清单', ['来源', '本表数量', '封账数量', '差', '口径说明'], [24, 12, 12, 6, 56])
    for src, a, b, note in data['sheet4_recon']:
        ws.append([src, a, b, a - b, note])
        assert a == b, f'对账破账: {src} {a} != {b}'
    ws.append([])
    ws.append(['挂账清单(未能读取, 如实单列)', '', '', '', ''])
    ws[ws.max_row][0].font = bold
    for item, why, src in data['sheet4_open']:
        ws.append([item, '', '', '', f'{why} | 来源: {src}'])
        for c in ws[ws.max_row]:
            c.fill = open_fill
    ws.append([])
    ws.append(['归属待核清单(数量已计, 房间归属存疑 — 引线端点与其类共用)', '', '', '', ''])
    ws[ws.max_row][0].font = bold
    for item, room, pts in data['sheet4_pend']:
        detail = f'{room} | {pts}' if item == '合计' else f'现判房间={room} | 共用端点 {pts}'
        ws.append([item, '', '', '', detail])
        for c in ws[ws.max_row]:
            c.fill = pend_fill
    finish(ws, ws.max_row, 5)

    wb.save(path)
    return path


def main():
    data = build()
    p = write_xlsx(data)
    t = data['totals']
    print('恒等断言全部通过:')
    for k, v in t.items():
        print(f'  {k} = {v}')
    pd = data['pending']
    print(f"归属待核(数量已计, 端点与其类引线共用, 房间待修追踪后重判): "
          f"P08 {pd['P08']['rows']}行/{pd['P08']['n']}件, P09 {pd['P09']['rows']}行/{pd['P09']['n']}件")
    print('产物:', p)


if __name__ == '__main__':
    main()
