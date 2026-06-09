"""Dify adapter — delegates to Dify workflows.

Used when Dify is available as the business workflow engine.
Each adapter maps high-level operations to specific Dify workflow names.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from agentic_rag.tools.adapters.base import (
    CRMBackend,
    CRMResult,
    LogisticsBackend,
    LogisticsResult,
    OrderBackend,
    OrderResult,
    PriceAnalysisBackend,
    PriceAnalysisResult,
)

logger = logging.getLogger(__name__)


class DifyAdapterMixin:
    """Shared Dify HTTP call logic."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    async def _call_workflow(
        self, workflow_name: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        import aiohttp

        url = f"{self._base_url}/workflows/run"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "inputs": inputs,
            "response_mode": "blocking",
            "user": "agentic_rag",
        }

        idempotency_key = hashlib.sha256(
            (workflow_name + json.dumps(inputs, sort_keys=True)).encode()
        ).hexdigest()[:16]

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._timeout)) as session:
                async with session.post(
                    url,
                    json=payload,
                    headers={**headers, "X-Idempotency-Key": idempotency_key},
                ) as resp:
                    data = await resp.json()
                    if resp.status >= 400:
                        logger.error("Dify workflow '%s' failed: %s", workflow_name, data)
                        return {"error": str(data), "status": "failed"}
                    return data.get("data", {}).get("outputs", data)
        except Exception as exc:
            logger.exception("Dify workflow '%s' call failed", workflow_name)
            return {"error": str(exc), "status": "error"}


class DifyOrderBackend(OrderBackend, DifyAdapterMixin):
    """Order operations via Dify workflows."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0) -> None:
        DifyAdapterMixin.__init__(self, base_url, api_key, timeout=timeout)

    async def lookup(self, order_id: str) -> OrderResult:
        outputs = await self._call_workflow("check_order_status", {"order_id": order_id})
        return OrderResult(
            order_id=order_id,
            status=outputs.get("status", "unknown"),
            raw=outputs,
        )

    async def create(self, order_data: dict[str, Any]) -> OrderResult:
        outputs = await self._call_workflow("create_order", order_data)
        return OrderResult(
            order_id=outputs.get("order_id", ""),
            status=outputs.get("status", "pending"),
            raw=outputs,
        )

    async def cancel(self, order_id: str, reason: str) -> OrderResult:
        outputs = await self._call_workflow("cancel_order", {"order_id": order_id, "reason": reason})
        return OrderResult(
            order_id=order_id,
            status=outputs.get("status", "cancelled"),
            raw=outputs,
        )


class DifyLogisticsBackend(LogisticsBackend, DifyAdapterMixin):
    """Logistics operations via Dify workflows."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0) -> None:
        DifyAdapterMixin.__init__(self, base_url, api_key, timeout=timeout)

    async def track(self, tracking_number: str) -> LogisticsResult:
        outputs = await self._call_workflow("track_shipment", {"tracking_number": tracking_number})
        return LogisticsResult(
            shipment_id=outputs.get("shipment_id", ""),
            status=outputs.get("status", "unknown"),
            tracking_number=tracking_number,
            raw=outputs,
        )

    async def get_warehouse_stock(self, product_id: str) -> dict[str, Any]:
        outputs = await self._call_workflow("check_warehouse_stock", {"product_id": product_id})
        return outputs


class DifyPriceAnalysisBackend(PriceAnalysisBackend, DifyAdapterMixin):
    """Price analysis via Dify workflows."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 60.0) -> None:
        DifyAdapterMixin.__init__(self, base_url, api_key, timeout=timeout)

    async def analyze(self, product_id: str, competitor_ids: list[str] | None = None) -> PriceAnalysisResult:
        inputs: dict[str, Any] = {"product_id": product_id}
        if competitor_ids:
            inputs["competitor_ids"] = competitor_ids
        outputs = await self._call_workflow("analyze_competitor_pricing", inputs)
        return PriceAnalysisResult(
            product_id=product_id,
            our_price=outputs.get("our_price", 0.0),
            suggested_price=outputs.get("suggested_price"),
            analysis_text=outputs.get("analysis", ""),
            raw=outputs,
        )


class DifyCRMBackend(CRMBackend, DifyAdapterMixin):
    """CRM operations via Dify workflows."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0) -> None:
        DifyAdapterMixin.__init__(self, base_url, api_key, timeout=timeout)

    async def lookup_ticket(self, ticket_id: str) -> CRMResult:
        outputs = await self._call_workflow("lookup_ticket", {"ticket_id": ticket_id})
        return CRMResult(
            ticket_id=ticket_id,
            status=outputs.get("status", "unknown"),
            raw=outputs,
        )

    async def create_return(self, order_id: str, reason: str, item_ids: list[str] | None = None) -> CRMResult:
        inputs: dict[str, Any] = {"order_id": order_id, "reason": reason}
        if item_ids:
            inputs["item_ids"] = item_ids
        outputs = await self._call_workflow("process_return", inputs)
        return CRMResult(
            ticket_id=outputs.get("ticket_id", ""),
            status=outputs.get("status", "pending"),
            resolution=outputs.get("resolution", ""),
            raw=outputs,
        )
