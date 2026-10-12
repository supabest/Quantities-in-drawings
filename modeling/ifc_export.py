"""modeling.ifc_export — 构件级 IFC 导出器 (IFC2X3 + IFC4)。

从 base_extract.build() 的定稿几何生成可被 Revit / 广联达导入的 IFC：
  - IfcSpace  房间体块(挤出到净高) + Pset(名称/净面积/净高)
  - IfcWall   真墙实体(按 STRICT 段挤出矩形截面) + Pset(长度/高/厚/类别)
纯 m.create_entity + 关键字参数(自补 GlobalId), 跨 schema 用属性名最稳;
单位=米, 坐标由 mm 换算并平移到局部原点(减母图左下角), 真实原点记入 Pset_ProjectInfo。
"""
import math
import ifcopenshell
import ifcopenshell.guid
from . import base_extract as B

S = 1.0 / 1000.0  # mm -> m

# 洞口高度参数(缺门窗表→逐樘高度/窗台高待补; 宽度取平面几何段长)
H_DOOR = 2100.0   # mm
H_WIN = 1500.0    # mm
SILL_DOOR = 0.0   # mm 门从地面起
SILL_WIN = 0.0    # mm 窗台高未知(缺图), 暂按 0
OPEN_TOL_ANG = 12.0   # deg 洞口与宿主墙平行容差
OPEN_TOL_DIST = 250.0  # mm 洞口中心到墙线垂距容差(内面与 strict 面基本共线)


def _r(m, cls, **a):   # root entity: 自动补 GlobalId
    a.setdefault("GlobalId", ifcopenshell.guid.new())
    return m.create_entity(cls, **a)


def _n(m, cls, **a):   # non-root entity
    return m.create_entity(cls, **a)


def _pt(m, c): return _n(m, "IfcCartesianPoint", Coordinates=[float(v) for v in c])
def _ax(m, loc=(0, 0, 0)): return _n(m, "IfcAxis2Placement3D", Location=_pt(m, loc))


def _pset(m, el, name, props):
    ps = []
    for k, v in props.items():
        if v is None:
            val = m.createIfcLabel("N/A")
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            val = m.createIfcReal(float(v))
        else:
            val = m.createIfcLabel(str(v))
        ps.append(_n(m, "IfcPropertySingleValue", Name=k, NominalValue=val))
    pset = _r(m, "IfcPropertySet", Name=name, HasProperties=ps)
    _r(m, "IfcRelDefinesByProperties", RelatedObjects=[el], RelatingPropertyDefinition=pset)


def _body(m, ctx, el, pts_m, z_m, height_m):
    pts = list(pts_m)
    if pts[0] != pts[-1]:
        pts.append(pts[0])   # IfcArbitraryClosedProfileDef 要求首尾闭合
    poly = _n(m, "IfcPolyline", Points=[_pt(m, (px, py)) for px, py in pts])
    prof = _n(m, "IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=poly)
    solid = _n(m, "IfcExtrudedAreaSolid", SweptArea=prof, Position=_ax(m, (0, 0, z_m)),
               ExtrudedDirection=_n(m, "IfcDirection", DirectionRatios=[0.0, 0.0, 1.0]), Depth=float(height_m))
    shape = _n(m, "IfcShapeRepresentation", ContextOfItems=ctx, RepresentationIdentifier="Body",
               RepresentationType="SweptSolid", Items=[solid])
    el.Representation = _n(m, "IfcProductDefinitionShape", Representations=[shape])


def _place(m, rel_to, x, y, z):
    return _n(m, "IfcLocalPlacement", PlacementRelTo=rel_to, RelativePlacement=_ax(m, (x, y, z)))


def _rect_pts(x0, y0, x1, y1, t):
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy) or 1.0
    ox, oy = -dy / L * t / 2.0, dx / L * t / 2.0
    return [(x0 + ox, y0 + oy), (x1 + ox, y1 + oy), (x1 - ox, y1 - oy), (x0 - ox, y0 - oy)]


