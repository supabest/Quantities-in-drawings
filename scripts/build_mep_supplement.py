# -*- coding: utf-8 -*-
"""scripts/build_mep_supplement.py — P08 口径漏项补账(M5f) → m5f_supplement_P08.json。

来源与裁定链(全部可复核, 数字只来自 PDF 文本层几何 + 人工裁定):
  §7.2 查出 M2 取数口径(认"+HT"标高注记)漏掉的图面标注 24 条(原报 25, 其中
  `预留接线盒` 同坐标重复 1 条已去重);
  §8.5/M5e 逐条核验**打印可见性**: 14 条为 3.78~7.25pt 可见文字, 10 条为 **0.18pt
  不可见文字**(打印后 0.064mm, 纸上读不到), 但 10 处均画有可见设备符号(接触表
  m5e_5 逐点实证, 墨迹占比 7~53%) → 属真漏项, 非幻影;
  裁定(2026-10-11): ①5孔→并类二三极插座, 3孔→单列(§7.2.1);
    带防水那 3 处 → **按带防水盖计**(并入既有"防水插座"类, 与清单 227 审核
    "防溅插座 7 + 防溅带开关 2 = 9" 吻合: 本账防水类 6 + 3 = 9);
    ②盒类 3 条(灯线盒/插座线盒/预留接线盒) → **不计插座点位**, 另立"预留电源
    (藏光预留接线盒)"项如实列出(清单该项 227 审核量 = 0);
    ③设计说明"厨房电器插座位置及高度仅作示意, 具体需由橱柜公司图纸另详" →
    **照数**, 定位标待深化; 灯带电源线按 B 口径**计点**(与封账既有"镜柜灯带电源"4 件自洽)。

归属: 直接标注者, 标签中心即符号位(接触表实证), 走 RoomIndex.room_of(点到边, 12pt
墙装容差) —— 与 81 行 M2 账同一判据; 带"+HT"的注记 4 条走 leaders.attribute_rows
同一引线追踪器(追踪器已知缺陷见 §8, 由 build_mep_workbook 的撞车规则统一判状态)。

运行: venv/bin/python scripts/build_mep_supplement.py
产物: outputs/preview/vlm/mep/m5f_supplement_P08.json
"""
import json
import os
import re
import sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.orchestrator.mep.leaders import attribute_rows, thin_segments   # noqa: E402
from app.orchestrator.mep.registration import (DEFAULT_PDF, _dist_poly, _inpoly,   # noqa: E402
                                               load_index)

ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'outputs', 'preview', 'vlm', 'mep')
PAGE = 18
MIN_VISIBLE_PT = 1.0        # 打印可见性阈值: 1pt=0.353mm, 低于此纸上读不出
RE_LEADER = re.compile(r'\+\d+HT', re.I)   # 带标高注记 → 需引线追踪; 否则为直接标注
BOX_KIND = '预留电源(藏光预留接线盒)'   # ②裁定: 盒类另立不计插座点位

# 图面标注原文 → (入账类别, 计入点位?, 计价清单对应列项, 裁定依据)
RULES = {
    '5孔插座':           ('二三极插座', True,  'ZS02-B-061 单相二、三极插座(227审31)',
                          '①已裁: 5孔=二三极同物(图例无独立5孔型号)'),
    '5孔插座带防水':      ('防水插座',   True,  'ZS02-B-061 单相二、三极防溅插座(227审7)',
                          '①推论+M5f裁定: 按带防水盖计; 注:符号未画图例规定的W'),
    '3孔插座':           ('厨电10A(符号)', True, '图例"3孔普通厨电插座250V,10A"; 清单无同名项',
                          '①已裁: 3孔单列不并类'),
    '冰箱插座':           ('冰箱插座',   True,  'ZS02-B-072 冰箱插座(227审4)', '④用途名单列'),
    '烤箱插座':           ('烤箱插座',   True,  'ZS02-B-070 蒸烤箱插座(227审1)', '④用途名单列'),
    '水槽插座':           ('水槽插座',   True,  '清单无同名项(计价时需定并入项)', '③照数(定位待橱柜深化)'),
    '烟机电源':           ('烟机电源',   True,  'ZS02-B-072 单相三极油烟机防溅插座(227审1)',
                          '③照数; 清单为独立列项故不单并入防水类'),
    '洗碗机电源':         ('洗碗机电源', True,  '清单无同名项(计价时需定并入项)', '③照数(定位待橱柜深化)'),
    '备用插座':           ('备用插座',   True,  '建议并入 ZS02-B-061 单相二、三极插座(227审31)',
                          '③照数; 非专用电器, 台面备用'),
    '除螨插座':           ('除螨插座',   True,  '清单无同名项', '④用途名单列'),
    '预留热水器插座':      ('热水器插座', True,  '清单无同名项(角阀见 ZS03-A-149)', '④用途名单列'),
    '预留热水器回水插座':  ('热水器回水插座', True, '清单无同名项', '④用途名单列'),
    '预留柜藏灯电源线':    ('柜藏灯带电源', True, '清单无同名项; 同类"镜柜灯带电源"封账已计4件',
                          '②选B: 灯带电源线计点(与既有口径自洽)'),
    '预留西厨柜藏光灯带电源线': ('柜藏灯带电源', True, '同上', '②选B: 灯带电源线计点'),
    # ↓ ②裁定: 不计插座点位, 另立 BOX_KIND 项
    '灯线盒':            (BOX_KIND, False, '新增 预留电源(227审0)', '②裁定: 盒类不计点位'),
    '插座线盒':          (BOX_KIND, False, '新增 预留电源(227审0)', '②裁定: 盒类不计点位'),
    '预留接线盒':        (BOX_KIND, False, '新增 预留电源(227审0)', '②裁定: 盒类不计点位'),
}


