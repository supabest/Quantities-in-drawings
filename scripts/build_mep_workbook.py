# -*- coding: utf-8 -*-
"""scripts/build_mep_workbook.py — 227户型 M2~M5 机电封账数字 → 工程量计算表(xlsx)。

纪律(与 golden 测试同构, 违反即构建失败):
  1. 只读封账工件(m2_counts_clean / m2_textcounts / m5_*_rows / m5f_supplement_P08 /
     m3_circuits / p24_sprinkler_split / mep_cost_indicators), 本脚本不新增不修改任何数字;
  2. 恒等断言: 每类归属 Σ ≡ M2 封账计数、P08 重封账 ≡ M2 86 + M5f 补账 21 = 107、
     面板 Σ控制线 ≡ 93、喷淋 24+9 ≡ 33, 破账直接 AssertionError, 不产出文件;
     (M2 原封账 m2_counts_clean["18"]=86 不改写, 补账另立工件并列可审计)
  3. 三态如实: "未能归属"行照列挂账(状态=未能读取), 不硬落域;
     补账中带标高注记者(+HT)定位出自尚未修复的引线追踪器(M5 §8), 未撞车也判待核;
     **已落域但端点未命中任何引线末端标记的行**(M5 §11, 证据源 m5h_arrows_*.json)
     一律降为待核 —— 骑线行不能冒充已确认(只降状态, 不动数量;
     未能归属行已是挂账, 本规则不重复标记);
     人工裁定例外(MANUAL_PEND, M5 §8.5/§10)仅用于规则覆盖不到的个案;
     "图上没有"项(烟感/水浸/管网)走 D 层披露, 不臆造;
  4. A 层图纸实测账与 D 层单方估算账分 sheet 并列, 永久不混
     (口径纪律见 docs/MEP_COST_INDICATORS.md); 计价清单仅作开发期校验, 不作数据源。

每行带计算式与图纸追溯, 可独立复算审计。
运行: venv/bin/python scripts/build_mep_workbook.py
产物: outputs/227户型机电工程量计算表.xlsx
"""
import hashlib
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

WHY_COLLIDE = '端点与其类引线共用'
WHY_SUPP_LEADER = '补账注记·引线追踪器待修'
WHY_NO_ARROW = '端点未命中末端标记(三角/开口/圆点<1.5pt)'
ARROW_ART = {'P08': 'm5h_arrows_P08.json', 'P09': 'm5h_arrows_P09.json'}

# 人工裁定例外清单(M5 §8.5/§10): 仅用于**通用规则覆盖不到**的个案降级。
# 纪律: 只收用户明确裁定过的行, 按 kind+anchor 精确钉住(命中行数≠1 或房间漂移即构建失败),
#       必须写明原因。§11 的箭头规则生效后, 本清单应为空: 能由入库证据推出来的降级
#       不得留在人工清单里, 否则机器判据永远不会暴露它盖不住哪。
MANUAL_PEND = []   # 2026-10-11 M5g 唯一条目(冰箱+400HT)已被 §11 通用规则自动覆盖(15.65pt/形心53.95pt)


def _load(name):
    with open(os.path.join(ART, name), encoding='utf-8') as f:
        return json.load(f)


def _unit(kind):
    return '台' if any(b in kind for b in BOX_UNIT_KINDS) else '个'


def _agg_rows(rows, flags=None):
    """rows → OrderedDict[(kind, room, 待核原因)] = {'n': Σn, 'parts': [每行n]}，排序稳定。

    flags 为**逐行原因字符串**(''=不待核)。按原因拆行聚合(同一 kind+room 内部分行
    待核时不会误标全组, 不同降级原因也不合并), 总量恒等不变。"""
    flags = flags if flags is not None else [''] * len(rows)
    g = defaultdict(lambda: {'n': 0, 'parts': []})
    for r, f in zip(rows, flags):
        k = (r['kind'], r['room'], f or '')
        g[k]['n'] += r['n']
        g[k]['parts'].append(r['n'])
    out = OrderedDict()
    for k in sorted(g, key=lambda x: (x[0], x[1], x[2])):
        out[k] = g[k]
    return out


