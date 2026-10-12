"""modeling.wall_materials — P2b 立面墙面【材料清单】(非面积拆分)。

实测边界(见 docs/QUANTITY_TAKEOFF_MANUAL.md §4.4):
  ✅ 材料代号可读: 藏于立面 INSERT 块属性(tag=FB-01, 值=ST-03/WD-01/...)。
  ❌ 每种材料的分区面积: 立面无闭合填充面(FIN 是踢脚碎片/HATCH 是整面背景)、
     DIM 刻度重叠无法把代号唯一映射到某格 → 逐材料 m² 无几何依据, 不臆造。
故本模块只产出【可靠可交付】的一层: 按立面文件/视图簇列出
  “用到哪些材料代号 + 标注处数 + 类别名”, 并附该幅整面墙(ele-2 HATCH)面积作锚点。
数值 = 标注处数(计数), 非面积; 若确需面积须补可机读分区边界或人工。
"""
import os
import re
from collections import Counter, defaultdict
import ezdxf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELEV_DIR = os.path.join(ROOT, "装修图纸", "227户型dxf")
# 立面文件 → 图号(手册 §2.3: 文件级覆盖房间组, 视图级房间不可机读→待核)
ELEV_FILES = [
    ("06.", "E-01~06", "客厅/餐厅/走道收纳/公卫/厨房/生活阳台安全间"),
    ("07.", "E-07~13", "主卧/主卫/卧室一/卧室二/浴室一/浴室二"),
]
CODE = re.compile(r"\b(UP|WD|ST|TL|PT|WC|MT|GL|MR)\s*-?\s*\d{1,3}\b", re.I)
# 代号前缀 → 材料类别(手册 R8; 具体子码名如 ST-05 需物料表 p1, 标待核)
CAT = {
    "WD": "木饰面/木地板", "ST": "石材/岩板", "TL": "瓷砖", "PT": "乳胶漆/涂料",
    "WC": "墙布/壁纸", "UP": "扪皮/软包(硬包)", "MT": "金属", "GL": "玻璃", "MR": "镜",
}
VIEW_GAP = 8000     # mm, FB 代号按 X 间隙分“幅”(视图)
MIN_TAG_PER_VIEW = 3  # 少于该处数归“零星”
FACE_MIN, FACE_MAX = 3.0, 45.0  # m2, 整面墙 HATCH 面积窗(视图背景)


def _bbox_pts(h):
    xs, ys = [], []
    try:
        for p in h.paths:
            if p.type == 0:
                for v in p.vertices:
                    xs.append(v[0]); ys.append(v[1])
            elif p.type == 4:
                for e in p.edges:
                    for a in ("start", "end"):
                        if hasattr(e, a):
                            xs.append(getattr(e, a).x); ys.append(getattr(e, a).y)
            else:
                for e in p.edges:
                    for a in ("start", "end"):
                        pt = getattr(e, a, None)
                        if pt is not None:
                            try:
                                xs.append(pt.x); ys.append(pt.y)
                            except Exception:
                                pass
    except Exception:
        pass
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def load_tags(ms):
    """立面 FB 材料标注: (x, y, code)。"""
    tags = []
    for ins in ms.query("INSERT"):
        for a in ins.attribs:
            m = CODE.search(a.dxf.text or "")
            if m:
                tags.append((ins.dxf.insert.x, ins.dxf.insert.y, m.group(0).upper()))
    return tags


def load_faces(ms):
    """整面墙视图背景 (ele-2 大 HATCH): [(bbox, area_m2)]。"""
    faces = []
    for h in ms.query("HATCH"):
        if h.dxf.layer.lower() != "ele-2":
            continue
        bb = _bbox_pts(h)
        if not bb:
            continue
        area = (bb[2] - bb[0]) * (bb[3] - bb[1]) / 1e6
        if FACE_MIN <= area <= FACE_MAX:
            faces.append((bb, area))
    return faces


def cluster_views(tags):
    """按 X 间隙把标注分成“幅”(视图); 返回 [(xlo, xhi, [tags])]。"""
    if not tags:
        return []
    xs = sorted(tags, key=lambda t: t[0])
    views, cur = [], [xs[0]]
    for t in xs[1:]:
        if t[0] - cur[-1][0] > VIEW_GAP:
            views.append(cur); cur = []
        cur.append(t)
    views.append(cur)
    return [(c[0][0], c[-1][0], c) for c in views]


