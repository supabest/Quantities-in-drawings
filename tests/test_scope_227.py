#!/usr/bin/env python
"""算量范围哨兵：仓库与代码只允许服务 227 户型（2026-10-12 用户裁定）。

钉五条（纯解析、零图纸、CI 可跑）：
  A 入库文件不得是范围外图纸，也不得是被清出的历史数据源名。
  B 入库 CAD 件（.dwg/.dxf/.dwl/.dwl2）文件名必须含 227 标识。
  C 原始图纸目录 装修图纸/ 不入库（已 gitignore），误提交即红。
  D 代码里的**路径型字面量**不得指向范围外图纸。
  E 判定函数本身用真实图名样本回归（防止把哨兵写成只会误报的噪音）。

范围判定的口径（核心，勿改回按目录禁词判定）：
  判据是**户型身份**，不是目录名。2026-10-12 实测确认：
  - 「210户型」即「227户型」，同一户型（用户裁定），故 210 属范围内标识；
  - 227 的机电图大量存放于名字含别的户型或「营销中心售楼部」的**混合图**中，
    如「136、227户型 样板间电气…dxf」「…135户型，210户型样板间全屋智能…dwg」
    「珠实金融城项目营销中心售楼部样板间暖通图纸(136、227户型)/227户型暖通…」；
    用目录禁词判红会把这些合法 227 源一律误杀，故含 227/210 者一律视为范围内。
  - 只有既不含 227/210、又出现别户型标识或工控(20层)项目标识的路径才算范围外。
  - 上述判定的输入以**文件名优先**：混合目录下同名并存着 136 图与 227 图，
    只看整条路径会让纯 136 文件借着父目录里的「227」蒙混过关（见判定函数注）。

D 的口径：
  "路径型字面量" = 含「装修图纸」或以图纸扩展名结尾的字符串。
  - 含「装修图纸/」的必须带 227/210 标识（目录常量唯一化）；
  - 以图纸扩展名结尾的不得判为范围外，也不得是 BANNED 历史数据源名。
  裸目录段（"装修图纸"）、裸后缀（".dxf"）、以及拼在 227 目录常量下的
  图纸文件名（"12.GD01~05客厅固装大样图.dxf"）都是合法写法，不判红。
  注释里的历史说明与 app/models.py 项目类型枚举"售楼部/展厅"（非路径）同样不管。
"""
import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CAD_EXT = (".dwg", ".dxf", ".dwl", ".dwl2")
DRAWING_EXT = CAD_EXT + (".pdf", ".zip", ".xlsx")
# 210 即 227（同一户型），两者都是范围内标识
UNIT_ALIASES = ("227", "210")
# 确属其它户型 / 其它项目的标识；仅在路径不含 UNIT_ALIASES 时才判红
OTHER_UNITS = ("99户型", "131户型", "135户型", "136户型", "410")
OTHER_PROJECTS = ("工控", "20层", "售楼部电气")
# 已退出范围的历史数据源文件名（防被重新接回管线）
BANNED = ("electrical.dxf", "extracted_texts.tsv", "quantity_summary.json")
# 合成测试小样：由 scripts/make_minimal_dxf.py 现场生成、已脱敏，不是任何项目的
# 真实图纸，故不适用「入库 CAD 必须带 227/210」的溯源判据。只放生成器产物，
# 不得借此豁免真实图纸（test_B2 校验生成器仍在、文件仍存在）。
SYNTHETIC_CAD_FIXTURES = {"tests/fixtures/minimal_flat.dxf"}


def _has(txt, toks):
    return any(t in txt for t in toks)


def is_out_of_scope(lit):
    """路径型字面量是否指向非 227 的图纸。

    **文件名优先于目录名**：混合目录（如「…暖通图纸(136、227户型)」）里既有 227
    图也有 136 图，只看整条路径会因父目录带 227 而把纯 136 文件误判为范围内。
    故先判 basename，basename 无户型标识时才退回目录路径作弱判定。
    """
    if lit.lower() in BANNED:
        return True
    base = os.path.basename(lit.rstrip("/"))
    if _has(base, UNIT_ALIASES):
        return False                       # 文件名点名 227/210 → 范围内
    if _has(base, OTHER_UNITS + OTHER_PROJECTS):
        return True                        # 文件名点名别户型/工控 → 范围外
    if _has(lit, UNIT_ALIASES):
        return False                       # 仅目录含 227（如 415-136及227）→ 放行
    return _has(lit, OTHER_UNITS + OTHER_PROJECTS)


def _git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                       text=True, timeout=60)
    if r.returncode != 0:
        pytest.skip(f"git {' '.join(args)} 不可用: {r.stderr[:200]}")
    return r.stdout


TRACKED = [p for p in _git("ls-files", "-z").split("\0") if p]


def test_A_no_out_of_scope_file_is_tracked():
    hit = [p for p in TRACKED
           if is_out_of_scope(p) or p.split("/")[-1] in BANNED]
    assert not hit, f"范围外文件被入库（裁定仅 227 户型）: {hit}"


