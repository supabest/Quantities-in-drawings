#!/usr/bin/env python
"""算量范围哨兵：仓库与代码只允许服务 227 户型（2026-10-12 用户裁定）。

钉四条（纯解析、零图纸、CI 可跑）：
  A 入库文件路径不得含范围外标识（工控/营销中心/售楼/样板房机电/别户型/20层）。
  B 入库 CAD 件（.dwg/.dxf/.dwl/.dwl2）文件名必须含 227。
  C 原始图纸目录 装修图纸/ 不入库（已 gitignore），误提交即红。
  D 代码里的**路径型字面量**不得指向范围外图纸。

D 的口径（重要，防止把哨兵写成只会误报的噪音）：
  "路径型字面量" = 含「装修图纸」或以图纸扩展名结尾的字符串。
  - 含「装修图纸/」的必须带 227（目录常量唯一化）；
  - 以图纸扩展名结尾的不得含范围外标识，也不得是 BANNED 历史数据源名。
  裸目录段（"装修图纸"）、裸后缀（".dxf"）、以及拼在 227 目录常量下的
  图纸文件名（"12.GD01~05客厅固装大样图.dxf"）都是合法写法，不判红。
  注释里的历史说明与 app/models.py 项目类型枚举"售楼部/展厅"（非路径）同样不管。
"""
import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CAD_EXT = (".dwg", ".dxf", ".dwl", ".dwl2")
DRAWING_EXT = CAD_EXT + (".pdf", ".zip", ".xlsx")
OUT_OF_SCOPE = ("工控", "营销中心", "售楼", "样板房机电", "20层",
                "135户型", "136户型", "210户型", "131户型", "99户型", "415板房")
# 已退出范围的历史数据源文件名（防被重新接回管线）
BANNED = ("electrical.dxf", "extracted_texts.tsv", "quantity_summary.json")


def _git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                       text=True, timeout=60)
    if r.returncode != 0:
        pytest.skip(f"git {' '.join(args)} 不可用: {r.stderr[:200]}")
    return r.stdout


TRACKED = [p for p in _git("ls-files", "-z").split("\0") if p]


def test_A_no_out_of_scope_file_is_tracked():
    hit = [p for p in TRACKED
           if any(tok in p for tok in OUT_OF_SCOPE) or p.split("/")[-1] in BANNED]
    assert not hit, f"范围外文件被入库（裁定仅 227 户型）: {hit}"


def test_B_tracked_cad_files_carry_227():
    bad = [p for p in TRACKED if p.lower().endswith(CAD_EXT) and "227" not in p]
    assert not bad, f"入库 CAD 件未带 227 标识: {bad}"


def test_C_raw_drawings_not_committed():
    hit = [p for p in TRACKED if p.startswith("装修图纸/")]
    assert not hit, f"原始图纸被提交入库（应仅本地存在）: {hit[:5]}"


@pytest.mark.parametrize("sub", ["modeling", "scripts", "app", "tests"])
def test_D_code_path_literals_stay_on_227(sub):
    bad = []
    for dp, _, fns in os.walk(os.path.join(ROOT, sub)):
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), ROOT)
            if rel == os.path.relpath(__file__, ROOT):
                continue          # 本哨兵自带禁用样本串，不扫自己
            text = open(os.path.join(dp, fn), encoding="utf-8", errors="ignore").read()
            for lit in re.findall(r"""['"]([^'"\n]{2,200})['"]""", text):
                low = lit.lower()
                if "装修图纸" not in lit and not low.endswith(DRAWING_EXT):
                    continue                          # 非路径型字面量，不管
                if "装修图纸/" in lit and "227" not in lit:
                    bad.append(f"{rel}: 目录常量未锁 227 -> {lit!r}")
                elif any(tok in lit for tok in OUT_OF_SCOPE) or low in BANNED:
                    bad.append(f"{rel}: 指向范围外图纸 -> {lit!r}")
    assert not bad, "发现非 227 的图纸路径字面量:\n  " + "\n  ".join(bad)
