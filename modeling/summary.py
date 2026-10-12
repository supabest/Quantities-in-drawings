"""modeling.summary — P5: 合并 P1~P4 为一张跨专业总算量表(几何权威 + 计算式)。

单一真源: 直接复用各部位模块的 rows() —— 地面/墙面/天花共享同一份 B.build() 栅格数据。
输出原则(用户指令): 数值一律几何自算、每行带计算式+尺寸追溯, 可独立复算审计。
BOQ 校验列仅开发期存在; 生产环境用 --no-check 一键剔除(数值不受影响)。

用法:
  ./venv/bin/python -m modeling.summary            # 含 BOQ 校验列(开发期)
  ./venv/bin/python -m modeling.summary --no-check # 生产形态: 无校验列
产出: outputs/model/TAKEOFF_SUMMARY.md
"""
import sys
import os
from . import base_extract as B
from . import quant_table as Q
from . import floor_takeoff as F
from . import wall_geometry as W
from . import ceiling_takeoff as C
from . import furniture_takeoff as FUR

OUTDIR = "outputs/model"
PRINCIPLE = (
    "**总原则**: 所有工程量数值均由几何从图纸独立算出, 每行附【几何计算式】+【尺寸追溯】, 可独立复算、可审计。\n"
    "BOQ 列仅在开发期用于交叉校验; **生产环境加 `--no-check` 即剔除校验/判定列, 数值不变。**")


def sections(data):
    return [
        ("地面 (P1)", F.room_rows(data)),
        ("墙面 (P2)", W.rows(data)),
        ("天花 (P3)", C.rows(data)),
        ("固定家具/柜台 (P4)", FUR.rows()),
    ]


def _subtotals(rows):
    m2 = sum(r["value"] for r in rows if r["unit"] == "m2" and isinstance(r["value"], (int, float)))
    m = sum(r["value"] for r in rows if r["unit"] == "m" and isinstance(r["value"], (int, float)))
    ge = sum(r["value"] for r in rows if r["unit"] == "个" and isinstance(r["value"], (int, float)))
    return m2, m, int(ge)


def _table(rows, include_check):
    if include_check:
        head = ["| 部位 | 列项 | 单位 | 几何计算式 | 数值 | 尺寸追溯 | BOQ校验 | 判定 |",
                "|---|---|:--:|---|---:|---|---:|---|"]
    else:
        head = ["| 部位 | 列项 | 单位 | 几何计算式 | 数值 | 尺寸追溯 |",
                "|---|---|:--:|---|---:|---|"]
    lines = head
    has_check = any(r.get("check") is not None for r in rows)
    for r in rows:
        base = "| %s | %s | %s | %s | %s | %s |" % (
            r["part"], r["item"], r["unit"], r["formula"],
            Q._fmt(r["value"], r["unit"]), r["trace"])
        if include_check:
            chk = ("%.2f" % r["check"]) if isinstance(r.get("check"), (int, float)) else "—"
            vd = Q.verdict(r["value"], r["check"]) if has_check else "—"
            base += " %s | %s |" % (chk, vd)
        else:
            base += ""
        lines.append(base)
    return lines


def build(include_check=True):
    data = B.build()
    secs = sections(data)
    lines = ["# 227户型 装修工程量计算表 · 总汇总 (几何权威)", "", PRINCIPLE, ""]

    # 专业汇总
    lines += ["## 专业汇总", ""]
    if include_check:
        lines += ["| 专业 | m² 小计 | m 小计 | 离散件(个) | 行数 |", "|---|---:|---:|---:|---:|"]
    else:
        lines += ["| 专业 | m² 小计 | m 小计 | 离散件(个) | 行数 |", "|---|---:|---:|---:|---:|"]
    tot = [0.0, 0.0, 0]
    for name, rows in secs:
        m2, m, ge = _subtotals(rows)
        tot[0] += m2
        tot[1] += m
        tot[2] += ge
        lines.append("| %s | %.2f | %.2f | %d | %d |" % (name, m2, m, ge, len(rows)))
    lines.append("| **合计** | **%.2f** | **%.2f** | **%d** | **%d** |" % (
        tot[0], tot[1], tot[2], sum(len(r) for _, r in secs)))
    lines.append("")
    lines.append("> 行级校验列的空值（—）不代表未校验: 地面按清单**分组**、墙面按**方向性**"
                 "（几何全墙≥BOQ局部饰面）校验, 详见 floor_takeoff.md / wall_geometry.md 文末。"
                 "天花行为**单层顶面投影**比对。")
    lines.append("")

    # 各专业明细
    for name, rows in secs:
        lines += ["## %s" % name, ""]
        lines += _table(rows, include_check)
        lines.append("")

    if not include_check:
        lines.append("> 生产形态: 已剔除 BOQ 校验/判定列; 数值全部来自几何计算式, 可直接用于计价。")
    return "\n".join(lines) + "\n", tot


def main():
    include_check = "--no-check" not in sys.argv
    md, tot = build(include_check)
    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, "TAKEOFF_SUMMARY.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    print("=" * 62)
    print("P5 全专业汇总 (校验列: %s)" % ("含" if include_check else "剔除/生产形态"))
    print("=" * 62)
    print("  m² 合计 %.2f | m 合计 %.2f | 离散件 %d 个" % (tot[0], tot[1], tot[2]))
    print("  产出 -> %s" % path)


if __name__ == "__main__":
    main()
