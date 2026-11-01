# -*- coding: utf-8 -*-
"""scripts/seed_227_decoration.py — 把 227 户型装修封账灌进 Web 应用(可直接在页面查看/导出)。

纪律：
  1. **不新算任何数**：行数据一律取自 scripts/build_decoration_workbook.collect_rows()
     (= modeling.summary 几何权威链 + 规则化备注)，并先过同一套恒等断言 seal_checks()；
     断言不过 → 不写库。
  2. **幂等**：按项目名定位，重跑先清旧行再灌，绝不累积成两套清单。
  3. **字段映射如实**：Measurement 既有语义一一映射(计算式→formula_text，追溯→position_desc，
     不确定原因→remarks，需补资料→data_json.need)；source_tag/confidence 按规则推导，
     不美化(低置信就是低置信)。项目特征列不编造材料名——图纸给不出就写"待X确认"。
  4. 只灌装修(地/墙/顶/踢脚/固装)；机电走独立线，本脚本不碰。

运行: venv/bin/python scripts/seed_227_decoration.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_decoration_workbook as W                                     # noqa: E402
from app import create_app, db                                            # noqa: E402
from app.models import Measurement, Project, User                          # noqa: E402

PROJECT_NAME = "227户型样板房 · 装修工程量(几何封账)"
SEAL_NOTE = ("封账基准：m² 763.12 ｜ m 166.73 ｜ 离散件 21 ｜ 72 行；"
             "数值全部由几何从图纸独立算出，每行带计算式与追溯")

# 227 样本竖向参数与 P1 地面封账分界：只属种子数据，不得进引擎常量。
# story 3170 / 板厚 100 / 地面完成面 50 → 净高 3020（enclosure._vertical）。
PROJECT_TYPE = "sample_flat"
DEFAULT_FLOOR_HEIGHT_M = 3.17
TAKEOFF_PARAMS = {
    "floor_height_mm": 3170,
    "slab_mm": 100,
    "floor_finish_mm": 50,
    "seals": [[52176828, 227805421, 52179628, 227805421]],
}

# 来源标签映射：三态 → app/models.SOURCE_TAGS 的六个既有标签(不新增标签)
def source_of(states, conf):
    if conf == "low":
        return "待确认", "❓"
    joined = " ".join(states)
    if "USER_RULING" in joined:
        return "逻辑推理", "📖"
    if conf == "high":
        return "图纸实测", "📐"
    return "几何推算", "🔢"


def features(row):
    """项目特征：只写图纸/大样能支撑的部分，缺依据就明确写"待确认"，不编造材料名。"""
    sec = row["section"]
    if sec.startswith("地面"):
        return "楼面完成面(面层材质待空间做法表确认，P04 做法表正文为图片不可机读)"
    if sec.startswith("天花"):
        return "天棚刮腻子/涂料基层；面层做法待做法表确认；投影口径(不含跌级展开)"
    if row["item"] == "踢脚线":
        return "踢脚线(材质待物料表/做法表确认)；按净周长扣门洞"
    if sec.startswith("墙面"):
        return "墙面抹灰/腻子/涂料基层(饰面材料分区见 wall_geometry 与立面图)"
    trace = row["trace"] or ""
    fin = [p for p in trace.split(";") if "饰面" in p]
    return ("固定家具/柜台；%s；柜体/台面展开面积待 P4b" % (fin[0].strip() if fin
                                                          else "饰面待大样确认"))


def to_measurement(row, idx, project_id):
    unit = W.UNIT_SHOW.get(row["unit"], row["unit"])
    remark = row["remark"]
    need = W.need_info(row)
    tag, icon = source_of(row["states"], row["conf"])
    if need:
        remark = "%s ｜ 需补充：%s" % (remark, need)
    m = Measurement(
        label=row["item"],
        division_id=row["division"][0],
        category=("wall" if row["section"].startswith("墙面") and row["unit"] == "m2"
                  else "skirting" if row["item"] == "踢脚线"
                  else "ceiling" if row["section"].startswith("天花")
                  else "floor" if row["section"].startswith("地面") else "fixture"),
        area=round(row["value"], 2) if row["unit"] == "m2" else None,
        length=round(row["value"], 2) if row["unit"] == "m" else None,
        count=int(row["value"]) if row["unit"] == "个" else None,
        unit=unit,
        project_id=project_id,
        source_tag=tag, source_tag_icon=icon,
        confidence_level=row["conf"],
        confidence_icon={"high": "🟢", "medium": "🟡", "low": "🔴"}[row["conf"]],
        position_desc="%s ｜ 追溯：%s" % (row["part"], row["trace"]),
        formula_text=row["formula"],
        project_features=features(row),
        remarks=remark,
        data_json=json.dumps({"section": row["section"], "codes": row["codes"],
                              "states": row["states"], "need": need,
                              "check": row.get("check"), "seq": idx,
                              "seal_note": SEAL_NOTE if idx == 1 else None},
                             ensure_ascii=False),
    )
    return m


def main():
    app = create_app()
    with app.app_context():
        print("① 取行 + 恒等断言(破账不写库)…")
        rows, code_hits = W.collect_rows()
        m2, m, ge, _ = W.seal_checks(rows)
        print("   ✓ %d 行 ｜ m² %.2f ｜ m %.2f ｜ 件 %d" % (len(rows), m2, m, ge))

        admin = User.query.filter_by(is_admin=True).order_by(User.id).first()
        assert admin is not None, "库里没有管理员账号，无法归属项目(先跑 flask 创建)"
        project = Project.query.filter_by(name=PROJECT_NAME).first()
        if project is None:
            project = Project(name=PROJECT_NAME, user_id=admin.id,
                              project_type=PROJECT_TYPE,
                              default_floor_height=DEFAULT_FLOOR_HEIGHT_M,
                              drawing_type="floor_plan", status="completed",
                              description=("227 户型(=210 户型别名)装修封账：地面 P1 / 墙面 P2 / "
                                           "天花 P3 + P3b 分级 / 固装 P4。数值全部几何自算，"
                                           "备注列逐行写明不确定原因与需补资料。"))
            db.session.add(project)
            print("   新建项目（待提交后取 id）")
        else:
            # 幂等：清旧行重灌，避免同一封账出现两套清单
            old = Measurement.query.filter_by(project_id=project.id).delete()
            print("   复用项目 #%d，清理旧清单 %d 行" % (project.id, old))
        # 竖向参数与封账 seal 每次校准：旧项目可能是 2.80 层高、无 seal 的版本。
        project.project_type = PROJECT_TYPE
        project.default_floor_height = DEFAULT_FLOOR_HEIGHT_M
        project.takeoff_params = json.dumps(TAKEOFF_PARAMS)
        project.status = "completed"
        db.session.flush()

        for i, r in enumerate(rows, start=1):
            db.session.add(to_measurement(r, i, project.id))
        db.session.commit()

        n = Measurement.query.filter_by(project_id=project.id).count()
        assert n == W.SEAL["n_rows"], "入库 %d 行 ≠ 封账 %d 行" % (n, W.SEAL["n_rows"])
        # 库内合计再核一遍(防字段映射把量放错列)
        db_m2 = sum((x.area or 0) for x in Measurement.query.filter_by(project_id=project.id))
        db_m = sum((x.length or 0) for x in Measurement.query.filter_by(project_id=project.id))
        db_ge = sum((x.count or 0) for x in Measurement.query.filter_by(project_id=project.id))
        assert abs(db_m2 - m2) < 0.5 and abs(db_m - m) < 0.5 and db_ge == ge, \
            "库内合计(m²%.2f/m%.2f/件%d) ≠ 封账(m²%.2f/m%.2f/件%d)" % (
                db_m2, db_m, db_ge, m2, m, ge)
        print("② ✓ 入库并回读核对：项目 #%d ｜ %d 行 ｜ m² %.2f ｜ m %.2f ｜ 件 %d" % (
            project.id, n, db_m2, db_m, db_ge))
        print("   Web: /project/%d 与 /project/%d/boq；导出 /project/%d/export.xlsx" % (
            project.id, project.id, project.id))
        print("   置信度分布：高 %d ｜ 中 %d ｜ 低 %d" % tuple(
            sum(1 for r in rows if r["conf"] == c) for c in ("high", "medium", "low")))
        print("   规则命中：%s" % " ".join("%s:%d" % (k, v) for k, v in
                                        sorted(code_hits.items())))


if __name__ == "__main__":
    main()
