"""Pluggable backend adapters for business tools.

Every business-facing tool (order, logistics, pricing, CRM) defines a
unified interface via an ABC, then provides two implementations:

* :class:`DirectAdapter` — calls APIs directly (or returns mock data).
* :class:`DifyAdapter` — delegates to a Dify workflow.

This decouples the tools from Dify so the system runs independently and
tool unit tests can inject mock backends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Shared result types
# ---------------------------------------------------------------------------


@dataclass
class OrderResult:
    order_id: str
    status: str
    items: list[dict[str, Any]] = field(default_factory=list)
    total_amount: float = 0.0
    created_at: str = ""
    tracking_number: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LogisticsResult:
    shipment_id: str
    status: str
    carrier: str = ""
    tracking_number: str = ""
    estimated_delivery: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PriceAnalysisResult:
    product_id: str
    our_price: float
    competitor_prices: list[dict[str, Any]] = field(default_factory=list)
    suggested_price: float | None = None
    confidence: float = 0.0
    analysis_text: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CRMResult:
    ticket_id: str
    status: str
    customer_id: str = ""
    priority: str = "normal"
    assigned_to: str = ""
    resolution: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Backend ABCs
# ---------------------------------------------------------------------------


class OrderBackend(ABC):
    """Interface for order management operations."""

    @abstractmethod
    async def lookup(self, order_id: str) -> OrderResult:
        ...

    @abstractmethod
    async def create(self, order_data: dict[str, Any]) -> OrderResult:
        ...

    @abstractmethod
    async def cancel(self, order_id: str, reason: str) -> OrderResult:
        ...


class LogisticsBackend(ABC):
    """Interface for logistics / shipping operations."""

    @abstractmethod
    async def track(self, tracking_number: str) -> LogisticsResult:
        ...

    @abstractmethod
    async def get_warehouse_stock(self, product_id: str) -> dict[str, Any]:
        ...


class PriceAnalysisBackend(ABC):
    """Interface for competitor price analysis."""

    @abstractmethod
    async def analyze(self, product_id: str, competitor_ids: list[str] | None = None) -> PriceAnalysisResult:
        ...


class CRMBackend(ABC):
    """Interface for customer relationship management."""

    @abstractmethod
    async def lookup_ticket(self, ticket_id: str) -> CRMResult:
        ...

    @abstractmethod
    async def create_return(self, order_id: str, reason: str, item_ids: list[str] | None = None) -> CRMResult:
        ...
