"""modeling.gltf_export — glTF/GLB 可视化(仅展示, 不承载算量语义)。

用 trimesh 把房间体块(彩色)与真墙(深灰)挤出成网格, 导出 .glb 供浏览器/复核工作台查看。
坐标与 IFC 一致: 平移到局部原点、单位米。
"""
import numpy as np
import trimesh
from shapely.geometry import Polygon
from . import base_extract as B

S = 1.0 / 1000.0


def _palette(n):
    rng = np.random.default_rng(7)
    return [tuple(int(c) for c in rng.integers(90, 210, 3)) + (200,) for _ in range(n)]


def export(path="outputs/model/227_model.glb", data=None):
    data = data or B.build()
    ox, oy = data["bounds"][0], data["bounds"][1]
    clear = data["V"]["clear_to_soffit"] * S
    floor_top = data["V"]["floor_finish"] * S

    def loc(x, y): return (x - ox) * S, (y - oy) * S

    rooms = [r for r in data["rooms"] if not r["is_out"] and r["poly"]]
    colors = _palette(len(rooms))
    meshes = []
    for r, col in zip(rooms, colors):
        poly = Polygon([loc(x, y) for x, y in r["poly"]])
        if not poly.is_valid or poly.area <= 0:
            continue
        mesh = trimesh.creation.extrude_polygon(poly, height=clear - floor_top)
        mesh.apply_translation([0, 0, floor_top])
        mesh.visual.face_colors = col
        meshes.append(mesh)

    wmeshes = []
    for x0, y0, x1, y1 in data["walls_strict"]:
        if (x0, y0) == (x1, y1): continue
        dx, dy = x1 - x0, y1 - y0
        L = (dx * dx + dy * dy) ** 0.5 or 1.0
        t = 100.0 * S
        ox_, oy_ = -dy / L * t / 2, dx / L * t / 2
        pts = [loc(x0, y0), loc(x1, y1)]
        pts = [(pts[0][0] + ox_, pts[0][1] + oy_), (pts[1][0] + ox_, pts[1][1] + oy_),
               (pts[1][0] - ox_, pts[1][1] - oy_), (pts[0][0] - ox_, pts[0][1] - oy_)]
        poly = Polygon(pts)
        if not poly.is_valid or poly.area <= 0:
            continue
        mesh = trimesh.creation.extrude_polygon(poly, height=clear)
        mesh.visual.face_colors = (40, 40, 40, 255)
        wmeshes.append(mesh)

    scene = trimesh.Scene()
    if meshes:
        scene.add_geometry(trimesh.util.concatenate(meshes), node_name="rooms")
    if wmeshes:
        scene.add_geometry(trimesh.util.concatenate(wmeshes), node_name="walls")
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    scene.export(path)
    return {"path": path, "bytes": os.path.getsize(path), "room_meshes": len(meshes), "wall_meshes": len(wmeshes)}


if __name__ == "__main__":
    import json
    print(json.dumps(export(), ensure_ascii=False, indent=2))
