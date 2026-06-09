"""Agentic RAG for E-Commerce — configuration via pydantic-settings.

All settings are read from environment variables with the ``AGENTIC_RAG_`` prefix.
A ``.env`` file in the project root is also loaded automatically.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class KGBackend(str, Enum):
    NEO4J = "neo4j"
    SIMPLE = "simple"


class MemoryBackend(str, Enum):
    PGVECTOR = "pgvector"
    SQLITE_CHROMA = "sqlite_chroma"


class PrivacyMode(str, Enum):
    STRICT = "strict"
    STANDARD = "standard"
    PERMISSIVE = "permissive"


class BackendMode(str, Enum):
    DIFY = "dify"
    DIRECT = "direct"


class AgenticRAGSettings(BaseSettings):
    """Configuration for the Agentic RAG system.

    All values can be overridden via ``AGENTIC_RAG_<FIELD>`` environment variables.
    """

    model_config = {"env_prefix": "AGENTIC_RAG_", "env_file": ".env", "extra": "ignore"}

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------
    llm_model: str = "deepseek-chat"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_light_model: str = ""

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------
    max_plan_steps: int = 5
    max_reflection_rounds: int = 3
    tool_timeout_seconds: float = 30.0
    intent_clarity_threshold: float = 0.7
    max_clarification_rounds: int = 2

    # ------------------------------------------------------------------
    # Knowledge Graph
    # ------------------------------------------------------------------
    kg_backend: KGBackend = KGBackend.SIMPLE
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    kg_query_timeout_seconds: float = 5.0
    kg_max_hops: int = 3
    kg_max_entities_per_hop: int = 50

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------
    memory_backend: MemoryBackend = MemoryBackend.SQLITE_CHROMA
    postgres_url: str = ""
    memory_ttl_hours: int = 720
    memory_privacy_mode: PrivacyMode = PrivacyMode.STANDARD
    memory_encryption_key: str = ""

    # Paths for SQLite + Chroma backend
    memory_db_path: str = str(Path("data/memory.db").absolute())
    memory_vectors_collection: str = "memory_vectors"

    # ------------------------------------------------------------------
    # Existing RAG stores (for import)
    # ------------------------------------------------------------------
    chroma_persist_dir: str = str(Path("chroma_data").absolute())
    fts_db_path: str = str(Path("fts_data/fts.db").absolute())

    # ------------------------------------------------------------------
    # Dify
    # ------------------------------------------------------------------
    dify_base_url: str = "http://localhost:5001/v1"
    dify_api_key: str = ""
    dify_cache_ttl_seconds: int = 60
    dify_idempotency_enabled: bool = True

    # Feishu integration
    feishu_webhook_url: str = ""
    feishu_signing_secret: str = ""
    feishu_bitable_app_token: str = ""

    # ------------------------------------------------------------------
    # Backend mode per service
    # ------------------------------------------------------------------
    order_backend: BackendMode = BackendMode.DIRECT
    logistics_backend: BackendMode = BackendMode.DIRECT
    price_analysis_backend: BackendMode = BackendMode.DIRECT
    crm_backend: BackendMode = BackendMode.DIRECT

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------
    otel_exporter_endpoint: str = ""
    prometheus_port: int = 9090
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "text"] = "json"

    # ------------------------------------------------------------------
    # Derived
    # ------------------------------------------------------------------
    @property
    def llm_light_model_effective(self) -> str:
        return self.llm_light_model or self.llm_model

    @property
    def data_dir(self) -> Path:
        p = Path("data")
        p.mkdir(parents=True, exist_ok=True)
        return p


# Global singleton
_settings: AgenticRAGSettings | None = None


def get_settings() -> AgenticRAGSettings:
    """Return the process-level settings singleton, initialising on first call."""
    global _settings
    if _settings is None:
        _settings = AgenticRAGSettings()
    return _settings


def reset_settings() -> None:
    """Clear the cached settings (for testing)."""
    global _settings
    _settings = None