def scan(page):
    """取图面标注逐条: 文本层命中 RULES 键者, 记录真实字号/坐标/可见性。

    匹配必须**长名优先**: `5孔插座` 是 `5孔插座带防水` 的前缀, 按字典序遍历会把
    3 条带防水误并入二三极插座(实跑已复现)。
    """
    names = sorted(RULES, key=len, reverse=True)
    out, seen = [], set()
    for b in page.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                t = s['text'].replace(' ', '').replace('，', '').rstrip('。')
                if not t:
                    continue
                hit = next((k for k in names if t == k or t.startswith(k)), None)
                if not hit:
                    continue
                bb = s['bbox']
                key = (hit, round((bb[0] + bb[2]) / 2, 1), round((bb[1] + bb[3]) / 2, 1))
                if key in seen:                      # 同坐标重复对象去重(原报25→实24)
                    continue
                seen.add(key)
                out.append({'label': hit, 'text': t, 'size': round(s['size'], 2),
                            'bbox': [round(v, 1) for v in bb],
                            'cx': round((bb[0] + bb[2]) / 2, 1),
                            'cy': round((bb[1] + bb[3]) / 2, 1),
                            'visible': s['size'] >= MIN_VISIBLE_PT,
                            'leader': bool(RE_LEADER.search(t))})
    return out


def _room_and_dist(index, x, y):
    """生产判据 room_of 取名(墙装容差 12pt, 点到边); 另记是否**面内**与离边距作审计证据。

    面内(dist 为内部离墙距) 与 容差内(点在墙上/墙带, dist<=12pt) 语义不同, 必须区分。
    """
    name = index.room_of(x, y)
    if not name:
        return None, False, None
    poly = next((r['poly_pt'] for r in index.rooms if r['name'] == name), None)
    return name, _inpoly(x, y, poly), (round(_dist_poly(x, y, poly), 1) if poly else None)


