import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "outputs"
OUT_DIR.mkdir(exist_ok=True)

with open(ROOT / "quantity_summary.json", "r", encoding="utf-8") as f:
    summary = json.load(f)

by_sheet = summary["by_sheet"]


def ins(sheet, layer=None, prefix=None):
    total = 0
    for key, value in by_sheet[sheet]["insert"].items():
        lyr, _name = key.split("||", 1)
        if layer is not None and lyr != layer:
            continue
        if prefix is not None and not lyr.startswith(prefix):
            continue
        total += value
    return total


def ins_layers(sheet, layers):
    return sum(value for key, value in by_sheet[sheet]["insert"].items() if key.split("||", 1)[0] in layers)


def length(sheet, layer):
    return float(by_sheet[sheet]["length"].get(layer, 0))


def r2(v):
    return round(v + 1e-9, 2)


lamp_23 = ins("23A照明", layer="C-天花灯具")
lamp_25 = ins("25照明", layer="C-天花灯具")
socket_23 = ins("23A插座", layer="EM-插座")
socket_25 = ins("25插座", layer="EM-插座")
emg_23 = ins("23A应急照明", layer="EQUIP-应急")
emg_25 = ins("25应急照明", layer="EQUIP-应急")
fire_23 = ins_layers("23A火灾报警", ["EQUIP-消防", "E-FIRE-EQPM", "EQUIP-隔离模块", "E-FIRE-CIRC"])
fire_25 = ins_layers("25火灾报警", ["EQUIP-消防", "E-FIRE-EQPM", "EQUIP-隔离模块", "E-FIRE-CIRC"])
weak_23 = ins("23A弱电", layer="EM-插座")
weak_25 = ins("25弱电", layer="EM-插座")
broadcast_23 = ins_layers("23A弱电", ["T-AVSX-EQPM", "EQUIP-广播"])
broadcast_25 = ins_layers("25弱电", ["T-AVSX-EQPM", "EQUIP-广播"])
security_23 = ins("23A弱电", layer="EQUIP-安防")
security_25 = ins("25弱电", layer="EQUIP-安防")

lighting_wire = length("23A照明", "WIRE-照明") + length("25照明", "WIRE-照明")
socket_wire = length("23A插座", "WIRE-照明") + length("25插座", "WIRE-照明") + length("23A插座", "EM-插座") + length("25插座", "EM-插座")
emg_wire = length("23A应急照明", "WIRE-照明") + length("25应急照明", "WIRE-照明")
fire_wire = (
    length("23A火灾报警", "E-FIRE-CIRC")
    + length("25火灾报警", "E-FIRE-CIRC")
    + length("23A火灾报警", "WIRE-消防电源")
    + length("25火灾报警", "WIRE-消防电源")
)
network_wire = length("23A弱电", "WIRE-网络") + length("25弱电", "WIRE-网络")
broadcast_wire = length("23A弱电", "WIRE-广播线") + length("25弱电", "WIRE-广播线")
comm_wire = length("23A弱电", "WIRE-通讯") + length("25弱电", "WIRE-通讯")

