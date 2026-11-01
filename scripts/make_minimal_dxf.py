#!/usr/bin/env python
"""生成脱敏小样：三间房共享 200 厚双线墙 + 一门线。CI 靠它守几何链路。

房间净空：客厅 4400×3000 / 卧室 3400×3000 / 卫生间 1800×3000（mm）。
围合栅格按 180mm 墙线内缩，实测净面积比标称每边小 110mm：
  (4400−220)(3000−220)=11.62 / (3400−220)(3000−220)=8.84 /
  (1800−220)(3000−220)=4.39，合计 24.85 m²。
这是**引擎栅格口径的期望值**（tests/test_takeoff_web.py 据此断言），
不是制图真值：图纸标称内净空合计 28.80 m²，差额全部来自上述内缩。
房间必须共享墙体：独立方盒间留 800mm 缝会被 AUTO_BRIDGE(1200) 判成
墙体错位而桥接，缝本身变成无名房间。

用法：./venv/bin/python scripts/make_minimal_dxf.py --out tests/fixtures/minimal_flat.dxf
"""
import argparse

import ezdxf

WALL_MM = 200
INNER_H = 3000
ROOMS = (("客厅", 4400), ("卧室", 3400), ("卫生间", 1800))


def main(out):
    doc = ezdxf.new("R2018", setup=True)
    for name in ("WALL", "S-WALL", "FIN", "DOOR", "WINDOW", "CURB", "P-TEXT"):
        doc.layers.add(name)
    msp = doc.modelspace()

    total_w = sum(w for _, w in ROOMS) + WALL_MM * (len(ROOMS) + 1)
    total_h = INNER_H + 2 * WALL_MM

    def hline(y):
        msp.add_line((0, y), (total_w, y), dxfattribs={"layer": "WALL"})

    def vline(x):
        msp.add_line((x, 0), (x, total_h), dxfattribs={"layer": "WALL"})

    # 外圈双线墙
    for y in (0, WALL_MM, total_h - WALL_MM, total_h):
        hline(y)
    vline(0)
    vline(WALL_MM)
    vline(total_w - WALL_MM)
    vline(total_w)

    # 隔墙双线：相邻两间共用 200 厚墙
    x = WALL_MM
    for name, width in ROOMS:
        if x > WALL_MM:
            vline(x)
            vline(x + WALL_MM)
            x += WALL_MM
        msp.add_text(name, height=250, dxfattribs={"layer": "P-TEXT"}).set_placement(
            (x + width / 2, WALL_MM + INNER_H / 2))
        x += width

    # 客厅南墙洞口开启线（900mm 宽；围合由共享墙 + 桥接封住，不据此漏房）
    msp.add_line((1000, WALL_MM), (1900, WALL_MM), dxfattribs={"layer": "DOOR"})
    doc.saveas(out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="tests/fixtures/minimal_flat.dxf")
    main(p.parse_args().out)