def _collision_flags(rows):
    """行→ 降级原因字符串('' = 不降级): 该引线端点是否与其**他专业类别**的行共用同一落点。

    失效模式(M5c §8): 行走器遇长直线(墙线/轴网/尺寸界线)会骑上去直走，
    同一注记簇里多条文字都骑同一条线 → 收敛到同一落点。不同类别的点位
    物理上不可能共点 → 这些行的房间归属不可信(数量仍可信)。
    未追通行无端点 → ''(已是"未能读取"挂账, 优先级更高)。
    同类共用的行不算在内(可能为 X2 乘数表达), 另作附加披露。
    """
    g = defaultdict(list)
    for r in rows:
        if r.get('endpoint'):
            g[tuple(round(v, 1) for v in r['endpoint'])].append(r['kind'])
    return [WHY_COLLIDE if (r.get('endpoint')
            and len(set(g[tuple(round(v, 1) for v in r['endpoint'])])) > 1) else ''
            for r in rows]


def _manual_flags(rows, flags, entries):
    """把人工裁定例外逐条叠加到 flags 上(仅对原本未降级的行生效, 不抢已有原因)。

    每条必须精确命中一行, 且其现判房间与裁定记录一致 —— 封账工件重跑后坐标/房间
    一旦漂移, 此处直接构建失败, 要求重新裁定, 而不是让过期裁定静默作用到别的行。"""
    for e in entries:
        hits = [i for i, r in enumerate(rows)
                if r['kind'] == e['kind']
                and abs(r['anchor'][0] - e['anchor'][0]) < 0.05
                and abs(r['anchor'][1] - e['anchor'][1]) < 0.05]
        assert len(hits) == 1, \
            f'MANUAL_PEND 条目漂移: {e["kind"]}{e["anchor"]} 命中 {len(hits)} 行, 请重新裁定'
        i = hits[0]
        assert rows[i]['room'] == e['room_ruled'], \
            f'MANUAL_PEND 房间漂移: 工件{rows[i]["room"]!r} != 裁定{e["room_ruled"]!r}'
        assert rows[i].get('endpoint'), f'MANUAL_PEND 行无端点(已属挂账), 不适用归属降级: {e["kind"]}'
        if not flags[i]:
            flags[i] = e['why']
    return flags


def _arrow_evidence(tag, rows):
    """读末端标记普查工件 → 逐行 {'dist','hit','form'}(与 rows **同序等长**)。

    证据不能过期: 工件记录的 rows 文件 md5 与当前不一致, 或逐行 kind/anchor/endpoint
    对不上 → 直接构建失败, 要求重跑 `python -m app.orchestrator.mep.arrows`。
    无端点的行(直接标注/未追通) → dist=None, 不适用本判据。
    """
    art = _load(ARROW_ART[tag])
    src = art['meta']['rows_sources']
    now = {f: hashlib.md5(open(os.path.join(ART, f), 'rb').read()).hexdigest() for f in src}
    assert now == art['meta']['rows_md5'], \
        f'{tag} 末端标记证据过期: rows 工件已变({src}) 而普查未重跑 → 请跑 python -m app.orchestrator.mep.arrows'
    arows = art['rows']
    assert len(arows) == len(rows), f'{tag} 普查行数{len(arows)} != 当前池行数{len(rows)}'
    out = []
    for i, (a, r) in enumerate(zip(arows, rows)):
        same = (a['kind'] == r['kind'] and abs(a['anchor'][0] - r['anchor'][0]) < 0.05
                and abs(a['anchor'][1] - r['anchor'][1]) < 0.05
                and bool(a.get('endpoint')) == bool(r.get('endpoint'))
                and (not a.get('endpoint')
                     or (abs(a['endpoint'][0] - r['endpoint'][0]) < 0.05
                         and abs(a['endpoint'][1] - r['endpoint'][1]) < 0.05)))
        assert same, f'{tag} 第{i}行证据与归属行不对齐({a["kind"]}@{a["anchor"]} != {r["kind"]}@{r["anchor"]})'
        out.append({'dist': a.get('dist_pt'), 'hit': bool(a.get('hit')),
                    'form': a.get('form')} if r.get('endpoint') else {'dist': None, 'hit': False, 'form': None})
    return out, art['meta']


def _state(room, pd=''):
    """四态优先级: 挂账 > 归属待核(逐行原因) > 房间名待核 > 已确认。"""
    if room == '未能归属':
        return '未能读取(挂账)'
    if pd:
        return f'归属待核({pd})'
    if room.startswith('(无名'):
        return '落域·房间名待核'
    return '已确认'


