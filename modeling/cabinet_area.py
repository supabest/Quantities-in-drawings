"""modeling.cabinet_area — P4b: 固定柜体 m² 可提取性**诊断**(不臆造面积)。

多轮探查结论: 从 GD 固装大样自动提取**房间独有**的可靠柜体 m² **不可行**, 证据:
  · 5 张不同房间大样的 msp 可见 DIM 宽度集合**完全相同**({3223,2340,1800,1650,861,750})
    → 这些是各图共用的**图框/标题栏模板标注**, 非某房某柜的真实尺寸;
  · 卧室一(GD07) msp DIM=0 且块内可展开 DIM=0 → 房间独有柜尺寸**完全取不到**;
  · 柜高标注入块/被旋转, 视图 2D 散布且重复(平面+立面+镜像), 坐标系非 1:1 mm。
且 BOQ 固定柜**全按"个"计价**(见 furniture_takeoff), 清单口径本就无柜体/台面 m² 列项。

因此本模块只产出一份**可提取性诊断报告**(量化 msp/块内 DIM 数、跨图共有度), 明确标注
"柜体 m² 需人工读大样或走立面分区(P2b)", 不输出误导性的伪面积。

用法: ./venv/bin/python -m modeling.cabinet_area
产出: outputs/model/cabinet_area.md
"""
import os
import math
import ezdxf

BASE = "装修图纸/227户型dxf/"
OUTDIR = "outputs/model"
GD_FILES = {
    "开放区(客厅)": "12.GD01~05客厅固装大样图.dxf",
    "主卧+衣帽间": "13.GD06主卧固装大样图.dxf",
    "卧室一": "14.GD07卧室一固装大样图.dxf",
    "卧室二": "15.GD08卧室二固装大样图.dxf",
    "收纳间/阳台": "16.GD09~10储物间阳台柜固装大样图.dxf",
}
MIN_W = 600


def orient(e):
    try:
        a = e.dxf.defpoint
        b = e.dxf.defpoint2
        th = math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 180
        return min(th, 180 - th)
    except Exception:
        return None


def probe(path):
    doc = ezdxf.readfile(BASE + path)
    msp = doc.modelspace()
    n_msp = n_mspdim = n_blkdim = 0
    widths = []
    for e in msp.query("DIMENSION"):
        n_mspdim += 1
        m = e.get_measurement()
        if not m:
            continue
        o = orient(e)
        if o is not None and o < 20 and m >= MIN_W:
            widths.append(int(round(m)))
    for ins in msp.query("INSERT"):
        n_msp += 1
        try:
            for sub in ins.virtual_entities():
                if sub.dxftype() == "DIMENSION":
                    mm = sub.get_measurement()
                    if mm and mm >= MIN_W:
                        n_blkdim += 1
        except Exception:
            pass
    return sorted(set(widths)), n_mspdim, n_blkdim, n_msp


def main():
    stats = {}
    for room, f in GD_FILES.items():
        try:
            stats[room] = probe(f)
        except Exception as ex:
            stats[room] = ("ERR:%s" % ex, 0, 0, 0)
    # 跨图共有宽度: 出现在 >=2 张图的宽度 → 判定为模板共有(非房间独有)
    from collections import Counter
    freq = Counter()
    for room, (ws, *_ ) in stats.items():
        if isinstance(ws, list):
            for w in ws:
                freq[w] += 1
    common = sorted([w for w, c in freq.items() if c >= 2], reverse=True)

    lines = ["# 227户型 固定柜体 m² 可提取性诊断 (P4b)", ""]
    lines.append("**结论**: 柜体饰面 m² 无法从 GD 大样自动可靠提取 → 需人工读大样或走立面分区(P2b)。"
                 "且 BOQ 柜按\"个\"计价, 清单口径本无柜 m² 列项。")
    lines.append("")
    lines.append("| 房间(大样) | msp DIM数 | 可见≥600宽度集 | 块内可展开DIM | 判定 |")
    lines.append("|---|---:|---|---:|---|")
    for room, (ws, nm, nb, ni) in stats.items():
        wstr = ws if isinstance(ws, str) else ("、".join(map(str, ws)) or "空")
        uniq = ("" if isinstance(ws, str)
                else ("无可见柜标注" if not ws else
                      ("有独有" if any(w not in common for w in ws) else "全为跨图共有(模板)")))
        lines.append("| %s | %s | %s | %s | %s |" % (room, nm if not isinstance(ws,str) else "—", wstr, nb, uniq))
    lines.append("")
    lines.append("跨图共有宽度(≥2 图同现, 判为图框/标题栏模板而非房间柜尺寸): **%s**"
                 % ("、".join(map(str, common)) or "无"))
    lines.append("")
    lines.append("> 说明: 各房间大样 msp 可见宽度高度一致 + 卧室一完全取不到, 证实可见 DIM 属共用模板;"
                 " 房间独有柜尺寸封存于块内(块内 DIM 展开不可靠/为 0)。故 P4 维持\"按个 + 同墙面料\"为固装最终口径, "
                 "柜饰面精确 m² 交立面分区(P2b)或人工。")

    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, "cabinet_area.md")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")

    print("=" * 66)
    print("P4b 柜体可提取性诊断")
    print("=" * 66)
    for room, (ws, nm, nb, ni) in stats.items():
        print("  %-16s mspDIM=%-4d 块内DIM=%-4d 宽度集=%s" % (
            room, nm, nb, (ws if isinstance(ws, str) else ws)))
    print("  跨图共有宽度(=模板):", common)
    print("\n产出 -> %s" % path)


if __name__ == "__main__":
    main()
