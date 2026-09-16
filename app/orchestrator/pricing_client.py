"""套价系统联动客户端 — 图算量 BOQ → decoration-pricing-system。

两个集成点：
  1. AI 套价（当前可用）: POST /api/v1/pricing/batch
     传清单项名称+规格+单位 → 返回匹配定额子目 + 置信度。
     用于"图算量完成后一键试套价"。
  2. 正式清单导入（需套价系统配合）: POST /api/v1/bill-items
     套价系统该路由当前被注释（router.py 标记 deprecated），启用后
     可将 Measurement 直接落库为 BillItem，走其全套价流程。

映射约定（两系统字段对齐）：
  Measurement.label + project_features → item.name
  Measurement.material_code / base_layer → item.spec
  Measurement.unit → item.unit
"""
import logging
import requests

logger = logging.getLogger(__name__)


class PricingSystemError(Exception):
    pass


class PricingClient:

    def __init__(self, base_url: str, api_key: str = '', timeout: int = 60):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout

    # ── 映射：Measurement → 套价请求项 ──
    @staticmethod
    def to_pricing_item(m) -> dict:
        name = m.label or ''
        if m.project_features:
            name = f"{name} {m.project_features}"
        return {
            'name': name.strip(),
            'spec': m.material_code or m.base_layer or '',
            'unit': m.unit or '',
        }

    def _headers(self) -> dict:
        h = {'Content-Type': 'application/json'}
        if self.api_key:
            h['Authorization'] = f'Bearer {self.api_key}'
        return h

    # ── 集成点 1: AI 批量套价 ──
    def price_batch(self, measurements: list) -> dict:
        items = [self.to_pricing_item(m) for m in measurements]
        if not items:
            return {'total': 0, 'success': 0, 'failed': 0, 'results': []}

        url = f"{self.base_url}/api/v1/pricing/batch"
        try:
            resp = requests.post(
                url, json={'items': items},
                headers=self._headers(), timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise PricingSystemError(f"套价系统调用失败: {exc}") from exc

    # ── 集成点 2: 正式清单导入（需套价系统启用 bill-items 路由）──
    def import_bill(self, pricing_project_id: int, measurements: list) -> dict:
        items = [{
            'project_id': pricing_project_id,
            'description': self.to_pricing_item(m)['name'],
            'quantity': m.area or m.length or m.count,
            'unit': m.unit,
        } for m in measurements]

        url = f"{self.base_url}/api/v1/bill-items"
        try:
            resp = requests.post(
                url, json={'project_id': pricing_project_id, 'items': items},
                headers=self._headers(), timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise PricingSystemError(
                f"清单导入失败（请确认套价系统已启用 /api/v1/bill-items 路由）: {exc}"
            ) from exc
