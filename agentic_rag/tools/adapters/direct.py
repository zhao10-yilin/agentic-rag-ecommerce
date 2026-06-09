"""Direct adapter — calls APIs directly or returns mock data.

Used when Dify is unavailable, for testing, or when the team prefers
direct API integration.
"""

from __future__ import annotations

import logging
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


class MockOrderBackend(OrderBackend):
    """Returns mock order data for development/testing."""

    async def lookup(self, order_id: str) -> OrderResult:
        return OrderResult(
            order_id=order_id,
            status="delivered",
            items=[{"product_id": "prod_001", "name": "Sample Product", "quantity": 1, "price": 99.0}],
            total_amount=99.0,
            created_at="2026-05-01T10:00:00Z",
            tracking_number="TRACK123",
        )

    async def create(self, order_data: dict[str, Any]) -> OrderResult:
        return OrderResult(
            order_id="new_order_001",
            status="confirmed",
            items=order_data.get("items", []),
            total_amount=sum(item.get("price", 0) * item.get("quantity", 1) for item in order_data.get("items", [])),
        )

    async def cancel(self, order_id: str, reason: str) -> OrderResult:
        return OrderResult(order_id=order_id, status="cancelled")


class MockLogisticsBackend(LogisticsBackend):
    """Returns mock logistics data."""

    async def track(self, tracking_number: str) -> LogisticsResult:
        return LogisticsResult(
            shipment_id=f"SH_{tracking_number}",
            status="in_transit",
            carrier="SF Express",
            tracking_number=tracking_number,
            estimated_delivery="2026-05-18",
            events=[
                {"timestamp": "2026-05-14T08:00:00Z", "location": "Shanghai", "status": "departed"},
                {"timestamp": "2026-05-13T20:00:00Z", "location": "Beijing", "status": "picked_up"},
            ],
        )

    async def get_warehouse_stock(self, product_id: str) -> dict[str, Any]:
        return {
            "product_id": product_id,
            "warehouse_id": "WH_SH_01",
            "quantity_available": 150,
            "quantity_reserved": 20,
            "next_restock_date": "2026-05-20",
        }


class MockPriceAnalysisBackend(PriceAnalysisBackend):
    """Returns mock competitor pricing data."""

    async def analyze(self, product_id: str, competitor_ids: list[str] | None = None) -> PriceAnalysisResult:
        return PriceAnalysisResult(
            product_id=product_id,
            our_price=299.0,
            competitor_prices=[
                {"competitor": "Competitor A", "price": 279.0, "platform": "JD.com"},
                {"competitor": "Competitor B", "price": 310.0, "platform": "Tmall"},
                {"competitor": "Competitor C", "price": 289.0, "platform": "PDD"},
            ],
            suggested_price=289.0,
            confidence=0.78,
            analysis_text="建议将价格下调至289元以匹配市场中位数，同时保持利润空间。",
        )


class MockCRMBackend(CRMBackend):
    """Returns mock CRM data."""

    async def lookup_ticket(self, ticket_id: str) -> CRMResult:
        return CRMResult(
            ticket_id=ticket_id,
            status="open",
            customer_id="cust_001",
            priority="normal",
            assigned_to="agent_007",
            resolution="",
        )

    async def create_return(self, order_id: str, reason: str, item_ids: list[str] | None = None) -> CRMResult:
        return CRMResult(
            ticket_id="ret_2026_001",
            status="return_approved",
            customer_id="cust_001",
            priority="high",
            resolution="Return authorised — RMA label sent to customer.",
        )
