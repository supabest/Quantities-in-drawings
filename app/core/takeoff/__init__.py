"""里程碑 A 算量引擎入口：参数映射、DXF 几何主源 run、重算清旧行。

模块级只允许 import stdlib / ezdxf / 本包子模块；ORM 只在
`clear_previous_measurements` 函数内惰性导入，`rows.to_measurement` 亦自行惰性导入。

`run()` 的挂载顺序与口径见设计稿 §2.2/§2.3/§2.7：
  用**全局** roles 切平面簇 → 定稿底图 → 以簇范围重取局部 roles →
  围合建房间 → 五分部出数 → 项目级披露 → 对账。
"""
from __future__ import annotations

import json
import os

import ezdxf

from . import coverage as _coverage
from . import enclosure, frames, layers, reconcile, takeoff
from .types import Coverage, Issue, Row, TakeoffParams, TakeoffResult  # noqa: F401

# 重算只清这五类识别产物（设计稿 §2.2）；历史参照/PDF语义提取/钉定行保留。
CLEAR_TAGS = ("图纸实测", "几何推算", "经验补全", "逻辑推理", "待确认")


def params_from_project(project):
    """Project 行 + takeoff_params JSON → TakeoffParams。

    层高列在 Project 上是**米**，换算成 mm；JSON 键 `slab_thickness_mm`
    与 `slab_mm` 都接受。板厚/地面完成厚度不设产品默认值（227 的
    100/50 是样本期参数，不得进入生产代码）。
    """
    raw = {}
    text = getattr(project, "takeoff_params", None)
    if text:
        try:
            loaded = json.loads(text)
        except (TypeError, ValueError):
            loaded = None
        if isinstance(loaded, dict):
            raw = loaded

    height = raw.get("floor_height_mm")
    if height is None:
        default_h = getattr(project, "default_floor_height", None)
        if default_h:
            height = float(default_h) * 1000.0

    slab = raw.get("slab_mm")
    if slab is None:
        slab = raw.get("slab_thickness_mm")

    scale_count = raw.get("scale_count")
    if scale_count is None:
        scale_count = getattr(project, "scale_count", None)

    kwargs = dict(
        project_type=getattr(project, "project_type", "") or "",
        floor_height_mm=height,
        slab_mm=slab,
        floor_finish_mm=raw.get("floor_finish_mm"),
        seals=[tuple(s) for s in (raw.get("seals") or [])],
        scale_count=scale_count,
    )
    # 只有显式给出才覆盖 TakeoffParams 的门/窗长期默认口径（2100/1500/0）。
    for key in ("door_height_mm", "window_height_mm", "window_sill_mm"):
        if raw.get(key) is not None:
            kwargs[key] = raw[key]
    return TakeoffParams(**kwargs)


def _unplaced_issue(unplaced):
    labels = sorted({str(t) for _, _, t in unplaced})
    anchors = [{"label": str(t), "x": round(float(x), 1), "y": round(float(y), 1)}
               for x, y, t in unplaced]
    return Issue(
        "unplaced_room_labels", "warn",
        "有房间标签未落到任何房间：%s（压墙线或连通域未成房）" % "、".join(labels),
        {"labels": labels, "anchors": anchors})


def run(project, drawings, upload_folder):
    """DXF 几何主源（Phase 1.2）。返回 TakeoffResult；不落库、不 commit。"""
    params = params_from_project(project)
    issues = []
    all_rows = []
    seed_coverage = None

    for drawing in drawings:
        filename = getattr(drawing, "filename", "") or ""
        if not filename.lower().endswith(".dxf"):
            continue
        name = getattr(drawing, "original_name", None) or filename
        path = os.path.join(upload_folder, filename)
        if not os.path.exists(path):
            issues.append(Issue("dxf_missing_file", "warn",
                                "图纸 %s 找不到文件：%s" % (name, path),
                                {"file": filename}))
            continue
        try:
            doc = ezdxf.readfile(path)
        except Exception as exc:  # noqa: BLE001 - 坏图必须显式披露后跳过
            issues.append(Issue("dxf_unreadable", "error",
                                "图纸 %s 无法解析：%s" % (name, exc),
                                {"file": filename}))
            continue

        report = layers.proxy_survey(doc)
        broken = set()
        for layer_name in report.proxies_by_layer:
            role = layers.role_of(layer_name)
            if role:
                broken.add(role)
        divisions = layers.affected_divisions(broken)
        if divisions:
            issues.append(Issue(
                "t20_proxy_on_role_layer", "error",
                "代理对象落在角色层，分部 %s 未计量" % sorted(divisions),
                {"layers": sorted(report.proxies_by_layer),
                 "divisions": sorted(divisions)}))

        # 切簇用全局 roles；定稿底图后必须按簇范围重取局部几何（防跨镜像副本）。
        global_roles = layers.resolve_roles(doc)
        base, base_issues = frames.pick_base(
            frames.cluster(doc, global_roles),
            pinned=getattr(drawing, "is_base", None))
        issues.extend(base_issues)
        if base is None:
            continue

        roles = layers.resolve_roles(doc, scope_bounds=base.bounds)
        data = enclosure.build_rooms(doc, roles, base.bounds, params,
                                     seals=params.seals)

        leaks = enclosure.detect_leaks(data)
        if leaks:
            issues.append(Issue(
                "enclosure_leak", "warn",
                "房间 %s 漏到图外（疑似垭口缺分界线），请核查围合"
                % "、".join(leaks),
                {"labels": leaks, "seals_applied": bool(params.seals)}))

        unplaced = data.get("unplaced_anchors") or []
        if unplaced:
            issues.append(_unplaced_issue(unplaced))

        if params.requires_floor_height():
            issues.append(Issue(
                "param_required_floor_height", "error",
                "该业态无依据的默认层高，请填写结构净高/吊顶净高",
                {"project_type": params.project_type}))

        rows, row_issues = takeoff.rows_for_divisions(data, params)
        issues.extend(row_issues)
        if params.requires_floor_height():
            # 无层高时墙面分母不存在：丢弃 div2 行，不拿兜底高度冒充。
            rows = [r for r in rows if r.division_id != 2]
        all_rows.extend(rows)

        if seed_coverage is None:
            seed_coverage = _coverage.build_coverage(data, params)

    issues.extend(reconcile.apply(all_rows, params=params))
    return TakeoffResult(rows=all_rows,
                         coverage=seed_coverage or Coverage(),
                         issues=issues)


def clear_previous_measurements(project):
    """清掉上一轮识别产物；钉定行、历史参照、PDF 语义行整行保留。

    `db.session.delete` + `flush()`，不 commit（由 process_project 末尾统一提交）。
    返回删除行数，便于调用方记日志。
    """
    from app import db
    from app.models import Measurement

    cleared = 0
    for m in Measurement.query.filter_by(project_id=project.id).all():
        if m.manual_pinned:
            continue
        if (m.source_tag or "") in CLEAR_TAGS:
            db.session.delete(m)
            cleared += 1
    db.session.flush()
    return cleared
