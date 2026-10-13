# -*- coding: utf-8 -*-
"""tests/test_static_site.py — 第 1 档静态站的产物契约。

分两层(与 test_decoration_delivery.py 同构)：
  Tier A  零图纸、CI 可跑：只读**已入库的 site/public/**，查页面结构、数值合计、
          强调渲染、以及"网页待确认顺序 == Excel 待确认 sheet 顺序"这类跨产物一致性。
  Tier B  @slow 且需图纸：真跑 build_static_site.build()，断言破账不产出、产物齐备、
          页面合计与 xlsx 合计互相对得上。

纪律：断言只认"页面里的数"而不是硬编码字符串清单；口径变了必须显式改测试。
"""
import io
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
PUB = os.path.join(ROOT, "site", "public")
DRAWINGS = os.path.join(ROOT, "装修图纸")
XLSX_NAME = "227户型装修工程量计算表.xlsx"


def read(name):
    with io.open(os.path.join(PUB, name), encoding="utf-8") as f:
        return f.read()


def data_rows(html):
    """boq.html 的数据行 → [(conf, div_id, unit, qty, 备注td, 需补td)]。"""
    out = []
    for m in re.finditer(r'<tr class="(high|medium|low)" data-div="(\d+)" data-conf="(\w+)"\s+'
                         r'data-unit="([^"]+)" data-qty="([\d.]+)"[^>]*>(.*?)</tr>',
                         html, re.S):
        tds = re.findall(r'<td[^>]*>(.*?)</td>', m.group(6), re.S)
        out.append((m.group(1), m.group(2), m.group(4), float(m.group(5)),
                    [re.sub(r"<[^>]+>", "", t).strip() for t in tds]))
    return out


# ─────────────────────────────── Tier A：零图纸，查已入库产物
def test_站点产物齐备():
    for f in ["index.html", "boq.html", "pending.html", "notes.html",
              "assets/style.css", "robots.txt", "build-info.json",
              os.path.join("download", XLSX_NAME)]:
        assert os.path.isfile(os.path.join(PUB, f)), "缺产物 site/public/%s" % f


def test_造价数据不进搜索引擎():
    assert "Disallow: /" in read("robots.txt")
    assert 'name="robots" content="noindex' in read("index.html")


def test_build_info_与封账同源():
    info = json.loads(read("build-info.json"))
    assert info["n_rows"] == 72
    assert info["seal"]["m2"] == 763.12 and info["seal"]["ge"] == 21
    # 页面表格里逐行相加的口径 = 763.13，与封账差 ≤0.02(末位舍入)，不是账实不符
    assert abs(info["row_totals"]["m2"] - info["seal"]["m2"]) <= 0.02
    assert info["code_hits"] and all(v > 0 for v in info["code_hits"].values()), \
        "有备注规则空转(命中 0 行)"


def test_boq_页面_72_行且每行都交代得清():
    rows = data_rows(read("boq.html"))
    assert len(rows) == 72, "boq.html 数据行 %d ≠ 72" % len(rows)
    html = read("boq.html")
    assert "<th>备注(不确定原因)</th>" in html and "<th>需补充的资料</th>" in html
    for conf, _d, _u, _q, tds in [(a, b, c, d, e) for a, b, c, d, e in rows]:
        assert tds[10], "备注列留空(未命中规则应显式写'无')"
        assert tds[11], "需补资料列留空(应显式写'无')"
    assert sum(1 for r in rows if r[0] == "low") == 10


def test_筛选后的合计必须回到封账():
    """页面 JS 用的是 data-qty，这里按同一口径重算，防止属性漏填/单位错列。"""
    rows = data_rows(read("boq.html"))
    m2 = round(sum(q for _c, _d, u, q, _t in rows if u == "m²"), 2)
    m = round(sum(q for _c, _d, u, q, _t in rows if u == "m"), 2)
    ge = int(round(sum(q for _c, _d, u, q, _t in rows if u == "个")))
    assert (m2, m, ge) == (763.13, 166.73, 21), "页面 data-qty 合计 %s/%s/%s" % (m2, m, ge)
    by_div = {}
    for _c, d, u, q, _t in rows:
        a = by_div.setdefault(d, [0.0, 0.0, 0])
        a[0 if u == "m²" else 1 if u == "m" else 2] += q
    assert round(by_div["1"][0], 2) == 163.76, "楼地面合计变了"
    assert round(by_div["2"][0], 2) == 437.22, "墙柱面合计变了"
    assert round(by_div["3"][0], 2) == 162.15, "天棚合计变了"
    assert round(by_div["5"][1], 2) == 166.73, "踢脚线合计变了"
    assert int(by_div["6"][2]) == 21, "固装件数变了"


