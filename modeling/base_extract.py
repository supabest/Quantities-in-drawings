"""modeling.base_extract — 从定稿底图(DXF)提取构件级建模数据。

Task 6 起本模块是**薄包装**：围合算法已搬入 `app.core.takeoff.enclosure`
（纯几何、只吃 LayerRoles），这里只负责
  1) 取线：按 227 固定窗口（±200mm 边距）调 `layers.resolve_roles`；
     显式传入 dxf 时先用 frames 切簇选底图，再以底图范围取线；
  2) 回填 p11 参考面积与含墙外包面积（吊顶标高由 build_rooms 按本图提取后挂好）；
  3) 保留 DXF/X0..Y1/V/_decode/SEALS/P11_NET/p11_ref/gross_area 等既有对外名字。

口径（沿用 _final2.py 定稿逻辑）：
  墙量 STRICT = WALL+S-WALL+FIN双线间墙 —— 造型/门扇/玻璃/反坎不计入墙
  围合 LOOSE  = WALL+S-WALL+方向FIN + 门窗洞口线 + 反坎 + 垭口分界(SEALS) + 门洞封开桥接
输出：房间(多边形+净面积+质心)、真墙段、洞口(门/窗)、竖向参数。
"""
import ezdxf
import re
import numpy as np

DXF = "装修图纸/227户型dxf/05.227户型平面图 （施工图纸） rev-33.dxf"
X0, Y0, X1, Y1 = 52161000, 227791000, 52186500, 227815500   # 母图带(含边距), 单位 mm
# 竖向参数(记忆确认)
V = {"story_height": 3170.0, "slab": 100.0, "floor_finish": 50.0, "ceiling_drop": 0.0}
V["clear_to_soffit"] = V["story_height"] - V["slab"] - V["floor_finish"]   # 板底净高 3020

SEALS = [(52176828, 227805421, 52179628, 227805421)]         # 卧室一北 2800 垭口地面分界(非墙)

# p11(平面砌筑图) 标注净面积(参考口径, 与几何围合互相独立); 单位 m2。
# 模型以几何为真值, p11 仅作对账参考列; 无对应标注的房间(如电梯厅/公共区)不在此表。
P11_NET = {
    "客厅/茶室": 65.6, "餐厅": 16.3, "玄关": 7.3,
    "主人卧室": 14.9, "主人衣帽间": 11.2, "主人浴室": 9.8,
    "卧室一": 10.8, "卧室二": 12.4, "中厨": 11.6, "生活阳台": 5.6,
    "浴室一": 4.7, "浴室二": 4.5, "浴室三": 5.6,
    "收纳间": 3.3, "储物间": 2.8, "过厅": 1.8,
}


def p11_ref(names):
    """按房间名匹配 p11 标注净面积; 合并房间取各标注名之和; 无任何匹配返回 None。"""
    vals = [P11_NET[n] for n in names if n in P11_NET]
    return round(sum(vals), 2) if vals else None


def gross_area(poly, walls):
    """含墙外包面积(最宽的合理口径): 用夹住房间四侧真墙的最外线定界。
    房间净轮廓边=墙内面; 向外 300mm 内取该侧墙最外侧线=外面。
    作为 p11 的上界: p11 若落在[净, 外包]之间→属净↔含墙口径差; 若超外包→真异常。
    无法定界时返回 None。"""
    if not poly or len(walls) == 0:
        return None
    P = np.array(poly)
    rx0, rx1, ry0, ry1 = P[:, 0].min(), P[:, 0].max(), P[:, 1].min(), P[:, 1].max()
    W = np.array(walls, dtype=float)
    vert = W[np.abs(W[:, 2] - W[:, 0]) < 40]
    horiz = W[np.abs(W[:, 3] - W[:, 1]) < 40]

    def outer(vals, face, sgn):   # sgn=-1 左/下取最小(外); +1 右/上取最大(外)
        sel = vals[(vals >= face - 300) & (vals <= face + 40)] if sgn < 0 \
            else vals[(vals >= face - 40) & (vals <= face + 300)]
        if len(sel) == 0:
            return None
        return float(sel.min() if sgn < 0 else sel.max())
    L = outer(vert[:, 0], rx0, -1); R = outer(vert[:, 0], rx1, 1)
    B = outer(horiz[:, 1], ry0, -1); T = outer(horiz[:, 1], ry1, 1)
    if None in (L, R, B, T):
        return None
    a = (R - L) * (T - B) / 1e6
    return round(a, 2) if a > 0 else None


