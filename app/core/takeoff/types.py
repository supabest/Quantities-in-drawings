"""里程碑 A 纯数据契约：算量参数、行、问题、覆盖矩阵、结果。

本模块不得导入 flask / app / app.models / modeling —— 只承载数据形状，
几何与落库分别由后续模块负责（numbers 只来自几何）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_FLOOR_HEIGHT_MM = {'sample_flat': 2800.0, 'office': 3000.0, 'showroom': 4000.0}
NO_DEFAULT_TYPES = frozenset({'mall', 'hotel'})


@dataclass
class TakeoffParams:
    project_type: str = 'sample_flat'
    floor_height_mm: float | None = None
    door_height_mm: float = 2100.0
    window_height_mm: float = 1500.0
    window_sill_mm: float = 0.0
    slab_mm: float | None = None
    floor_finish_mm: float | None = None
    seals: list = field(default_factory=list)
    scale_count: int | None = None

    def requires_floor_height(self) -> bool:
        if self.floor_height_mm:
            return False
        return self.project_type not in DEFAULT_FLOOR_HEIGHT_MM

    def effective_floor_height_mm(self) -> float | None:
        return self.floor_height_mm or DEFAULT_FLOOR_HEIGHT_MM.get(self.project_type)

    def height_source(self) -> str:
        return '用户填写' if self.floor_height_mm else '业态默认'


@dataclass
class Row:
    division_id: int
    label: str
    category: str
    unit: str
    value: float | None
    formula_text: str
    position_desc: str = ''
    remarks: str = ''
    source_tag: str = '图纸实测'
    confidence_level: str = 'high'
    material_code: str | None = None
    project_features: str = ''
    check: dict | None = None


@dataclass
class Issue:
    code: str
    level: str          # info / warn / error
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class Coverage:
    cells: dict = field(default_factory=dict)

    def mark(self, space: str, part: str, status: str, source: str | None = None):
        self.cells[(space, part)] = {'status': status, 'source': source}

    def rows(self) -> list:
        return [{'space': s, 'part': p, **v} for (s, p), v in sorted(self.cells.items())]


@dataclass
class TakeoffResult:
    rows: list
    coverage: Coverage
    issues: list
