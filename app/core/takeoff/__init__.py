"""里程碑 A 算量引擎入口：参数映射、DXF 几何主源 run、重算清旧行。

模块级只允许 import stdlib / ezdxf / 本包子模块；ORM 只在
`clear_previous_measurements` / `reconcile_pinned` 函数内惰性导入，
`rows.to_measurement` 亦自行惰性导入。

`run()` 的挂载顺序与口径见设计稿 §2.2/§2.3/§2.7：
  选底图（里程碑 A 单 DXF：无 DXF / 多 DXF 无钉定一律空结果披露）→
  用**全局** roles 切平面簇 → 定稿底图 → 以簇范围重取局部 roles →
  围合建房间 → 五分部出数 → 项目级披露 → 对账。
"""
from __future__ import annotations

import json
import os
import re

import ezdxf

from . import coverage as _coverage
from . import enclosure, frames, layers, reconcile, takeoff
from .types import (  # noqa: F401
    Coverage, Issue, MISSING_FLOOR_HEIGHT_BANNER, Row, TakeoffParams,
    TakeoffResult,
)

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
                                "图纸 %s 找不到文件，请重新上传" % name,
                                {"file": filename}))
            continue
        try:
            doc = ezdxf.readfile(path)
        except Exception as exc:  # noqa: BLE001 - 坏图必须显式披露后跳过
            issues.append(Issue("dxf_unreadable", "error",
                                "图纸 %s 无法解析（文件损坏或非 DXF）" % name,
                                {"file": filename, "error": str(exc)}))
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
            if roles.audit_fallback:
                issues.append(Issue(
                    "audit_layer_fallback", "warn",
                    "图纸 %s 真墙层零命中，已回退审计层 %s 取墙线，请核对图层/口径"
                    % (name, "、".join(roles.audit_fallback)),
                    {"file": filename, "drawing": name,
                     "layers": roles.audit_fallback}))
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
            if roles.audit_fallback:
                # 几何来自审计层：量仍在，但来源需人工核对，不能显示成完整有源。
                for (space, part), cell in list(covered.cells.items()):
                    # 只降房间部位：洞口来自 DOOR/WINDOW 角色层，不经审计墙层回退。
                    if part in _coverage.ROOM_PARTS and cell.get("source") == "dxf":
                        covered.mark(space, part, "待确认", "audit_layer_fallback")
        except Exception as exc:  # noqa: BLE001 - 单图失败必须显式披露后跳过
            issues.append(Issue(
                "dxf_processing_failed", "error",
                "图纸 %s 几何处理失败，请检查图纸内容" % name,
                {"file": filename, "error": str(exc)}))
            continue

        # 逐图披露需能指认图纸：同一张坏图/残条在多图项目里不得只靠消息区分。
        for issue in row_issues:
            issue.detail.setdefault("drawing", name)
            issue.detail.setdefault("file", filename)
        issues.extend(row_issues)
        if divisions:
            # 角色层代理对象：受影响分部整批不出数；代理图元不可分解，不猜。
            rows = [r for r in rows if r.division_id not in divisions]
            parts = {1: "地面", 2: "墙面", 3: "天棚", 4: "洞口", 5: "踢脚"}
            affected = {parts[d] for d in divisions if d in parts}
            for space, part in list(covered.cells):
                if part in affected:
                    covered.mark(space, part, "无源", "t20_proxy")
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


_PIN_NOTE = "机器重算值 %s ≠ 钉定值 %s（本轮未覆盖，待复核）"
_PIN_MISSING = "钉定值缺失，机器值 %s 未覆盖（机器值已记入备注/pin，待人工核对）"
# 分隔符可能是我们补的 ASCII ';'，也可能是用户自己写的中文'；'/换行/空白；
# 提示总在末尾（由本模块追加），但用户可能在其后补写备注——所以两种位置都认。
_PIN_NOTE_RE = re.compile(
    r"(?:^|[；;\n])\s*(?:"
    r"机器重算值 [^；;\n]*≠ 钉定值 [^；;\n]*（本轮未覆盖，待复核）"
    r"|钉定值缺失，机器值 [^；;\n]* 未覆盖（机器值已记入备注/pin，待人工核对）)")


def _quantity(m):
    """Measurement 的工程量：m² 取 area、m 取 length、樘/个取 count。"""
    if m.unit in ("m2", "m²"):
        return m.area
    if m.unit == "m":
        return m.length
    return m.count


def _fmt_qty(value):
    text = "%.2f" % float(value)
    return text.rstrip("0").rstrip(".") if "." in text else text