def _decode(s):
    return re.sub(r"\\M\+5([0-9A-Fa-f]{4})", lambda m: bytes.fromhex(m.group(1)).decode("gbk"), s)


def _window():
    """227 固定窗口：栅格边界 + 取线 scope（与旧 _inb(p=200) 同口径）。"""
    return (X0, Y0, X1, Y1), (X0 - 200, Y0 - 200, X1 + 200, Y1 + 200)


def _base_window(doc, strict_enc):
    """显式给图时：切簇选底图，返回 (bounds, scope)。无底图 raise ValueError。"""
    from app.core.takeoff import frames
    from app.core.takeoff.layers import resolve_roles

    roles = resolve_roles(doc, strict_enc=strict_enc)
    base, issues = frames.pick_base(frames.cluster(doc, roles))
    if base is None:
        codes = ",".join(i.code for i in issues) or "no_geometry"
        raise ValueError("无法确定底图（issue: %s）" % codes)
    x0, y0, x1, y1 = base.bounds
    return base.bounds, (x0 - 200, y0 - 200, x1 + 200, y1 + 200)


def build(strict_enc=False, dxf=None, seals=None, params=None):
    """定稿底图的房间/墙段/洞口。

    两条入口的缺省值互不串味：
      dxf=None  走 227 固定窗口；seals 缺省 SEALS，params 缺省由模块 V 派生
                （结构层高 3170 / 板 100 / 找平 50），行为与 Task 6 前数值一致。
      dxf=path  先切簇选底图再按底图范围取线；seals 缺省 ()、params 缺省
                TakeoffParams()（不继承 227 的垭口分界与层高）。调用方可显式传入
                seals=... / params=... 覆盖。无底图时 raise ValueError。

    吊顶标高由 `enclosure.build_rooms` 按**传入 doc/bounds** 提取并挂到房间
    （T8 起不再是 227 专属：dxf 入口读本图的 +2xxx，也不再兜底覆盖）。
    """
    from app.core.takeoff.enclosure import build_rooms
    from app.core.takeoff.layers import resolve_roles
    from app.core.takeoff.types import TakeoffParams

    doc = ezdxf.readfile(dxf or DXF)
    if dxf is None:
        if seals is None: seals = SEALS
        if params is None:
            params = TakeoffParams(floor_height_mm=V["story_height"], slab_mm=V["slab"],
                                   floor_finish_mm=V["floor_finish"])
        bounds, scope = _window()
    else:
        if seals is None: seals = ()
        if params is None: params = TakeoffParams()
        bounds, scope = _base_window(doc, strict_enc)
    roles = resolve_roles(doc, strict_enc=strict_enc, scope_bounds=scope)
    data = build_rooms(doc, roles, bounds, params, seals=seals, strict_enc=strict_enc)
    # 挂 p11 参考面积(几何为真值, p11 仅对账) + 含墙外包面积(最宽口径上界)
    for r in data["rooms"]:
        r["p11"] = p11_ref(r["names"])
        r["gross_area"] = gross_area(r["poly"], data["walls_strict"]) if r["poly"] else None
    return data


if __name__ == "__main__":
    d = build()
    print("counts:", d["counts"])
    inn = [r for r in d["rooms"] if not r["is_out"]]
    print("户内空间 %d 个, 合计净面积 %.1f m2" % (len(inn), sum(r["area"] for r in inn)))
    for r in sorted(inn, key=lambda x: -x["area"]):
        print("  %-24s %6.2f  poly=%d点" % ("+".join(r["names"]), r["area"], len(r["poly"]) if r["poly"] else 0))