rows = [
    [1, "配电箱", "23A层配电箱4台、25层配电箱2台；非标箱，接强电井原有配电回路，箱内元器件及安装调试按图纸系统图", "台", 6, "系统图文字“共1个”统计：23A层4台 + 25层2台"],
    [2, "普通照明灯具", "展示区天花灯具，含可调筒灯及图纸灯具块，安装方式按天花照明平面图", "套", lamp_23 + lamp_25, f"23A照明平面图C-天花灯具块计数{lamp_23} + 25照明平面图C-天花灯具块计数{lamp_25}"],
    [3, "接线盒", "照明接线盒，配合照明回路安装", "个", 6, "23A照明平面图 WIRE-照明/接线盒块计数6"],
    [4, "单联照明开关", "墙面暗装单联开关；回路编号按S/F系列标注", "个", 11, "文字标注统计：23A S1.08、S1.11各1个 + S5.03~S5.11共9个"],
    [5, "双联照明开关", "墙面暗装双联开关；回路编号按S/F系列标注", "个", 18, "文字标注统计：23A双联4个 + 25层双联14个"],
    [6, "三联照明开关", "墙面暗装三联开关；回路编号按S/F系列标注", "个", 21, "文字标注统计：23A三联6个 + 25层三联15个"],
    [7, "照明配管配线", "WDZC-B1-BYJ-3x2.5，JDG20，敷设方式SCE/WC/CC，含管内穿线", "m", r2(lighting_wire), f"照明平面图WIRE-照明长度/1000：23A {r2(length('23A照明','WIRE-照明'))}m + 25层 {r2(length('25照明','WIRE-照明'))}m"],
    [8, "墙身电源插座", "墙身安装双插座电源接线盒及配套面板，暗装，规格按插座平面图", "个", socket_23 + socket_25, f"EM-插座块计数：23A插座{socket_23}个 + 25插座{socket_25}个"],
    [9, "插座配管配线", "WDZC-B1-BYJ-3x2.5/3x4，JDG20，WC/FC或SCE/WC敷设，含管内穿线", "m", r2(socket_wire), f"插座平面图相关线长/1000：23A {r2(length('23A插座','WIRE-照明')+length('23A插座','EM-插座'))}m + 25层 {r2(length('25插座','WIRE-照明')+length('25插座','EM-插座'))}m"],
    [10, "应急照明及疏散指示灯具", "集中控制A型应急照明系统；疏散指示常亮，应急照明灯火灾时点亮", "套", emg_23 + emg_25, f"EQUIP-应急块计数：23A {emg_23}套 + 25层 {emg_25}套"],
    [11, "应急照明配管配线", "应急照明回路配管配线，敷设方式按应急照明平面及系统图", "m", r2(emg_wire), f"应急照明平面WIRE-照明长度/1000：23A {r2(length('23A应急照明','WIRE-照明'))}m + 25层 {r2(length('25应急照明','WIRE-照明'))}m"],
    [12, "火灾报警设备", "火灾报警平面设备，含探测、模块、手报/声光等图纸块；具体型号按消防报警系统图及厂家深化", "个", fire_23 + fire_25, f"火灾报警平面相关设备块计数：23A {fire_23}个 + 25层 {fire_25}个"],
    [13, "消防报警配管配线", "消防报警及消防电源线路，JDG管/消防线，含配管穿线，规格按图纸标注", "m", r2(fire_wire), f"E-FIRE-CIRC + WIRE-消防电源长度/1000：23A {r2(length('23A火灾报警','E-FIRE-CIRC')+length('23A火灾报警','WIRE-消防电源'))}m + 25层 {r2(length('25火灾报警','E-FIRE-CIRC')+length('25火灾报警','WIRE-消防电源'))}m"],
    [14, "信息/网络插座", "弱电平面信息点/网络点，含面板底盒，六类八芯双绞线系统由弱电厂家深化", "个", weak_23 + weak_25, f"弱电平面EM-插座块计数：23A {weak_23}个 + 25层 {weak_25}个"],
    [15, "网络线路", "UTP CAT6六类八芯双绞线，JDG管或线槽敷设，含线头预留", "m", r2(network_wire), f"弱电平面WIRE-网络长度/1000：23A {r2(length('23A弱电','WIRE-网络'))}m + 25层 {r2(length('25弱电','WIRE-网络'))}m"],
    [16, "广播终端/广播设备", "公共兼消防应急广播系统终端及相关设备，42U机柜内广播主机按系统图", "个", broadcast_23 + broadcast_25, f"弱电平面广播相关设备块计数：23A {broadcast_23}个 + 25层 {broadcast_25}个"],
    [17, "广播线路", "广播线JDG20，含配管穿线，23A与25层引上引下按图纸标注", "m", r2(broadcast_wire), f"弱电平面WIRE-广播线长度/1000：23A {r2(length('23A弱电','WIRE-广播线'))}m + 25层 {r2(length('25弱电','WIRE-广播线'))}m"],
    [18, "安防设备", "视频监控/门禁/一体式读卡器等安防设备，具体型号由弱电深化确认", "个", security_23 + security_25, f"弱电平面EQUIP-安防块计数：23A {security_23}个 + 25层 {security_25}个"],
    [19, "通讯线路", "弱电通讯线路，配管敷设方式按弱电平面图", "m", r2(comm_wire), f"弱电平面WIRE-通讯长度/1000：23A {r2(length('23A弱电','WIRE-通讯'))}m + 25层 {r2(length('25弱电','WIRE-通讯'))}m"],
    [20, "42U弱电机柜", "42U机柜，公共兼消防应急广播主机等设备设置于机柜内", "台", 2, "图纸文字标注“42U机柜x2”"],
]