def _load_json(text):
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _drop_pin_note(m):
    """剥掉上一轮的「本轮」提示与 pin 记录，保留用户其它备注。"""
    old = m.remarks or ""
    base = _PIN_NOTE_RE.sub("", old).strip(";； \t\r\n")
    if base != old.strip(";； \t\r\n"):
        m.remarks = base or None
    if m.data_json and "pin" in _load_json(m.data_json):
        data = _load_json(m.data_json)
        data.pop("pin")
        m.data_json = json.dumps(data, ensure_ascii=False) if data else None


def reconcile_pinned(project, machine_measurements):
    """钉定行整行冻结（规格 §2.2 用户裁定 A）：机器行不覆盖、不重复落库。

    本轮机器行与钉定行同条目（分部+名称+分类+单位）时丢弃**一条**机器行——
    钉定行已经代表该条目，两份并存会让清单翻倍；量不同时只写回钉定行
    remarks 与 data_json['pin']。提示是「本轮」口径：每轮先剥旧提示，量已
    一致或本轮没有对应机器行时连 data_json['pin'] 一起清掉，避免过期披露。

    两段式调用（幂等）：图片路径的房间行在 Phase 1 产好，而 2/3/5/6 派生分部
    在 Phase 3 才按 div1 房间行生成；若只在末尾对账，钉定房行与机器房行会各
    派生一套（清单翻倍）。process_project 在 Phase 3 前先对账一次（删掉房间
    双胞胎，派生只见钉定行），Phase 4 后再对账一次处理派生行。第二段会剥掉
    第一段的提示再按本轮机器行重写，结果一致（同一批对象），不依赖调用顺序。

    同名同分类可能对应多个真实房间（图片路径不追加 #comp_id，同名房间的
    position_desc 完全相同）。此时优先按 position_desc 精确定位；**匹配到多条
    或多条都无法定位时保留全部机器行**——宁可留重复，也不能因为一条钉定行
    删掉别的房间。注意 comp_id 是连通域扫描序号：改图后会重排，按名称匹配
    只能视为弱证据，需人工复核（里程碑 B 引入稳定房号后消除）。

    钉定行自身无量（area/length/count 为 None，可能来自手工清值）时不静默
    丢机器量：删机器行并把机器值写进 remarks 与 data_json['pin'] 待核对。

    返回本轮写入提示的条数。
    """
    from datetime import datetime

    from app import db
    from app.models import Measurement

    def key(m):
        return (m.division_id, m.label or "", m.category or "", m.unit or "")

    pinned = Measurement.query.filter_by(
        project_id=project.id, manual_pinned=True).all()
    if not pinned:
        return 0

    machine = {}
    for m in machine_measurements or []:
        machine.setdefault(key(m), []).append(m)

    changed = 0
    for m in pinned:
        _drop_pin_note(m)
        twins = machine.get(key(m)) or []
        if not twins:
            continue
        desc = m.position_desc or ""
        same_pos = [t for t in twins if desc and (t.position_desc or "") == desc]
        if len(same_pos) > 1:
            continue          # 同名同位置多条：无法分辨是哪间，不猜、不删
        if same_pos:
            twin = same_pos[0]
        elif len(twins) == 1:
            twin = twins[0]
        else:
            continue          # 同名多条且无法定位：不猜、不删
        machine_qty = _quantity(twin)
        if machine_qty is None:
            continue          # 机器行自身无量：无值可记，保留待人工核对
        if twin not in db.session.deleted:
            db.session.delete(twin)
        pinned_qty = _quantity(m)
        data = _load_json(m.data_json)
        if pinned_qty is None:
            note = _PIN_MISSING % _fmt_qty(machine_qty)
            base = m.remarks or ""
            m.remarks = (base + ";" + note) if base else note
            data["pin"] = {
                "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "by": "manual",
                "machine_recompute": machine_qty,
            }
            m.data_json = json.dumps(data, ensure_ascii=False)
            changed += 1
            continue
        if round(float(machine_qty), 2) == round(float(pinned_qty), 2):
            continue
        note = _PIN_NOTE % (_fmt_qty(machine_qty), _fmt_qty(pinned_qty))
        base = m.remarks or ""
        m.remarks = (base + ";" + note) if base else note
        data["pin"] = {
            "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "by": "manual",
            "machine_recompute": machine_qty,
        }
        m.data_json = json.dumps(data, ensure_ascii=False)
        changed += 1
    return changed
