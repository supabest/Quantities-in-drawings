"""全局定标 — 比例尺/单位全项目统一，先于任何并行计算。

原则：定标不通过 → 拒绝执行，回炉。宁可不出清单，不出错清单。

Phase 1 简版：
  - 校验 project.scale 已设置且在合理范围
  - 检查各图纸记录的 scale 是否一致（若 Drawing 有该字段）
Phase 1.5（TODO）：
  - 逐图用 DIMENSION 标注值 ÷ 实测像素距离 估计比例尺
  - 多图交叉验证，偏差 > 10% 标记该图 🟡 并拒绝参与并行
"""
import logging

logger = logging.getLogger(__name__)

MIN_VALID_SCALE = 10      # px/m 下限（防止漏设或识别成 1）
MAX_VALID_SCALE = 2000    # px/m 上限
SCALE_AGREEMENT_TOLERANCE = 0.10   # 各图比例尺相对偏差容忍


def calibrate_project(project, drawings) -> dict:
    """全局定标检查。

    Returns:
        {
            'ok': bool,               # False 则 Orchestrator 拒绝执行
            'scale': float,           # 全项目统一 px/m
            'warnings': [str],
        }
    """
    warnings = []
    scale = project.scale or 0

    if not scale:
        return {
            'ok': False,
            'scale': 0,
            'warnings': ['项目未设置比例尺(px/m)，请先在项目设置中指定或运行自动定标'],
        }

    if not (MIN_VALID_SCALE <= scale <= MAX_VALID_SCALE):
        return {
            'ok': False,
            'scale': scale,
            'warnings': [f'比例尺 {scale} px/m 超出合理范围 [{MIN_VALID_SCALE}, {MAX_VALID_SCALE}]'],
        }

    # 各图纸独立标定值一致性检查（若已记录）
    per_drawing = [d.scale for d in drawings if getattr(d, 'scale', None)]
    if len(per_drawing) >= 2:
        lo, hi = min(per_drawing), max(per_drawing)
        if hi / lo - 1 > SCALE_AGREEMENT_TOLERANCE:
            warnings.append(
                f'各图纸比例尺不一致: min={lo}, max={hi}，偏差超 {SCALE_AGREEMENT_TOLERANCE:.0%}，'
                f'存在单位混用风险，请人工核对图纸出图比例'
            )

    return {'ok': True, 'scale': scale, 'warnings': warnings}
