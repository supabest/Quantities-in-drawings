"""modeling.run_model — 建模总编排。

一次运行: 提取定稿几何 -> IFC2x3/IFC4 + glTF + DXF底图, 并打印房间清单与净面积对账。
用法: ./venv/bin/python -m modeling.run_model
"""
import json
from . import base_extract as B
from . import ifc_export, gltf_export, dxf_export

P11_NET_TOTAL = 188.2  # p11 标注净面积合计(地面/天花对账基准)


def main():
    data = B.build()
    outdir = "outputs/model"
    # 复用一次提取, 避免重复解析
    import os
    os.makedirs(outdir, exist_ok=True)
    r_ifc = {}
    for schema, fn in [("IFC2X3", "227_model_ifc2x3.ifc"), ("IFC4", "227_model_ifc4.ifc")]:
        m, meta = ifc_export.build(schema=schema, data=data)
        path = os.path.join(outdir, fn); m.write(path)
        r_ifc[schema] = {"path": path, "spaces": meta["n_space"], "walls": meta["n_wall"], **ifc_export._validate(path)}
    r_glb = gltf_export.export(data=data)
    r_dxf = dxf_export.export(data=data)

    inn = [r for r in data["rooms"] if not r["is_out"]]
    total = sum(r["area"] for r in inn)
    print("=" * 60)
    print("建模产物:")
    print(json.dumps({"ifc": r_ifc, "glb": r_glb, "dxf": r_dxf}, ensure_ascii=False, indent=2))
    print("=" * 60)
    print("户内空间 %d 间, 围合净面积合计 %.1f m2" % (len(inn), total))

    # ===== 面积对账: 主基准=清单BOQ(算量真实目标), p11 降为参考 =====
    from . import boq_ref
    rec = boq_ref.reconcile(data["rooms"])
    print("\n面积对账 — 模型 vs 清单BOQ (主基准):")
    print("  %-26s %8s %8s %8s  %s" % ("分组", "清单", "净", "含墙", "判定"))
    for row in rec:
        gs = ("%8.2f" % row["gross"]) if row["gross"] else "      --"
        print("  %-26s %8.2f %8.2f %s  %s" % (row["group"], row["boq"], row["clear"], gs, row["note"]))
    boq_flag = [r for r in rec if "查" in r["note"] or "未定界" in r["note"]]

    # p11 逐间参考(已降级: 仅供回溯, 不作算量依据)
    matched = [r for r in inn if r.get("p11")]
    unmatched = [r for r in inn if not r.get("p11")]

    # 复核清单(Markdown)
    md = ["# 227户型 面积对账复核清单\n",
          "**主基准 = 工程量清单(BOQ)楼面完成面积**——算量的真实目标, 采用清单自身的房间分组。p11 标注面积已降为参考(上一节曾误当作净面积, 实为最宽口径离群值)。\n",
          "模型三个几何口径: **净面积**(墙内面, 装饰面层用) / **含墙外包面积**(墙外面, 最宽合理口径, 通行开放区不定界时为 —) / **清单BOQ**。\n",
          "\n## 一、模型 vs 清单BOQ(主对账)\n",
          "\n| 分组 | 清单BOQ(m2) | 模型净(m2) | 模型含墙(m2) | 判定 |", "|---|---:|---:|---:|---|"]
    for row in rec:
        gs = ("%.2f" % row["gross"]) if row["gross"] else "—"
        md.append("| %s | %.2f | %.2f | %s | %s |" % (row["group"], row["boq"], row["clear"], gs, row["note"]))
    md.append("\n> 清单取数: " + "; ".join("%s=%s" % (r["group"], r["src"]) for r in rec) + "\n")

    md.append("\n## 二、p11 逐间参考(已降级, 不作依据)\n")
    md.append("\n| 房间 | 净(m2) | 含墙(m2) | p11(m2) |")
    md.append("|---|---:|---:|---:|")
    for r in sorted(matched, key=lambda x: x["area"], reverse=True):
        nm = "+".join(r["names"]) or "(无标注)"
        gs = ("%.2f" % r["gross_area"]) if r.get("gross_area") else "—"
        md.append("| %s | %.2f | %s | %.2f |" % (nm, r["area"], gs, r["p11"]))
    if unmatched:
        md.append("\n无 p11 参考: " + ", ".join(("+".join(r["names"]) or "(无标注)") + " %.1f" % r["area"] for r in unmatched) + "\n")

    md_path = os.path.join(outdir, "area_reconciliation.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    print("\nBOQ待核项 %d: %s" % (len(boq_flag), " | ".join(r["group"] + "(" + r["note"] + ")" for r in boq_flag)))
    print("复核清单 -> %s" % md_path)


if __name__ == "__main__":
    main()