def _supp_row(r, verdict):
    """补账行 → Sheet⑤ 一行: 每条自带字号/打印可见性/坐标/落域依据/清单对应项/裁定依据。"""
    pos = f"({r['anchor'][0]},{r['anchor'][1]})"
    if r.get('endpoint') and r['room'] == '未能归属':
        pos += f" → 端点({r['endpoint'][0]},{r['endpoint'][1]})"
        why = (f"引线追踪链{r['len']}pt 仍未落域(疑骑长直线, §8.2 失效模式) → 如实挂账")
    elif r.get('endpoint'):
        pos += f" → 端点({r['endpoint'][0]},{r['endpoint'][1]})"
        why = f"引线追踪链{r['len']}pt(追踪器待修§8)"
    elif r.get('in_face'):
        why = f"图面直接标注, 落在房间面内(离墙{r['wall_dist']}pt)"
    elif r.get('wall_dist') is not None:
        why = f"图面直接标注, 贴墙容差内落域(离边{r['wall_dist']}pt)"
    else:
        why = '文字位置未落域(离最近面>12pt容差), 位置待核'
    return (r['text'], r['kind'], verdict, pos, f"{r['size']}pt"
            f"/{r['size'] * 0.3528:.2f}mm", '可读' if r['visible'] else '✗不可读(符号可见)',
            r['room'], why, r['boq'], r['basis'])


