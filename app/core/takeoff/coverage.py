"""覆盖矩阵：逐空间×部位披露「有没有来源」，不给任何数值。

状态语义（T10/T12 复用，必须一致）：
  · 有源   = 几何/参数来源存在；**数值为 0 仍是 有源**（0 行≠缺行，T7 handoff）；
  · 无源   = 无 poly / 无来源；
  · 待参数 = 来源存在但必需参数缺失（不猜默认值）。

部位口径（与 takeoff.rows_for_divisions 的出数分部对应）：
  · 地面 / 天棚 / 踢脚：房间有围合 poly 即有源（面积/周长直接量出）；
  · 墙面：来源=围合边长×净高。requires_floor_height() 且房间无吊顶标高标准
    （ceiling_levels）→ 待参数；有吊顶标高证据或层高可解析 → 有源；
  · 洞口（项目级）：doors 或 windows 任一存在 → 有源；
  · 房间围合（项目级）：消费 T6/T8 handoff 的 data["unplaced_anchors"]——
    无未落房锚点 → 有源("anchors")；否则 待确认，source 列未落房锚点名
    （sorted 去重，逗号顿号皆可），不静默丢。

纯包：只 import .types；不得导入 flask / app.models / modeling。
"""
from __future__ import annotations

from .types import Coverage

PARTS = ("地面", "天棚", "墙面", "洞口", "踢脚")
PROJECT = "(项目)"
ROOM_PARTS = ("地面", "天棚", "墙面", "踢脚")


def _room_name(room):
    return "+".join(room.get("names") or []) or "(无标注)"


def _has_poly(room):
    return len(room.get("poly") or []) >= 3


def build_coverage(data, params):
    """data（T6/T7 的 rooms/doors/windows/unplaced_anchors）+ params → Coverage。"""
    cov = Coverage()
    need_h = params.requires_floor_height()
    for room in data.get("rooms") or []:
        if room.get("is_out"):
            continue
        name = _room_name(room)
        if not _has_poly(room):
            for part in ROOM_PARTS:
                cov.mark(name, part, "无源", None)
            continue
        cov.mark(name, "地面", "有源", "dxf")
        cov.mark(name, "天棚", "有源", "dxf")
        cov.mark(name, "踢脚", "有源", "dxf")
        has_levels = bool(room.get("ceiling_levels") or [])
        if need_h and not has_levels:
            cov.mark(name, "墙面", "待参数", None)
        else:
            cov.mark(name, "墙面", "有源", "ceiling_levels" if has_levels else "dxf")

    if data.get("doors") or data.get("windows"):
        cov.mark(PROJECT, "洞口", "有源", "dxf")
    else:
        cov.mark(PROJECT, "洞口", "无源", None)

    unplaced = data.get("unplaced_anchors") or []
    if unplaced:
        names = sorted({str(t) for _, _, t in unplaced})
        cov.mark(PROJECT, "房间围合", "待确认", "、".join(names))
    else:
        cov.mark(PROJECT, "房间围合", "有源", "anchors")
    return cov
