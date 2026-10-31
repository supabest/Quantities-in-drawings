"""围合域的房间显示名与非房间残条判据（出数行与覆盖矩阵共用同一口径）。

· 无名围合域按 comp_id 出「未命名空间N」并标低置信（设计稿 §3.4），绝不编房间名；
· 同名房间追加 `#comp_id` 消歧（T9 覆盖矩阵口径），唯一名保持原样；
· 非房间残条 = **无名**且平均宽度 2A/P ≤ 250mm 的窄条（227 两块残条 0.46/1.14㎡
  均为 10cm 宽长条）。只按几何判，不依赖「图里有没有别的房间名」——部分房间
  漏标时，未命名的大房间仍是房间，照常出 2/3/5；有名字的域永远不是残条。
"""
from __future__ import annotations

import math
from collections import Counter

UNNAMED_PREFIX = "未命名空间"
RESIDUAL_MAX_WIDTH_MM = 250.0


def _perimeter_mm(poly):
    n = len(poly or [])
    if n < 3:
        return 0.0
    return sum(math.hypot(poly[(i + 1) % n][0] - poly[i][0],
                          poly[(i + 1) % n][1] - poly[i][1])
               for i in range(n))


def mean_width_mm(room):
    """2A/P 的平均宽度近似（mm）；无 poly 或面积为 0 时返回 0。"""
    per = _perimeter_mm(room.get("poly"))
    area = float(room.get("area") or 0.0) * 1e6
    if per <= 0 or area <= 0:
        return 0.0
    return 2.0 * area / per


def is_residual(room):
    """无名窄条不是房间：只计地面，不出天棚/墙面/踢脚。"""
    if room.get("names"):
        return False
    if len(room.get("poly") or []) < 3:
        return False
    width = mean_width_mm(room)
    return 0.0 < width <= RESIDUAL_MAX_WIDTH_MM


def base_name(room):
    names = [str(n) for n in (room.get("names") or []) if n]
    if names:
        return "+".join(names)
    return "%s%s" % (UNNAMED_PREFIX, room.get("comp_id"))


def name_counts(rooms):
    return Counter(base_name(r) for r in rooms)


def display_name(room, counts):
    name = base_name(room)
    if counts.get(name, 0) > 1:
        return "%s#%s" % (name, room.get("comp_id"))
    return name