def _ang(x0, y0, x1, y1):
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0


def _ang_diff(a, b):
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _perp_dist(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L2))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def _host_wall(walls, x0, y0, x1, y1):
    """给洞口找宿主墙: 洞口正位于墙线缺口处, 故用**无限直线垂距**+**沿墙投影重叠**(放宽一个洞口长)。
    返回 list 下标或 None。"""
    am = _ang(x0, y0, x1, y1)
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    margin = math.hypot(x1 - x0, y1 - y0)          # 缺口在墙端时允许重叠到墙外一个洞长
    best, bd = None, 1e18
    for i, (wx0, wy0, wx1, wy1) in enumerate(walls):
        if (wx0, wy0) == (wx1, wy1):
            continue
        if _ang_diff(am, _ang(wx0, wy0, wx1, wy1)) > OPEN_TOL_ANG:
            continue
        wdx, wdy = wx1 - wx0, wy1 - wy0
        wl = math.hypot(wdx, wdy) or 1.0
        ux, uy = wdx / wl, wdy / wl
        # 开口中心到墙无限直线的垂距(叉积)
        perp = abs((mx - wx0) * uy - (my - wy0) * ux)
        if perp > OPEN_TOL_DIST:
            continue
        # 开口两端沿墙投影与墙跨[0,wl]是否重叠(含 margin)
        p0 = (x0 - wx0) * ux + (y0 - wy0) * uy
        p1 = (x1 - wx0) * ux + (y1 - wy0) * uy
        lo, hi = min(p0, p1), max(p0, p1)
        if hi < -margin or lo > wl + margin:
            continue
        if perp < bd:
            bd, best = perp, i
    return best


