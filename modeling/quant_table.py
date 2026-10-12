"""modeling.quant_table — 全部位(地面/墙面/天花/固装)统一的算量输出骨架。

核心原则(用户指令): 生产环境无 BOQ, 工程量数值一律由几何从图纸独立算出,
每一项都要带【几何计算式】+【尺寸追溯】, 做到可独立复算、可审计。
BOQ 仅作开发期校验列与列项/计量单位模板, 绝不当数值来源。

行 schema(所有部位共用):
  part    部位/房间(如"主卧·北墙"、"生活阳台")
  item    列项(材料或构件名, 如"墙柱硬包饰面"、"玄关柜")
  unit    计量单位: m2(面) / m(线) / 个(离散件) —— 见 BOQ 三制
  formula 几何计算式(如 "3.20×2.82−0.90×2.10" / "Σ竖条: 5.20×3.10+2.30×1.50" / "识别计数×1")
  value   数值(由 formula 算出, 单位同 unit)
  trace   尺寸追溯(图号/标注来源, 如 "05平面·边A3 / E-03#DIM128" / "GD-06")
  check   (可选)BOQ 校验值, 有则填、无则 None; 生产环境整列剔除
"""
import os


def row(part, item, unit, formula, value, trace, check=None):
    return {"part": part, "item": item, "unit": unit, "formula": formula,
            "value": round(value, 3) if isinstance(value, (int, float)) else value,
            "trace": trace, "check": check}


def diff_pct(value, check):
    """几何值 vs BOQ 校验值的偏差%(check 为空则返回 None)。"""
    if check in (None, 0) or not isinstance(value, (int, float)):
        return None
    return round((value - check) / check * 100, 1)


def verdict(value, check, tol=5.0):
    """开发期校验判定(仅当有 BOQ check 时)。"""
    d = diff_pct(value, check)
    if d is None:
        return "—"
    if abs(d) <= tol:
        return "一致(%+.1f%%)" % d
    if abs(d) <= tol * 2:
        return "临界(%+.1f%%)→复核" % d
    return "偏差(%+.1f%%)→查几何/口径" % d


def write_md(path, title, note, rows, group_by="part"):
    """把行列表写成统一 Markdown 算量表(带计算式/追溯/校验列)。返回 path。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    md = ["# %s\n" % title, note + "\n",
          "\n| 部位 | 列项 | 单位 | 几何计算式 | 数值 | 尺寸追溯 | BOQ校验 | 判定 |",
          "|---|---|:--:|---|---:|---|---:|---|"]
    has_check = any(r.get("check") is not None for r in rows)
    for r in rows:
        chk = ("%.2f" % r["check"]) if isinstance(r.get("check"), (int, float)) else "—"
        vd = verdict(r["value"], r["check"]) if has_check else "—"
        md.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["part"], r["item"], r["unit"], r["formula"],
            _fmt(r["value"], r["unit"]), r["trace"], chk, vd))
    # 分单位小计
    md.append(_totals(rows))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    return path


def _fmt(v, unit):
    if isinstance(v, (int, float)):
        return ("%d" % v) if unit == "个" else ("%.2f" % v)
    return str(v)


def _totals(rows):
    parts = []
    for u in ("m2", "m"):
        tot = sum(r["value"] for r in rows if r["unit"] == u and isinstance(r["value"], (int, float)))
        if tot:
            parts.append("%s 合计 %.2f" % (u, tot))
    n_ge = sum(1 for r in rows if r["unit"] == "个")
    if n_ge:
        parts.append("离散件 %d 项" % n_ge)
    return "\n**" + " | ".join(parts) + "**" if parts else ""