def build():
    """组装五张表的数据并做恒等断言。返回 dict(totals/sheets...)；不写文件。"""
    m2 = _load('m2_counts_clean.json')
    rows8 = _load('m5_P08_rows.json')
    rows9 = _load('m5_P09_rows.json')
    supp = _load('m5f_supplement_P08.json')
    srows8 = supp['rows']
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
    # M5f 补账账账相符: M2 原封账未被改写 + 补账条数/重封账总额钉死
    m2_n = sum(v['total'] for v in m2['18'].values())
    supp_n = sum(r['n'] for r in srows8)
    assert m2_n == supp['m2_closed'], f'M2 封账漂移: 工件{m2_n} != 补账记载{supp["m2_closed"]}'
    assert supp_n == supp['supplement_n'], f'补账Σn{supp_n} != 记载{supp["supplement_n"]}'

    totals = {
        'P08': m2_n + supp_n,
        'P09': sum(got9.values()),
        'hvac': sum(v['total'] for v in m2['23'].values()),
        'elv': sum(v['total'] for v in m2['24'].values()),
        'sprinkler': 33,
        'control_wires': n_wires,
    }
    assert totals['P08'] == supp['resealed'], f"P08 重封账{totals['P08']} != 补账工件{supp['resealed']}"
    assert supp_n == 21 and totals['P08'] == 107, 'P08 口径钉死: M2 86 + M5f 补账 21 = 107'

    # ── Sheet 点位数量账(A 层图纸实测) ──
    s1 = []   # (专业, kind, room, unit, n, formula, trace, state)
    pend = {}     # tag -> {'rows': 待核行数, 'n': 待核件数}
    page8, page9 = 'P08 强电点位平面图', 'P09 给排水点位平面图'
    # 撞车判定在**合并池**上做一次: 补账落点若与 M2 账共点同样要揪出来
    merged8 = rows8 + srows8
    flags8 = _collision_flags(merged8)
    for i in range(len(rows8), len(merged8)):   # 补账注记行(+HT): 追踪器缺陷未修(§8), 未撞车也判待核
        if merged8[i].get('leader') and merged8[i]['room'] != '未能归属':
            flags8[i] = WHY_SUPP_LEADER
    _manual_flags(merged8, flags8, MANUAL_PEND)  # 用户裁定例外(§8.5): 仅降级归属状态, 不动任何量
    flags9 = _collision_flags(rows9)
    arr8, ameta8 = _arrow_evidence('P08', merged8)
    arr9, ameta9 = _arrow_evidence('P09', rows9)
    # §11 通用规则: 端点未命中任何末端标记 → 无引线终点凭证 → 降待核(不抢已有更具体原因)。
    # 只作用于**已落域**行: 未能归属行本就是"未能读取(挂账)"(优先级更高), 给它记原因既看不见
    # 也会把挂账行按原因白白拆成几组 —— n_noarrow 必须等于"状态真的变了"的行数。
    n_noarrow = 0
    for ev, flags, rows in ((arr8, flags8, merged8), (arr9, flags9, rows9)):
        for i, e in enumerate(ev):
            if (e['dist'] is not None and not e['hit'] and not flags[i]
                    and rows[i]['room'] != '未能归属'):
                flags[i] = WHY_NO_ARROW
                n_noarrow += 1
    by_tag = {'P08': (merged8, flags8), 'P09': (rows9, flags9)}
    for tag, (rows, flags) in by_tag.items():
        kept = [(r, f) for r, f in zip(rows, flags) if f and r['room'] != '未能归属']
        pend[tag] = {'rows': len(kept), 'n': sum(r['n'] for r, _ in kept),
                     'rows_all': sum(1 for f in flags if f)}

    def emit(rows, flags, label, page):
        agg = _agg_rows(rows, flags)
        for kind in sorted({k for k, _, _ in agg}):
            sub = {(r, pd): v for (k, r, pd), v in agg.items() if k == kind}
            for (room, pd), v in sub.items():
                expr = f"{v['n']}" if len(v['parts']) == 1 else 'Σ' + '+'.join(map(str, v['parts']))
                s1.append(('强电' if label.startswith('P08') else '给排水', kind, room,
                           _unit(kind), v['n'],
                           f'{expr}（{label}归属行{len(v["parts"])}条聚合）',
                           page, _state(room, pd)))

    emit(rows8, flags8[:len(rows8)], 'P08', page8)
    emit(srows8, flags8[len(rows8):], 'P08 M5f补账', page8)
    emit(rows9, by_tag['P09'][1], 'P09', page9)
    # 拆行聚合(新增待核维度)不得丢量/重量: 每页拆后Σ仍须等于封账
    for tag, page in (('P08', page8), ('P09', page9)):
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
        ('P08 强电点位', totals['P08'], m2_n + supp_n,
         f'M2封账{m2_n}锚 + M5f补账{supp_n}(§9); 锚点口径(USB双注2锚=1件,定案6件见M5通注)'),
        ('P09 给排水点位', totals['P09'], sum(t9.values()), 'm2_textcounts["19"]'),
        ('空调风口', totals['hvac'], totals['hvac'], 'm2_counts_clean["23"] S7/R7/E4'),
        ('智能化弱电', totals['elv'], totals['elv'], 'm2_counts_clean["24"]'),
        ('喷淋头', 33, len(spr['down']) + len(spr['side']), 'M4定案 下24+侧9'),
        ('控制线', totals['control_wires'], n_wires, 'M3定案 Σ受控对象(保守上界,注⑪以华为深化为准)'),
    ]
    s4_open = []
    open8 = defaultdict(int)      # (kind, 账面来源) -> 挂账件数: 补账/M2 分道披露
    for r in merged8:
        if r['room'] == '未能归属':
            open8[(r['kind'], 'M5f补账' if r.get('src') else 'M2账')] += r['n']
    for (kind, prov), n in sorted(open8.items()):
        why = '归属未达(引线未追通/端点超容差)'
        ref = 'M5报告§2/§6'
        if prov == 'M5f补账':
            why = '文字位置离最近房间面>12pt容差, 引线未追通 → 不猜位置'
            ref = 'M5报告§9'
        s4_open.append((f'P08 {kind} ×{n} [{prov}]', why, ref))
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
        (f'P08 口径漏项 24 条 → M5f 已补账并入 {supp_n} 件({m2_n}→{totals["P08"]})',
         '裁定: ①5孔并类二三极/3孔单列; ②盒类不计点位(另列3处); ③厨房电器照数(定位待橱柜深化). '
         '其中10条为0.18pt不可见文字(打印后0.064mm纸上读不到, 但设备符号可见, 接触表m5e_5实证)',
         'M5§7.2勘误/§9 逐条证据见⑤'),
        ('P08 预留电源(藏光预留接线盒) 3 处',
         '②裁定不计插座点位(已排除于107之外); 清单该项 227 审核量=0 → 计价时需单独列项', 'M5§9 / ⑤补账明细'),
        ('P08 清单校验差异: 二三极 33vs31、冰箱 5vs4、紧急按钮 4vs5',
         '清单仅作开发期校验(非数据源), 不据此改图面值; 差异待电气专业DXF到位后复核', 'M5§9.4'),
        ('P08 分房间归属总量待核',
         '引线追踪器缺陷未修(M5§8箭头普查定案本PDF上不可行), 房间列仅供参照; 数量账不受影响',
         'M5§8 / 待电气专业DWG/DXF'),
        ('烟感/水浸布置、给水管网、冷媒/喷淋/弱电管网、配电系统图', '图上没有 → 走D层单方或缺图披露', 'M4§4 三态总账'),
    ]

    # 归属待核清单: 数量已计入账, 但逐行原因(撞车/补账注记/用户裁定)使房间归属不可信
    s4_pend = []
    for tag in ('P08', 'P09'):
        rows, flags = by_tag[tag]
        grp = defaultdict(lambda: {'n': 0, 'rooms': set(), 'pts': set(), 'why': set()})
        for r, f in zip(rows, flags):
            if f and r['room'] != '未能归属':
                k = (r['kind'], r['room'])
                grp[k]['n'] += r['n']
                grp[k]['rooms'].add(r['room'])
                grp[k]['pts'].add(tuple(round(v) for v in r['endpoint']))
                grp[k]['why'].add(f)
        for (kind, room), v in sorted(grp.items()):
            pts = ', '.join(f'({x},{y})' for x, y in sorted(v['pts']))
            s4_pend.append((f'{tag} {kind} ×{v["n"]}', room, pts, ' / '.join(sorted(v['why']))))
    pend_n = pend['P08']['n'] + pend['P09']['n']
    s4_pend.append(('合计', f'{pend["P08"]["rows"] + pend["P09"]["rows"]} 行 / {pend_n} 件',
                    f'另有降级但已属挂账 {pend["P08"]["rows_all"] + pend["P09"]["rows_all"] - pend["P08"]["rows"] - pend["P09"]["rows"]} 行(计在上方挂账清单); '
                    '数量已计且计入Σ恒等, 仅房间归属存疑, 修好引线追踪后自动消除', ''))

    # 不升档清单(§11): 现因撞车/补账/裁定而待核, 但端点**确实踩在末端标记上**的行。
    # 箭头证据比撞车证据更硬, 但本轮只做了降级方向, 是否据箭头撤销撞车需用户另行裁定。
    s4_hitpend = []
    for tag, ev in (('P08', arr8), ('P09', arr9)):
        rows, flags = by_tag[tag]
        for i, e in enumerate(ev):
            if e['hit'] and flags[i] and flags[i] != WHY_NO_ARROW and rows[i]['room'] != '未能归属':
                s4_hitpend.append((f"{tag} {rows[i]['kind']} ×{rows[i]['n']}", rows[i]['room'],
                                   f"{e['dist']}pt / {e['form']}", flags[i]))
    s4_hitpend.sort()
    ameta = {'P08': ameta8, 'P09': ameta9}

    # ── Sheet⑤ M5f 补账明细(逐条证据, 与①同账可回溯) ──
    s5 = [_supp_row(r, '计入点位') for r in supp['rows']] \
        + [_supp_row(r, '不计点位(另立)') for r in supp['excluded_box']]

    return {
        'totals': totals,
        'pending': pend,
        'arrow': {'n_noarrow_downgraded': n_noarrow, 'meta': ameta},
        'sheet1': s1,
        'sheet2': s2, 'sheet3': s3, 'sheet3_len': s3_len,
        'sheet4_recon': s4_recon, 'sheet4_open': s4_open, 'sheet4_pend': s4_pend,
        'sheet4_hitpend': s4_hitpend,
        'sheet5': s5,
        'supp_meta': {'m2': m2_n, 'supp': supp_n, 'sealed': totals['P08'],
                      'box_n': len(supp['excluded_box']), 'sealed_by': supp['sealed_by'],
                      'note': supp['note']},
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
    sm = data['supp_meta']
    ws.append(['', '合计(A层点位)', '', '', '', sum(r[4] for r in data['sheet1']),
               f"P08(锚点{sm['m2']}+M5f补账{sm['supp']}={sm['sealed']})+P09+风口+智能化+喷淋(烟感0)",
               'Σ≡各页M2/M4封账+M5f补账(见④对账)', ''])
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
    ws.append(['归属待核清单(数量已计, 房间归属存疑 — 原因逐行列于末列)', '', '', '', ''])
    ws[ws.max_row][0].font = bold
    for item, room, pts, why in data['sheet4_pend']:
        detail = f'{room} | {pts}' if item == '合计' \
            else f'现判房间={room} | 落点 {pts} | 原因: {why}'
        ws.append([item, '', '', '', detail])
        for c in ws[ws.max_row]:
            c.fill = pend_fill
    ws.append([])
    hp = data['sheet4_hitpend']
    ws.append([f'不升档清单(§11): 现因{WHY_COLLIDE}/{WHY_SUPP_LEADER}等而待核, '
               f'但端点踩在末端标记上 — 共 {len(hp)} 行, 本轮不撤销其待核, 只披露证据供后续裁定',
               '', '', '', ''])
    ws[ws.max_row][0].font = bold
    for item, room, ev, why in hp:
        ws.append([item, '', '', '', f'现判房间={room} | 最近末端标记 {ev} | 现降级原因: {why}'])
        for c in ws[ws.max_row]:
            c.fill = pend_fill
    finish(ws, ws.max_row, 5)

    # ⑤ M5f 补账明细(逐条证据)
    sm = data['supp_meta']
    ws = sheet('⑤M5f补账明细', ['序号', '图面标注原文', '入账类别', '点位口径', '坐标(PDF pt)',
                              '字号/打印尺', '纸上可读性', '落域房间', '定位依据',
                              '清单对应列项(仅校验)', '裁定依据'],
               [6, 24, 16, 16, 26, 14, 18, 12, 34, 34, 34])
    for j, row in enumerate(data['sheet5'], 1):
        ws.append([j] + list(row))
        if row[3].startswith('不计') or row[7] == '未能归属':
            for c in ws[ws.max_row]:
                c.fill = open_fill
    ws.append([])
    ws.append(['', f"重封账: M2 {sm['m2']} + 补账 {sm['supp']} = {sm['sealed']} 锚点"
                     f"(USB双注2锚=1件 → {sm['sealed'] - 1} 件)", '',
               f"盒类另立 {sm['box_n']} 处不计点位", '', '', '', '', '',
               sm['sealed_by'], sm['note']])
    for c in ws[ws.max_row]:
        c.font = bold
    ws.append(['', '注: 0.18pt 文字打印后 0.064mm 纸上读不到, 但设备符号可见(接触表 '
                    'outputs/preview/vlm/mep/m5e_5_隐藏标注_符号接触表.png 逐点实证) —— '
                    '属真漏项而非幻影; 清单列项仅用于开发期校验, 生产不作数据源。'])
    ws[ws.max_row][0].font = Font(italic=True, color='808080')
    finish(ws, ws.max_row + 1, 11)

    wb.save(path)
    return path


def main():
    data = build()
    p = write_xlsx(data)
    t = data['totals']
    sm = data['supp_meta']
    print('恒等断言全部通过:')
    for k, v in t.items():
        print(f'  {k} = {v}')
    print(f"  P08 重封账 = M2 {sm['m2']} + M5f 补账 {sm['supp']} = {sm['sealed']} "
          f"(盒类另立 {sm['box_n']} 处不计点位)")
    pd = data['pending']
    whys = sorted({r[7][len('归属待核('):-1] for r in data['sheet1'] if r[7].startswith('归属待核(')})
    print(f"归属待核(数量已计, 原因={'/'.join(whys)}, 房间待重判): "
          f"P08 {pd['P08']['rows']}行/{pd['P08']['n']}件, P09 {pd['P09']['rows']}行/{pd['P09']['n']}件")
    ar = data['arrow']
    print(f"  其中§11 箭头规则新降 {ar['n_noarrow_downgraded']} 行"
          f"(证据: P08 标记{ar['meta']['P08']['n_marks']}个/三角{ar['meta']['P08']['n_triangles']}个, "
          f"P09 标记{ar['meta']['P09']['n_marks']}个/三角{ar['meta']['P09']['n_triangles']}个, 容差{ar['meta']['P08']['hit_tol_pt']}pt); "
          f"不升档披露 {len(data['sheet4_hitpend'])} 行(待核但踩到真箭头)")
    print('产物:', p)


if __name__ == '__main__':
    main()
