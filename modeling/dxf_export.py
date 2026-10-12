"""modeling.dxf_export — 导出干净的建模底图 DXF(供广联达/Revit 平面导入兜底)。

图层: WALL_STRICT(真墙双线/间墙), ROOM(房间边界闭合多段线), ROOM_NAME(房间名文字)。
坐标保留母图世界坐标(mm), 与原图叠加对齐; 不含造型/门扇/玻璃/反坎。
"""
import ezdxf
from . import base_extract as B


def export(path="outputs/model/227_model_base.dxf", data=None):
    data = data or B.build()
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    for lay, color in [("WALL_STRICT", 7), ("ROOM", 3), ("ROOM_NAME", 1), ("SEAL", 5)]:
        doc.layers.add(lay, color=color)

    for x0, y0, x1, y1 in data["walls_strict"]:
        msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": "WALL_STRICT"})
    for x0, y0, x1, y1 in data["seals"]:
        msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": "SEAL"})

    for r in data["rooms"]:
        if r["is_out"] or not r["poly"]: continue
        pts = r["poly"]
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "ROOM"})
        msp.add_text("+".join(r["names"]), dxfattribs={"layer": "ROOM_NAME", "height": 250}) \
            .set_placement((r["cx"], r["cy"]), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc.saveas(path)
    n_room = sum(1 for r in data["rooms"] if not r["is_out"] and r["poly"])
    return {"path": path, "bytes": os.path.getsize(path),
            "walls": len(data["walls_strict"]), "rooms": n_room}


if __name__ == "__main__":
    import json
    print(json.dumps(export(), ensure_ascii=False, indent=2))