def build():
    doc = fitz.open(DEFAULT_PDF)
    page = doc[PAGE]
    index, _calib = load_index(os.path.join(ART, 'm2_attribution.json'))
    segs = thin_segments(page)
    found = scan(page)

    # 带标高注记者需引线追踪; 直接标注者标签中心即符号位
    ann = [f for f in found if f['leader']]
    src = {}
    for f in ann:
        src.setdefault(f['label'], []).append([1, f['bbox']])
    tracked, _ = attribute_rows(src, segs, index.room_of) if ann else ([], [])
    # attribute_rows 按 label 分组遍历, 顺序不等于 ann 顺序 → 按锚点(标签中心)对齐
    tmap = {(r['anchor'][0], r['anchor'][1]): r for r in tracked}
    
    rows, excluded = [], []
    for f in sorted(found, key=lambda x: (x['label'], x['cy'], x['cx'])):
        kind, count, boq, basis = RULES[f['label']]
        if f['leader']:
            r = tmap.get((f['cx'], f['cy'])) or {}
            room, ep, ln, dist, in_face = (r.get('room', '未能归属'), r.get('endpoint'),
                                           r.get('len', 0), None, False)
        else:
            room, in_face, dist = _room_and_dist(index, f['cx'], f['cy'])
            room, ep, ln = room or '未能归属', None, 0
        rec = {'kind': kind, 'n': 1, 'anchor': [f['cx'], f['cy']], 'endpoint': ep, 'len': ln,
               'room': room, 'in_face': in_face, 'wall_dist': dist, 'bbox': f['bbox'],
               'label': f['label'], 'text': f['text'],
               'size': f['size'], 'visible': f['visible'], 'leader': f['leader'],
               'boq': boq, 'basis': basis, 'src': 'M5f补账'}
        (rows if count else excluded).append(rec)

    # 直接标注但落不进房间面者(如离面 >12pt 容差): 用**真实 bbox** 走同一追踪器再试,
    # 追通则标为注记(待核), 追不通如实挂账 —— 不猜位置。
    stranded = [r for r in rows + excluded if r['room'] == '未能归属' and not r['leader']]
    if stranded:
        src2 = {}
        for r in stranded:
            src2.setdefault(r['label'], []).append([1, r['bbox']])
        got, _ = attribute_rows(src2, segs, index.room_of)
        gmap = {(g['anchor'][0], g['anchor'][1]): g for g in got}
        for r in stranded:
            g = gmap.get(tuple(r['anchor']))
            if g and g['endpoint']:
                r.update(room=g['room'], endpoint=g['endpoint'], len=g['len'],
                         leader=True, in_face=False, note='文字位置未落域, 改取引线端点')

    n_in = sum(r['n'] for r in rows)
    assert n_in == 21, f'补账计入应为 21 条(24 命中 − 3 盒类), 实为 {n_in}'
    assert len(excluded) == 3, f'盒类另立应为 3 条, 实为 {len(excluded)}'
    assert len(rows) + len(excluded) == len(found) == 24, f'命中应 24 条, 实 {len(found)}'
    art = {
        'page': PAGE,
        'sealed_by': 'M5f 补账裁定 2026-10-11: ①并类(§7.2.1) + 带防水按防水盖计 + ②选B(盒类不计/灯带线计点) + ③照数',
        'm2_closed': 86, 'supplement_n': n_in, 'resealed': 86 + n_in,
        'visible_n': sum(1 for r in rows + excluded if r['visible']),
        'invisible_n': sum(1 for r in rows + excluded if not r['visible']),
        'rows': rows, 'excluded_box': excluded,
        'note': 'M2 封账 m2_counts_clean.json["18"]=86 一字未改; 本工件为并入后的重封账依据, '
                '恒等: Σ(m5_P08_rows) + Σ(本rows.n) ≡ 107 (build_mep_workbook 断言)。'
                '10 条 invisible 者文字 0.18pt 打印不可读但符号可见(m5e_5 接触表实证), 非幻影。',
    }
    return art


def main():
    art = build()
    out = os.path.join(ART, 'm5f_supplement_P08.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(art, f, ensure_ascii=False, indent=1)
    print(f"补账计入 {art['supplement_n']} 条(可见 {art['visible_n'] - sum(1 for r in art['excluded_box'] if r['visible'])}"
          f" / 不可见文字 {art['invisible_n'] - sum(1 for r in art['excluded_box'] if not r['visible'])}), "
          f"盒类另立 {len(art['excluded_box'])} 条 → 重封账 {art['m2_closed']} + {art['supplement_n']} = {art['resealed']}")
    print(f"{'类别':14s}{'图面标注':14s}{'字号':>6s} {'可见':4s} 房间归属        位置证据")
    for r in art['rows']:
        ev = (f"链{r['len']}pt" if r['leader'] else
              (f"面内离墙{r['wall_dist']}pt" if r['in_face'] else
               (f"墙容差内{r['wall_dist']}pt" if r['wall_dist'] is not None else '-')))
        print(f"{r['kind']:14s}{r['label']:14s}{r['size']:6.2f} "
              f"{'是' if r['visible'] else '✗':4s} {r['room']:14s}{ev}")
    print('\n另立项(不计插座点位):')
    for r in art['excluded_box']:
        print(f"  {r['label']:8s} @{r['anchor']} 字号{r['size']} 房间={r['room']} | {r['boq']}")
    print('\n→', out)


if __name__ == '__main__':
    main()
