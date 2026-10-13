# -*- coding: utf-8 -*-
"""scripts/build_static_site.py — 第 1 档：把 227 装修封账发布成静态站(Cloudflare Pages 可托管)。

纪律（与 xlsx/Web 同一套口径，不新算任何数）：
  1. **数据源 = 几何权威链**：行数据一律取 scripts/build_decoration_workbook.collect_rows()，
     并先过同一套恒等断言 seal_checks()；破账 → 不产出站点。
     不读 app.db：DB 是 Web 的运行时存储，交付物真源是几何链。
  2. **文案零复制**：口径与假设用 W.note_lines()，待确认统计用 W.pend_summary_lines()，
     挂账用 W.HANGOVER，规则表用 W.REMARK_RULES —— 模板里不另写一份话术。
  3. **页面与 Excel 同一份数**：直接复制 outputs/227户型装修工程量计算表.xlsx 进站点，
     并校验其主表是 73 行 × 12 列(表头 + 72 行)；防止"网页看的数"和"下载到的表"不是一份。
  4. 产物落 site/public/ 并**入库**：几何链依赖 装修图纸/(已 gitignore)，云端 CI 跑不动，
     所以发布走"本地构建 → 提交产物 → Pages 发布"，访问者侧不需要任何人的机器开机。

运行: venv/bin/python scripts/build_static_site.py [--out DIR]
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from jinja2 import Environment, FileSystemLoader, select_autoescape   # noqa: E402
from markupsafe import Markup, escape                                 # noqa: E402
from openpyxl import load_workbook                                    # noqa: E402

import build_decoration_workbook as W                                 # noqa: E402
from app.models import CONFIDENCE_LEVELS                              # noqa: E402
from seed_227_decoration import PROJECT_NAME, SEAL_NOTE               # noqa: E402

TPL_DIR = os.path.join(ROOT, "site", "templates")
ASSET_DIR = os.path.join(ROOT, "site", "assets")
OUT_DIR = os.path.join(ROOT, "site", "public")
XLSX_SRC = os.path.join(ROOT, "outputs", "227户型装修工程量计算表.xlsx")
XLSX_NAME = "227户型装修工程量计算表.xlsx"
CONF_LABEL = {"high": "高", "medium": "中", "low": "低"}


def em(text):
    """备注话术里的 `**词**` 只当"强调"渲染，其余字符一律转义。
    不改文案(xlsx 与本站共用同一串)，只改呈现。"""
    parts = str(text).split("**")
    html = escape(parts[0])
    for i in range(1, len(parts), 2):
        html += Markup("<strong>%s</strong>") % escape(parts[i])
        if i + 1 < len(parts):
            html += escape(parts[i + 1])
    return Markup(html)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit():
    try:
        out = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def verify_xlsx():
    """页面与 Excel 必须同一份：主表 73 行(表头+72) × 12 列，4 个 sheet 齐全。"""
    assert os.path.isfile(XLSX_SRC), "缺少 %s(先跑 build_decoration_workbook.py)" % XLSX_SRC
    wb = load_workbook(XLSX_SRC, read_only=True)
    assert wb.sheetnames == ["工程量计算表", "待确认与挂账清单", "天花分级明细P3b", "口径与假设"], \
        "xlsx sheet 结构与预期不符: %s" % wb.sheetnames
    ws = wb["工程量计算表"]
    rows = list(ws.iter_rows(values_only=True))
    assert len(rows) == W.SEAL["n_rows"] + 1, "xlsx 主表 %d 行 ≠ 封账 %d 行 + 表头" % (
        len(rows), W.SEAL["n_rows"])
    assert len(rows[0]) == 12, "xlsx 主表 %d 列，应 12 列(含备注列与需补资料)" % len(rows[0])
    assert rows[0][10] == "备注(不确定原因)", "xlsx 第 11 列不是备注列: %r" % rows[0][10]
    body_m2 = round(sum(r[6] for r in rows[1:] if r[4] == "m²"), 2)
    assert abs(body_m2 - W.SEAL["m2"]) < 0.05, "xlsx m² 合计 %.2f 偏离封账 %.2f" % (
        body_m2, W.SEAL["m2"])
    wb.close()
    return sha256(XLSX_SRC)


def view_rows(rows):
    """行 → 页面视图模型(与 xlsx build_main 同列序)。seq 是全表行号，跨页引用它。"""
    out = []
    for i, r in enumerate(rows, start=1):
        need = W.need_info(r)
        qty = round(r["value"], 2) if isinstance(r["value"], (int, float)) else r["value"]
        if r["unit"] == "个":
            qty = int(qty)
        v = {"seq": i, "div_id": r["division"][0],
             "div": "%d·%s" % r["division"],
             "part": r["part"], "item": r["item"],
             "unit": W.UNIT_SHOW.get(r["unit"], r["unit"]), "qty": qty,
             "formula": r["formula"], "trace": r["trace"],
             "states": " + ".join(r["states"]),
             "conf": r["conf"], "conf_label": CONF_LABEL[r["conf"]],
             "remark": r["remark"], "need": need,
             "codes": r["codes"]}
        v["search"] = " ".join(str(x) for x in
                               [r["part"], r["item"], r["formula"], r["trace"],
                                r["remark"].replace("**", ""), need,
                                " ".join(r["codes"])]).lower()
        out.append(v)
    return out


def rollup(view):
    """分部汇总：按行首次出现顺序(地→墙→顶→踢脚→固装)，与主表读序一致。"""
    agg = OrderedDict()
    for v in view:
        a = agg.setdefault(v["div_id"], {"id": v["div_id"], "name": v["div"].split("·", 1)[1],
                                         "rows": 0, "m2": 0.0, "m": 0.0, "ge": 0, "low": 0})
        a["rows"] += 1
        if v["unit"] == "m²":
            a["m2"] += v["qty"]
        elif v["unit"] == "m":
            a["m"] += v["qty"]
        else:
            a["ge"] += v["qty"]
        if v["conf"] == "low":
            a["low"] += 1
    for a in agg.values():
        a["m2"], a["m"] = round(a["m2"], 2), round(a["m"], 2)
    return list(agg.values())


def conf_buckets(view):
    out = {}
    for key in ("high", "medium", "low"):
        rs = [v for v in view if v["conf"] == key]
        meta = CONFIDENCE_LEVELS[key]
        out[key] = {"n": len(rs), "label": meta["label"], "icon": meta["icon"],
                    "color": meta["color"], "usage": meta["usage"],
                    "m2": round(sum(v["qty"] for v in rs if v["unit"] == "m²"), 2),
                    "m": round(sum(v["qty"] for v in rs if v["unit"] == "m"), 2),
                    "ge": sum(v["qty"] for v in rs if v["unit"] == "个")}
    return out


def build(out_dir):
    print("① 取行 + 恒等断言(破账不出站)…")
    rows, code_hits = W.collect_rows()
    m2, m, ge, _ = W.seal_checks(rows)
    print("   ✓ %d 行 ｜ 逐行求和 m² %.2f ｜ m %.2f ｜ 件 %d" % (len(rows), m2, m, ge))

    print("② 校验并复制 xlsx(网页与表同一份)…")
    xlsx_sha = verify_xlsx()

    view = view_rows(rows)
    raw_m2 = sum(r["value"] for r in rows if r["unit"] == "m2")
    # 三把口径同时摆出来，免得读者把末位舍入读成账实不符(口径详见 notes 页)：
    #  封账 763.12 = 各模块先求和再取 2 位显示 ｜ 未舍入真值 763.115
    #  逐行取 2 位后相加 763.13 = 本页表格/xlsx/库内回读得到的数
    row_m2 = round(sum(v["qty"] for v in view if v["unit"] == "m²"), 2)
    assert abs(row_m2 - W.SEAL["m2"]) <= 0.02, \
        "逐行取 2 位后相加 %.2f 与封账 %.2f 差超 0.02，舍入口径说明需重写" % (row_m2, W.SEAL["m2"])
    by_seq = {v["seq"]: v for v in view}
    seq_of = {id(r): v["seq"] for r, v in zip(rows, view)}
    pend_view = [by_seq[seq_of[id(r)]] for r in W.pend_sorted(rows)]
    ctx = {
        "active": "", "page_title": "总览",
        "xlsx_url": "./download/" + quote(XLSX_NAME),
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "git_commit": git_commit(),
        "n_rows": len(view),
        "seal_m2": "%.2f" % W.SEAL["m2"], "seal_m": "%.2f" % W.SEAL["m"],
        "seal_ge": W.SEAL["ge"],
        "row_m2": "%.2f" % row_m2, "row_m": "%.2f" % m, "row_ge": ge,
        "raw_m2": "%.3f" % raw_m2,
        "seal_note": SEAL_NOTE,
        "project_name": PROJECT_NAME,
        "xlsx_sha": sha256(XLSX_SRC)[:16],
        "source_sha": W.CZ.SOURCE_SHA256[:16],
        "divs": rollup(view),
        "confs": conf_buckets(view),
        "hangover": W.HANGOVER,
        "pend_total": len(pend_view),
        "rules": [{"code": ru["code"], "text": ru["text"], "state": ru["state"],
                   "conf": ru["conf"], "conf_label": CONF_LABEL[ru["conf"]],
                   "need": ru["need"], "hits": code_hits[ru["code"]]}
                  for ru in W.REMARK_RULES],
    }
    env = Environment(loader=FileSystemLoader(TPL_DIR, encoding="utf-8"),
                      autoescape=select_autoescape(["html"]), trim_blocks=True,
                      lstrip_blocks=True)
    env.filters["em"] = em

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, "assets"))
    os.makedirs(os.path.join(out_dir, "download"))
    shutil.copy2(os.path.join(ASSET_DIR, "style.css"),
                 os.path.join(out_dir, "assets", "style.css"))
    shutil.copy2(XLSX_SRC, os.path.join(out_dir, "download", XLSX_NAME))

    print("③ 渲染 4 页…")
    for name, tpl, extra in (
            ("index.html", "index.html", {"active": "index", "page_title": "总览",
                                          "notes": W.note_lines(code_hits)}),
            ("boq.html", "boq.html", {"active": "boq", "page_title": "工程量计算表",
                                      "rows": view}),
            ("pending.html", "pending.html", {"active": "pending", "page_title": "待确认与挂账",
                                              "pend": pend_view,
                                              "pend_lines": W.pend_summary_lines(rows, m2, m, ge)}),
            ("notes.html", "notes.html", {"active": "notes", "page_title": "口径与假设",
                                          "note_title": W.NOTE_TITLE,
                                          "notes": [ln[2:] if ln.startswith("■ ") else ln
                                                    for ln in W.note_lines(code_hits)]})):
        c = dict(ctx); c.update(extra)
        html = env.get_template(tpl).render(**c)
        _selfcheck(name, html, {"n_rows": len(view), "low": ctx["confs"]["low"]["n"],
                                "hangover": len(W.HANGOVER)})
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(html)
        print("   ✓ %-14s %6d B" % (name, len(html.encode("utf-8"))))

    with open(os.path.join(out_dir, "robots.txt"), "w", encoding="utf-8") as f:
        f.write("User-agent: *\nDisallow: /\n")     # 含造价数据，别让搜索引擎收走

    info = {"built_at": ctx["built_at"], "git_commit": ctx["git_commit"],
            "project": PROJECT_NAME, "n_rows": len(view),
            "row_totals": {"m2": m2, "m": m, "ge": ge},
            "seal": dict(W.SEAL), "code_hits": code_hits,
            "xlsx_sha256": xlsx_sha, "source_pdf_sha256": W.CZ.SOURCE_SHA256}
    with open(os.path.join(out_dir, "build-info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    print("④ 产物 -> %s ｜ download/%s (%d B)" % (
        out_dir, XLSX_NAME, os.path.getsize(os.path.join(out_dir, "download", XLSX_NAME))))
    return out_dir


def _selfcheck(name, html, expect):
    """产物自检：页面说的数必须和链算出来的一致，且不许漏 None/nan。"""
    assert "None" not in html, "%s 漏了 None(字段未兼容)" % name
    assert ">nan<" not in html and "nan m²" not in html, "%s 漏了 nan" % name
    assert "**" not in re.sub(r"<[^>]+>", "", html), \
        "%s 可见文本残留 markdown 星号(强调未渲染)" % name
    if name == "boq.html":
        n = (html.count('<tr class="high') + html.count('<tr class="medium')
             + html.count('<tr class="low'))
        assert n == expect["n_rows"], "boq.html 数据行 %d ≠ %d" % (n, expect["n_rows"])
        assert "<th>备注(不确定原因)</th>" in html, "boq.html 丢了备注列"
        assert html.count('<td class="r">') >= 2 * expect["n_rows"], "有行的备注/需补列未落值"
    if name == "index.html":
        assert ">%d<" % expect["low"] in html, "低置信 %d 行未落进卡片" % expect["low"]
        assert html.count('class="f">') >= expect["hangover"], "挂账表行数不足"
    if name == "pending.html":
        assert html.count("<tr>") >= expect["hangover"], "挂账未入页"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args()
    build(os.path.abspath(a.out))


if __name__ == "__main__":
    main()