def build(schema="IFC4", data=None):
    data = data or B.build()
    ox, oy = data["bounds"][0], data["bounds"][1]
    V = data["V"]
    clear = V["clear_to_soffit"] * S
    floor_top = V["floor_finish"] * S

    m = ifcopenshell.file(schema=schema)
    ctx = _n(m, "IfcGeometricRepresentationContext", ContextType="Model",
             CoordinateSpaceDimension=3, Precision=1e-5, WorldCoordinateSystem=_ax(m))
    units = _n(m, "IfcUnitAssignment", Units=[
        _n(m, "IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE"),
        _n(m, "IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE"),
        _n(m, "IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE"),
        _n(m, "IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN")])
    project = _r(m, "IfcProject", Name="227户型装修模型", RepresentationContexts=[ctx], UnitsInContext=units)
    site = _r(m, "IfcSite", Name="227户型", ObjectPlacement=_place(m, None, 0, 0, 0))
    bld = _r(m, "IfcBuilding", Name="单户", ObjectPlacement=_place(m, site.ObjectPlacement, 0, 0, 0))
    storey = _r(m, "IfcBuildingStorey", Name="L1", Elevation=0.0,
                ObjectPlacement=_place(m, bld.ObjectPlacement, 0, 0, 0))
    _r(m, "IfcRelAggregates", RelatingObject=project, RelatedObjects=[site])
    _r(m, "IfcRelAggregates", RelatingObject=site, RelatedObjects=[bld])
    _r(m, "IfcRelAggregates", RelatingObject=bld, RelatedObjects=[storey])

    def loc(cx, cy): return (cx - ox) * S, (cy - oy) * S

    def contain(el):
        _r(m, "IfcRelContainedInSpatialStructure", RelatedElements=[el], RelatingStructure=storey)

    n_sp = n_wl = 0
    wall_objs = {}
    for r in data["rooms"]:
        if r["is_out"] or not r["poly"]: continue
        nm = "+".join(r["names"]) or "(无标注)"
        soffit = float(r.get("soffit_mm", V["clear_to_soffit"]))   # 逐间主吊顶净高(缺则结构3020)
        kw = dict(Name=nm, ObjectPlacement=_place(m, storey.ObjectPlacement, *loc(r["cx"], r["cy"]), floor_top))
        if schema != "IFC2X3":
            kw["PredefinedType"] = "SPACE"
        el = _r(m, "IfcSpace", **kw)
        _body(m, ctx, el, [loc(x, y) for x, y in r["poly"]], floor_top, (soffit - V["floor_finish"]) * S)
        contain(el)
        p11 = r.get("p11")
        var = round((r["area"] - p11) / p11 * 100.0, 1) if p11 else None
        _pset(m, el, "Pset_Room", {"Name": nm, "NetArea_m2": float(r["area"]),
                                   "GrossArea_m2": (float(r["gross_area"]) if r.get("gross_area") else None),
                                   "P11NetArea_m2": (float(p11) if p11 else None),
                                   "AreaVariance_pct": var,
                                   "ClearHeight_mm": soffit,
                                   "StructuralHeight_mm": float(V["clear_to_soffit"]),
                                   "CeilingLevels": ("/".join("+%d" % int(v) for v in r.get("ceiling_levels", [])) or "N/A"),
                                   "FloorFinish_mm": float(V["floor_finish"])})
        n_sp += 1

    for i, (x0, y0, x1, y1) in enumerate(data["walls_strict"]):
        if (x0, y0) == (x1, y1): continue
        t = 100.0
        el = _r(m, "IfcWall", Name="真墙", ObjectPlacement=_place(m, storey.ObjectPlacement, 0, 0, 0))
        pts = [(px * S, py * S) for px, py in _rect_pts(x0 - ox, y0 - oy, x1 - ox, y1 - oy, t)]
        _body(m, ctx, el, pts, 0.0, clear)
        contain(el)
        _pset(m, el, "Pset_Wall", {"Length_mm": round(math.hypot(x1 - x0, y1 - y0), 1),
                                   "Height_mm": float(V["clear_to_soffit"]),
                                   "Thickness_mm": t, "Category": "STRICT"})
        n_wl += 1
        wall_objs[i] = el

    # ---- 洞口(门/窗): 按房间围合边(与算量同口径)得逐樘区间, 去重后开洞 ----
    from . import wall_geometry as WG
    walls_segs = data["walls_strict"]
    cand = []   # (cx,cy,kind,(wx0,wy0,wx1,wy1),width,hh,sill)
    for r in data["rooms"]:
        if r["is_out"] or not r.get("poly"):
            continue
        for e in WG._edges(r["poly"]):
            x0, y0, x1, y1, L = e
            if L < 100:
                continue
            dx, dy = (x1 - x0) / L, (y1 - y0) / L
            for segs, kind, hh, sill, par in ((data["doors"], "门", H_DOOR, SILL_DOOR, False),
                                              (data["windows"], "窗", H_WIN, SILL_WIN, True)):
                for a, b in WG.opening_intervals(segs, e, parallel_only=par):
                    wx0, wy0 = x0 + dx * a, y0 + dy * a
                    wx1, wy1 = x0 + dx * b, y0 + dy * b
                    cand.append(((wx0 + wx1) / 2, (wy0 + wy1) / 2, kind,
                                 (wx0, wy0, wx1, wy1), b - a, hh, sill))
    # 去重: 共享墙会在相邻两间各命中一次; 同一物理洞口按中心距+同类合并
    DEDUP = 300.0   # mm
    uniq = []
    for c in cand:
        if not any(u[2] == c[2] and math.hypot(u[0] - c[0], u[1] - c[1]) < DEDUP for u in uniq):
            uniq.append(c)

    n_op = n_door = n_win = n_orphan = 0
    op_area = 0.0
    for (cx, cy, kind, seg, width, hh, sill) in uniq:
        wx0, wy0, wx1, wy1 = seg
        hi = _host_wall(walls_segs, wx0, wy0, wx1, wy1)
        host = wall_objs.get(hi) if hi is not None else None
        op = _r(m, "IfcOpeningElement", Name=kind + "洞",
                ObjectPlacement=_place(m, storey.ObjectPlacement, 0, 0, 0))
        pts = [(px * S, py * S) for px, py in _rect_pts(wx0 - ox, wy0 - oy, wx1 - ox, wy1 - oy, 200.0)]
        _body(m, ctx, op, pts, sill * S, hh * S)   # 厚 200mm 足以贯穿 100 墙
        if host is not None:
            _r(m, "IfcRelVoidsElement", RelatingBuildingElement=host, RelatedOpeningElement=op)
        else:
            n_orphan += 1
        _pset(m, op, "Pset_Opening", {"Category": kind, "Width_mm": round(width, 1),
                                      "Height_mm": hh, "Sill_mm": sill,
                                      "HostWall": ("#" + str(host.id())) if host is not None else "NO_HOST"})
        n_op += 1
        op_area += width * hh * S * S
        if kind == "门":
            n_door += 1
        else:
            n_win += 1

    _pset(m, project, "Pset_ProjectInfo", {"RealOriginX_mm": float(ox), "RealOriginY_mm": float(oy),
                                           "StoryHeight_mm": float(V["story_height"]), "Slab_mm": float(V["slab"])})
    return m, {"real_origin": [ox, oy], "n_space": n_sp, "n_wall": n_wl,
               "n_opening": n_op, "n_door": n_door, "n_win": n_win,
               "n_orphan": n_orphan, "opening_area_m2": round(op_area, 2)}


