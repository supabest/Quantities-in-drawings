"""里程碑 A 算量引擎入口：参数映射、DXF 几何主源 run、重算清旧行。

模块级只允许 import stdlib / ezdxf / 本包子模块；ORM 只在
`clear_previous_measurements` 函数内惰性导入，`rows.to_measurement` 亦自行惰性导入。

`run()` 的挂载顺序与口径见设计稿 §2.2/§2.3/§2.7：
  选底图（里程碑 A 单 DXF：无 DXF / 多 DXF 无钉定一律空结果披露）→
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

# 重算只清识别产物（设计稿 §2.2）：历史参照整批保留、钉定行整行冻结。
# PDF语义提取是机器每次重算都会重发的识别产物，不清就 10→20 翻倍。
CLEAR_TAGS = ("图纸实测", "几何推算", "经验补全", "逻辑推理", "待确认", "PDF语义提取")


def _num(value):
    """JSON 里的脏值（"abc"/None/对象）→ None；数字/数字串 → float。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _seals(raw):
    """seals 必须是 [[x0,y0,x1,y1], ...]；脏值整条丢弃，不得让下游迭代崩。"""
    out = []
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            continue
        nums = [_num(v) for v in item]
        if any(v is None for v in nums):
            continue
        out.append(tuple(nums))
    return out


def params_from_project(project):
    """Project 行 + takeoff_params JSON → TakeoffParams。

    层高列在 Project 上是**米**，换算成 mm；JSON 键 `slab_thickness_mm`
    与 `slab_mm` 都接受。板厚/地面完成厚度不设产品默认值（227 的
    100/50 是样本期参数，不得进入生产代码）。JSON 脏值按缺省处理，
    不抛错（否则整个重算一律 500）。
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

    height = _num(raw.get("floor_height_mm"))
    if height is None:
        default_h = _num(getattr(project, "default_floor_height", None))
        if default_h:
            height = default_h * 1000.0

    slab = _num(raw.get("slab_mm"))
    if slab is None:
        slab = _num(raw.get("slab_thickness_mm"))

    scale_count = raw.get("scale_count")
    if scale_count is None:
        scale_count = getattr(project, "scale_count", None)

    kwargs = dict(
        project_type=getattr(project, "project_type", "") or "",
        floor_height_mm=height,
        slab_mm=slab,
        floor_finish_mm=_num(raw.get("floor_finish_mm")),
        seals=_seals(raw.get("seals")),
        scale_count=scale_count,
    )
    # 只有显式给出才覆盖 TakeoffParams 的门/窗长期默认口径（2100/1500/0）。
    for key in ("door_height_mm", "window_height_mm", "window_sill_mm"):
        value = _num(raw.get(key))
        if value is not None:
            kwargs[key] = value
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
    """DXF 几何主源（Phase 1.2）。返回 TakeoffResult；不落库、不 commit。

    底图选取（里程碑 A 单 DXF）：无 DXF → no_dxf_base 空结果；有 is_base=True
    的图纸 → 只计量这些；多张且无钉定 → multi_dxf_unsupported 空结果。
    单图几何管线整体兜底：失败只废本图并披露 dxf_processing_failed。
    """
    params = params_from_project(project)
    issues = []
    all_rows = []
    seed_coverage = None

    # 层高闸门按**项目级**披露一次（不按图纸重复）：缺层高时无吊顶标高的房间
    # 墙面部不出数，逐间判定在 rows_for_divisions 内做。
    if params.requires_floor_height():
        issues.append(Issue(
            "param_required_floor_height", "error",
            "该业态无依据的默认层高，请填写结构净高/吊顶净高；"
            "无吊顶标高的房间墙面部不出数",
            {"project_type": params.project_type}))

    dxf_drawings = [d for d in drawings
                    if (getattr(d, "filename", "") or "").lower().endswith(".dxf")]
    if not dxf_drawings:
        issues.append(Issue(
            "no_dxf_base", "warn",
            "无矢量底图：第 1/2/3/5 分部不产几何行，全部量为待确认，禁止对外报价"))
        return TakeoffResult(rows=[], coverage=Coverage(), issues=issues)

    # 钉定 is_base=True 即人工指定底图文件：只计量这些。
    pinned = [d for d in dxf_drawings if getattr(d, "is_base", None) is True]
    if pinned:
        selected = pinned
    elif len(dxf_drawings) > 1:
        # 里程碑 A 单 DXF：不求和、不猜，交给人工钉定后重算。
        issues.append(Issue(
            "multi_dxf_unsupported", "error",
            "本项目有 %d 张 DXF，里程碑 A 仅支持单张底图；"
            "请钉定 Drawing.is_base 后重算" % len(dxf_drawings),
            {"files": [getattr(d, "filename", "") or "" for d in dxf_drawings]}))
        return TakeoffResult(rows=[], coverage=Coverage(), issues=issues)
    else:
        selected = dxf_drawings

    for drawing in selected:
        filename = getattr(drawing, "filename", "") or ""
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

        # 几何管线整体兜底：任何一步抛错只废掉本图（行不得部分入 all_rows），
        # 不得 500 拖垮整轮重算（C1）。
        try:
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

            rows, row_issues = takeoff.rows_for_divisions(data, params)
            covered = _coverage.build_coverage(data, params)
        except Exception as exc:  # noqa: BLE001 - 单图失败必须显式披露后跳过
            issues.append(Issue(
                "dxf_processing_failed", "error",
                "图纸 %s 几何处理失败：%s" % (name, exc),
                {"file": filename}))
            continue

        issues.extend(row_issues)
        if divisions:
            # 角色层代理对象：受影响分部整批不出数；代理图元不可分解，不猜。
            rows = [r for r in rows if r.division_id not in divisions]
        all_rows.extend(rows)

        if seed_coverage is None:
            seed_coverage = covered

    issues.extend(reconcile.apply(all_rows, params=params))
    return TakeoffResult(rows=all_rows,
                         coverage=seed_coverage or Coverage(),
                         issues=issues)


def clear_previous_measurements(project):
    """清掉上一轮识别产物；钉定行、历史参照整批保留。

    `PDF语义提取` 是每次重算都会重发的识别产物，同样清掉（否则重算会翻倍）；
    PDF 行被人工改过时应钉 `manual_pinned=True`，钉定保护优先。
    `db.session.delete` + `flush()`，不 commit（由 process_project 末尾统一提交，
    失败时由 api 的 rollback 保住旧行）。返回删除行数，便于调用方记日志。
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