def test_网页待确认顺序与_excel_sheet_完全一致():
    """跨产物一致性：pending.html 与 xlsx「待确认与挂账清单」必须同一批行、同一顺序。"""
    from openpyxl import load_workbook
    html = read("pending.html")
    web = re.findall(r'<tr class="(?:high|medium|low)">\s*<td class="num">(\d+)</td>', html)
    wb = load_workbook(os.path.join(PUB, "download", XLSX_NAME), read_only=True)
    ws = wb["待确认与挂账清单"]
    body = [r for r in ws.iter_rows(values_only=True)][1:]
    body = [r for r in body if isinstance(r[0], int)]
    wb.close()
    assert len(web) == len(body) == 58, "待确认行数 网页%d / Excel%d ≠ 58" % (len(web), len(body))
    # 网页给的是全表行号，Excel 给的是段内序号；比对(部位,列项,量)三元组
    xls = [(r[2], r[3], round(r[4], 2)) for r in body]
    webr = []
    for tr in re.findall(r'<tr class="(?:high|medium|low)">(.*?)</tr>', html, re.S):
        tds = [re.sub(r"<[^>]+>", "", c).strip()
               for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        webr.append((tds[2], tds[3], float(tds[4])))
    assert webr == xls, "网页与 Excel 的待确认顺序不一致"


def test_低置信行必须写明原因和需补资料():
    html = read("boq.html")
    trs = re.findall(r'<tr class="low"[^>]*>(.*?)</tr>', html, re.S)
    assert len(trs) == 10
    for tr in trs:
        tds = [re.sub(r"<[^>]+>", "", c).strip()
               for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        assert len(tds) == 12, "低置信行缺列"
        # 低置信就是"没交代清楚"，不允许多人读一句套话：必须非"无"且能读到具体缘由
        assert tds[10] != "无", "低置信行备注为空话(无)"
        assert len(tds[10]) >= 20, "低置信备注过短，不足以说明不确定原因: %s" % tds[10][:40]
        assert tds[11] not in ("", "无"), "低置信行必须给出需补充的资料"


def test_挂账七条全部入页():
    html = read("index.html") + read("pending.html")
    for key in ["墙面·无标注窄条", "天花·无标注块", "跌级侧带", "灯槽", "柜体/台面面积",
                "门窗洞口面积", "电梯厅 5.73m 开口性质"]:
        assert key in html, "挂账条目漏出: %s" % key


def test_强调渲染不吃字符():
    from build_static_site import em
    s = em("洞口扣减按**假设尺寸**(门高2.10m) 与 <script>")
    assert "<strong>假设尺寸</strong>" in s
    assert "2.10m" in s
    assert "<script>" not in str(s), "未转义"
    assert em("无") == "无"
    assert "**" not in str(s).replace("<strong>", "").replace("</strong>", "")


def test_可见文本不残留_markdown_星号():
    for f in ["index.html", "boq.html", "pending.html", "notes.html"]:
        vis = re.sub(r"<[^>]+>", "", read(f))
        assert "**" not in vis, "%s 可见文本残留 **" % f
        assert "None" not in read(f), "%s 漏 None" % f


# ─────────────────────────────── Tier B：需图纸，真跑构建
@pytest.mark.skipif(not os.path.isdir(DRAWINGS), reason="需本地图纸目录(不入库)")
@pytest.mark.slow
def test_重新构建的产物与入库版同一批数(tmp_path):
    import build_static_site as S
    out = S.build(str(tmp_path / "public"))
    rows = data_rows(io.open(os.path.join(out, "boq.html"), encoding="utf-8").read())
    assert len(rows) == 72
    assert round(sum(q for _c, _d, u, q, _t in rows if u == "m²"), 2) == 763.13
    # 重新生成的 xlsx 副本与仓库里那份内容一致(页面与表同一份数)
    from openpyxl import load_workbook
    a = load_workbook(os.path.join(out, "download", XLSX_NAME))["工程量计算表"]
    b = load_workbook(os.path.join(ROOT, "outputs", XLSX_NAME))["工程量计算表"]
    assert [r for r in a.iter_rows(values_only=True)] == \
           [r for r in b.iter_rows(values_only=True)], "站点内 xlsx 与 outputs 的不是一份"