def test_B_tracked_cad_files_carry_227():
    bad = [p for p in TRACKED
           if p.lower().endswith(CAD_EXT)
           and not any(a in p for a in UNIT_ALIASES)
           and p not in SYNTHETIC_CAD_FIXTURES]
    assert not bad, f"入库 CAD 件未带 227/210 标识: {bad}"


def test_B2_synthetic_fixture_exemption_stays_generated():
    """豁免只给生成器产物：文件仍在、生成器仍产出该文件名，防止豁免变通用后门。"""
    generator = os.path.join(ROOT, "scripts", "make_minimal_dxf.py")
    with open(generator, encoding="utf-8") as fh:
        source = fh.read()
    for path in SYNTHETIC_CAD_FIXTURES:
        assert os.path.exists(os.path.join(ROOT, path)), path
        assert os.path.basename(path) in source, path


def test_C_raw_drawings_not_committed():
    hit = [p for p in TRACKED if p.startswith("装修图纸/")]
    assert not hit, f"原始图纸被提交入库（应仅本地存在）: {hit[:5]}"


@pytest.mark.parametrize("sub", ["modeling", "scripts", "app", "tests"])
def test_D_code_path_literals_stay_on_227(sub):
    bad = []
    for dp, _, fns in os.walk(os.path.join(ROOT, sub)):
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), ROOT)
            if rel == os.path.relpath(__file__, ROOT):
                continue          # 本哨兵自带禁用样本串，不扫自己
            text = open(os.path.join(dp, fn), encoding="utf-8", errors="ignore").read()
            for lit in re.findall(r"""['"]([^'"\n]{2,200})['"]""", text):
                if "装修图纸" not in lit and not lit.lower().endswith(DRAWING_EXT):
                    continue                          # 非路径型字面量，不管
                if "装修图纸/" in lit and not any(a in lit for a in UNIT_ALIASES):
                    bad.append(f"{rel}: 目录常量未锁 227/210 -> {lit!r}")
                elif is_out_of_scope(lit):
                    bad.append(f"{rel}: 指向范围外图纸 -> {lit!r}")
    assert not bad, "发现非 227 的图纸路径字面量:\n  " + "\n  ".join(bad)


# E：判定函数的真实样本回归。样本取自 2026-10-12 范围清理时磁盘上的实际图名，
# 目的是把「210 即 227」「混合图属范围内」这两条裁定钉进可执行代码。
@pytest.mark.parametrize("lit,expect_out", [
    # —— 范围内（含混合图），判红即误杀合法 227 源 ——
    ("装修图纸/227户型dxf/05.227户型平面图 （施工图纸） rev-33.dxf", False),
    ("装修图纸/样板房机电/.../415-136及227/136、227户型 样板间电气2026.07.08.dxf", False),
    ("装修图纸/样板房机电/.../415-136及227/227户型暖通平面图.dxf", False),
    ("装修图纸/样板房机电/.../设变-ZN-01-20260714珠实金融城项目135户型，210户型样板间.dwg", False),
    ("装修图纸/样板房机电dxf/.../设变-ZN-02-20260717珠实金融城项目135户型，227户型样板间.dxf", False),
    # —— 范围外（2026-10-12 已清出磁盘）——
    ("装修图纸/工控方案图/20260917+20层平面布置.dxf", True),
    ("装修图纸/工控方案图/20260917+20层平面材料说明.pdf", True),
    ("装修图纸/样板房机电/.../99户型样板房二次机电出图2026.6.30/99户型暖通平面图.dwg", True),
    ("装修图纸/样板房机电/.../131户型样板房二次机电出图2026.7.3/131户型 样板间电气 2026.07.02(1).dwg", True),
    ("装修图纸/样板房机电dxf/.../415-136及227/136户型暖通平面图.dxf", True),
    # 混合目录下文件名点名 136 —— 必须靠 basename 优先才判得出（该件已清出）
    ("装修图纸/样板房机电/.../珠实金融城项目营销中心售楼部样板间暖通图纸(136、227户型)/136户型暖通平面图.dwg", True),
    # 同一混合目录下文件名点名 227 —— 真 227 源，判红即丢图
    ("装修图纸/样板房机电/.../珠实金融城项目营销中心售楼部样板间暖通图纸(136、227户型)/227户型暖通平面图.dwg", False),
    # 131 目录里被保留的 410 配置清单：非 227 数据，代码不该去读
    ("装修图纸/样板房机电/.../131户型样板房二次机电出图2026.7.3/金融城410华为全屋智能配置清单（不带价）20260708.xlsx", True),
    ("electrical.dxf", True),
])
def test_E_scope_predicate_matches_rulings(lit, expect_out):
    assert is_out_of_scope(lit) is expect_out, (
        f"判定函数与 2026-10-12 裁定不符: {lit!r} -> "
        f"{'范围外' if is_out_of_scope(lit) else '范围内'}，应为"
        f"{'范围外' if expect_out else '范围内'}")
