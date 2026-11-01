import importlib.util
import json
import os

import openpyxl

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "build_boq_template", os.path.join(BASE, "scripts", "build_boq_template.py"))
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

from app.core.takeoff import template as vocab  # noqa: E402


def test_normalize_unit_aliases():
    assert vocab.normalize_unit("m²") == "m2"
    assert vocab.normalize_unit("㎡ ") == "m2"
    assert vocab.normalize_unit("M2") == "m2"
    assert vocab.normalize_unit("樘") == "樘"
    assert vocab.normalize_unit("") == "项"


def test_material_of_uses_controlled_vocabulary():
    assert vocab.material_of("墙面PT-01有色乳胶漆") == "乳胶漆"
    assert vocab.material_of("主卧衣柜1") == "衣柜"
    assert vocab.material_of("无法识别的东西") is None


def test_material_of_uses_head_noun():
    # 中文中心语在后：取最靠右的受控词，不能把修饰语（洗衣机/马桶）当材料
    assert vocab.material_of("洗衣机地漏") == "地漏"
    assert vocab.material_of("洗衣机龙头") == "龙头"
    assert vocab.material_of("马桶喷枪") == "喷枪"
    # 「开关型」是修饰用法，跳过该次命中后无有效词 → None（转人工复核）
    assert vocab.material_of("电动二通阀(开关型)DN20") is None
    assert vocab.material_of("8路开关执行器") == "开关"


def test_assert_no_quantities_rejects_numbers():
    mod.assert_no_quantities({"items": [{"name": "x", "unit": "m2"}]})
    import pytest

    with pytest.raises(AssertionError):
        mod.assert_no_quantities({"items": [{"name": "x", "quantity": 3.0}]})
    with pytest.raises(AssertionError):
        mod.assert_no_quantities({"items": [{"name": "x", "nested": {"area": 1.5}}]})


def _write_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ZS02-A-精装修工程-227户型"
    ws.append(["ZS02-A-精装修工程——分部分项工程量清单计价表"])
    ws.append(["序号", "项目名称", "项目特征", "计量单位"])
    ws.append(["一", "地面装饰工程"])
    ws.append(["（一）", "地面基层"])
    ws.append(["ZS02-A-001", "地面水泥砂浆找平层", "1:3 砂浆", "m2"])
    ws.append(["（二）", "地面瓷砖"])
    ws.append(["ZS02-A-007-1", "瓷砖地面铺贴阳台", "TL-02", "m²"])
    ws.append(["ZS02-A-007-1", "瓷砖地面铺贴阳台", "TL-02", "m2"])
    ws.append(["二", "墙柱面装饰工程"])
    ws.append(["（六）", "墙柱面踢脚线"])
    ws.append(["ZS02-A-075", "踢脚线不锈钢饰面", "不锈钢", "m"])
    ws.append(["三", "天棚装饰工程"])
    ws.append(["（五）", "天棚装饰线"])
    ws.append(["", "定制GRG线条1", "", ""])
    ws.append(["", "其他项目", "", ""])
    ws.append(["ZS02-A-106", "天棚刮腻子每增减一遍", "", "m2"])

    wb_b = wb.create_sheet("ZS02-B- 精装修水电136、227户型")
    wb_b.append(["序号", "项目名称", "项目特征", "计量单位"])
    wb_b.append(["五", "暖通（已填）"])
    wb_b.append(["新增", "厨房空调", "满足设计", "台"])

    wb_d = wb.create_sheet("户内门")
    wb_d.append(["序号", "材料名称/型号", "品牌", "单位"])
    wb_d.append(["一", "明细"])
    wb_d.append(["1", "户内门", "", "樘"])

    wb.save(path)


def test_build_from_synthetic_workbook(tmp_path):
    path = tmp_path / "boq.xlsx"
    _write_workbook(path)
    out = mod.build(str(path))
    items = out["items"]
    by_name = {it["name"]: it for it in items}

    assert by_name["地面水泥砂浆找平层"]["division"] == 1
    assert by_name["地面水泥砂浆找平层"]["capability"] == "floor_area"
    assert by_name["瓷砖地面铺贴阳台"]["unit"] == "m2"
    assert len([it for it in items if it["name"] == "瓷砖地面铺贴阳台"]) == 1
    assert by_name["踢脚线不锈钢饰面"]["division"] == 5
    assert by_name["踢脚线不锈钢饰面"]["capability"] == "baseboard_length"
    # 空白计量单位但名称属线性列项（GRG/线条/灯槽…）→ 真实行项，按 m 计
    assert by_name["定制GRG线条1"]["unit"] == "m"
    assert by_name["定制GRG线条1"]["group_only"] is False
    assert by_name["定制GRG线条1"]["division"] == 3
    # 其余空白计量单位行仍是分组标题
    assert by_name["其他项目"]["unit"] == "项"
    assert by_name["其他项目"]["group_only"] is True
    assert by_name["天棚刮腻子每增减一遍"]["group_only"] is True
    assert by_name["厨房空调"]["division"] == 8
    assert by_name["厨房空调"]["capability"] == "hvac"
    assert by_name["户内门"]["division"] == 4
    assert by_name["户内门"]["capability"] == "opening_count"
    mod.assert_no_quantities(out)


def test_committed_sample_flat_is_sanitized():
    path = os.path.join(BASE, "app", "core", "takeoff", "templates", "sample_flat.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    mod.assert_no_quantities(data)
    assert data["type"] == "sample_flat"
    assert {it["division"] for it in data["items"]} == {1, 2, 3, 4, 5, 6, 7, 8}
    assert len(data["items"]) >= 100
    allowed = {"m2", "m3", "m", "km", "个", "套", "台", "樘", "项", "kg", "t", "组", "根"}
    assert {it["unit"] for it in data["items"]} <= allowed