wb = Workbook()
ws = wb.active
ws.title = "工程量计算表"
headers = ["序号", "项目名称", "项目特征", "单位", "工程量", "计算公式"]
ws.append(headers)
for row in rows:
    ws.append(row)

stats = wb.create_sheet("分层统计")
stats.append(["图纸区域", "类别", "图层", "块名/线型", "数量/长度(m)"])
for sheet, data in by_sheet.items():
    for key, value in sorted(data["insert"].items(), key=lambda kv: kv[1], reverse=True):
        layer, name = key.split("||", 1)
        if any(token in layer for token in ["EQUIP", "EM-", "0-开关", "C-天花灯具", "WIRE", "E-FIRE", "T-AVSX", "LWIRE"]):
            stats.append([sheet, "块计数", layer, name, value])
    for layer, value in sorted(data["length"].items(), key=lambda kv: kv[1], reverse=True):
        if any(token in layer for token in ["WIRE", "E-FIRE", "EM-", "通讯", "广播", "消防", "照明", "电"]):
            stats.append([sheet, "线长", layer, "", round(value, 2)])

notes = wb.create_sheet("计量说明")
notes_rows = [
    ["源文件", "营销中心售楼部电气图纸.dwg；计价办法.pdf"],
    ["DWG转换", "使用本机AutoCAD 2024 AcCoreConsole转换为electrical.dxf后读取；DWG提示外部参照xtl.dwg缺失、部分字体替换。"],
    ["计价办法页码", "计价办法.pdf共27页；第20-27页为总包管理、精装修配合及价差调整，未见水电分部分项清单格式。已按PDF第1-6页“清单计价原则/机电安装工程基准价适用范围”并结合图纸分项输出。"],
    ["计量口径", "平面工程量按标题框切分为23A/25层各6张平面图，避开系统图、目录和图框；线长按DXF图上长度/1000换算为米。"],
    ["项目特征", "优先采用图纸系统图及平面文字：WDZC-B1-BYJ、JDG20/JDG25、SCE/WC/FC/CC、UTP CAT6、42U机柜等。"],
    ["复核建议", "因缺少外部参照和正式图例，消防/弱电设备块名称为CAD内部块名，建议由设计图例或设备表复核具体设备分类后再计价。"],
]
for row in notes_rows:
    notes.append(row)

thin = Side(style="thin", color="D9E2EC")
header_fill = PatternFill("solid", fgColor="1F4E78")
sub_fill = PatternFill("solid", fgColor="D9EAF7")
for sheet in wb.worksheets:
    sheet.freeze_panes = "A2"
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in sheet.iter_rows():
        for cell in row:
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.auto_filter.ref = sheet.dimensions

widths = {
    "工程量计算表": [8, 22, 58, 10, 12, 72],
    "分层统计": [18, 12, 22, 46, 16],
    "计量说明": [18, 100],
}
for sheet_name, cols in widths.items():
    ws0 = wb[sheet_name]
    for i, w in enumerate(cols, 1):
        ws0.column_dimensions[get_column_letter(i)].width = w
    for r in range(1, ws0.max_row + 1):
        ws0.row_dimensions[r].height = 32 if r > 1 else 24

for r in range(2, ws.max_row + 1):
    ws.cell(r, 5).number_format = "0.00"

out = OUT_DIR / "营销中心售楼部电气工程量计算表.xlsx"
wb.save(out)
print(out)