def face_for(faces, xlo, xhi):
    """找与该幅 X 区间重叠最多的整面墙面积(锚点)。"""
    best, area = None, 0.0
    cx = (xlo + xhi) / 2.0
    for bb, a in faces:
        if bb[0] - 2000 <= cx <= bb[2] + 2000:
            if a > area:
                best, area = bb, a
    return area


def file_rows(fidx, num, rooms, path):
    doc = ezdxf.readfile(path)
    ms = doc.modelspace()
    tags = load_tags(ms)
    faces = load_faces(ms)
    views = cluster_views(tags)
    rows = []
    real = [v for v in views if len(v[2]) >= MIN_TAG_PER_VIEW]
    misc = [t for v in views if len(v[2]) < MIN_TAG_PER_VIEW for t in v[2]]
    for k, (xlo, xhi, tg) in enumerate(real, 1):
        cc = Counter(c for _, _, c in tg)
        fa = face_for(faces, xlo, xhi)
        for code, n in sorted(cc.items(), key=lambda z: -z[1]):
            cat = CAT.get(code.split("-")[0], "?")
            rows.append({
                "part": "立面%s·幅%d" % (num, k),
                "item": "%s %s" % (code, cat),
                "unit": "处", "formula": "标注 %d 处(无分区边界→非面积)" % n,
                "value": n,
                "trace": "X[%d~%d]; 该幅整面墙≈%.1fm²%s" % (
                    xlo, xhi, fa, "" if fa else "(无整面HATCH)"),
            })
    if misc:
        cc = Counter(c for _, _, c in misc)
        for code, n in sorted(cc.items(), key=lambda z: -z[1]):
            cat = CAT.get(code.split("-")[0], "?")
            rows.append({"part": "立面%s·零星" % num, "item": "%s %s" % (code, cat),
                         "unit": "处", "formula": "标注 %d 处" % n, "value": n,
                         "trace": "未成幅(<%d处)" % MIN_TAG_PER_VIEW})
    return rows, len(tags), len(real)


def main():
    all_rows, grand = [], Counter()
    hdr = ["# 227户型 立面墙面 材料清单 (P2b · 代号可机读, 面积无依据)", "",
           "> 依据实测(手册§4.4): 材料代号=立面块属性可读; 分区面积=无闭合面/DIM重叠→**不臆造**。",
           "> 本表数值为**标注处数(材料出现计数)**, 非 m²。类别名据代号前缀(R8); 子码(如 ST-05)精确名需物料表, 标待核。", ""]
    for prefix, num, rooms in ELEV_FILES:
        path = None
        for f in os.listdir(ELEV_DIR):
            if f.startswith(prefix) and f.lower().endswith(".dxf"):
                path = os.path.join(ELEV_DIR, f); break
        if not path:
            hdr.append("## %s: 未找到文件\n" % num); continue
        rows, ntag, nview = file_rows(prefix, num, rooms, path)
        all_rows += rows
        hdr.append("## 文件 %s (%s) — %d 处材料标注, %d 个视图幅" % (num, rooms, ntag, nview))
        hdr.append("")
        hdr += ["| 幅 | 材料代号 类别 | 处数 | 追溯 |", "|---|---|---:|---|"]
        for r in rows:
            hdr.append("| %s | %s | %d | %s |" % (r["part"], r["item"], r["value"], r["trace"]))
        hdr.append("")
        for r in rows:
            grand[r["item"]] += r["value"]
    hdr.append("## 全户型材料汇总(按代号, 处数)")
    hdr.append("")
    hdr.append("| 材料 | 总处数 |")
    hdr.append("|---|---:|")
    for item, n in sorted(grand.items(), key=lambda z: -z[1]):
        hdr.append("| %s | %d |" % (item, n))
    out = os.path.join(ROOT, "outputs", "model", "wall_materials.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(hdr) + "\n")
    print("→", out)
    print("文件数 %d, 行 %d, 代号种类 %d" % (len(ELEV_FILES), len(all_rows), len(grand)))


if __name__ == "__main__":
    main()