def _validate(path):
    """用 ifcopenshell.geom 实体化 + trimesh 算体积, 确认 Revit/广联达能看到有效实体。"""
    try:
        import ifcopenshell.geom as geom
        import trimesh
        import numpy as np
        settings = geom.settings()
        f = ifcopenshell.open(path)
        n = 0; vol = 0.0
        for el in f.by_type("IfcSpace") + f.by_type("IfcWall"):
            try:
                shp = geom.create_shape(settings, el)   # 必须持有 Shape 引用, 否则底层网格内存被释放
                g = shp.geometry
                V = getattr(g, "verts", None); F = getattr(g, "faces", None)
                if V is None: V = g.vertex_coords
                if F is None: F = g.face_indices
                tm = trimesh.Trimesh(np.array(V).reshape(-1, 3), np.array(F).reshape(-1, 3), process=False)
                if tm.volume:
                    n += 1; vol += abs(tm.volume)
            except Exception:
                pass
        return {"solids_ok": n, "total_vol_m3": round(vol, 1)}
    except Exception as e:
        return {"geom_err": str(e)[:100]}


def export_all(outdir="outputs/model"):
    import os
    os.makedirs(outdir, exist_ok=True)
    data = B.build()
    res = {}
    for schema, fn in [("IFC2X3", "227_model_ifc2x3.ifc"), ("IFC4", "227_model_ifc4.ifc")]:
        m, meta = build(schema=schema, data=data)
        path = os.path.join(outdir, fn)
        m.write(path)
        chk = ifcopenshell.open(path)  # 回读校验
        v = _validate(path)
        res[schema] = {"path": path, "bytes": os.path.getsize(path),
                       "spaces": len(chk.by_type("IfcSpace")), "walls": len(chk.by_type("IfcWall")),
                       "openings": len(chk.by_type("IfcOpeningElement")),
                       "voids": len(chk.by_type("IfcRelVoidsElement")),
                       "door": meta["n_door"], "win": meta["n_win"],
                       "orphan": meta["n_orphan"], "opening_area_m2": meta["opening_area_m2"],
                       "psets": len(chk.by_type("IfcPropertySet")), "schema": chk.schema, **v}
    return res


if __name__ == "__main__":
    import json
    print(json.dumps(export_all(), ensure_ascii=False, indent=2))
