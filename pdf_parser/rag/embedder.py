"""Embedding generation with local models, API fallback, and LRU caching.

Provides a unified :class:`EmbeddingService` that:

* Prefers a local ``sentence-transformers`` model (fast, no network, free).
* Can fall back to an OpenAI-compatible embedding API.
* Caches embeddings in-process via an LRU dict keyed on the SHA-256 hash of
  the input text — duplicate texts never hit the model twice.
* L2-normalises all vectors so callers can use dot-product as cosine similarity.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseEmbedder(ABC):
    """Interface for embedding providers."""

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of document texts."""
        ...

    @abstractmethod
    def embed_query(self, query: str) -> list[float]:
        """Encode a single query string."""
        ...

    @property
    @abstractmethod
    def dim(self) -> int:
        """Dimensionality of the embedding vectors."""
        ...


# ---------------------------------------------------------------------------
# Local sentence-transformers provider
# ---------------------------------------------------------------------------


class SentenceTransformerEmbedder(BaseEmbedder):
    """Embedding provider backed by a local ``sentence-transformers`` model.

    Parameters
    ----------
    model_name:
        HuggingFace model identifier.  Defaults to ``BAAI/bge-large-zh-v1.5``
        which is the best open-source Chinese embedding model as of 2025.
    device:
        ``'cpu'``, ``'cuda'``, or ``None`` (auto-detect).
    batch_size:
        Number of texts to encode in one forward pass.
    normalize:
        Whether to L2-normalise output vectors (default ``True``).
    """

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-large-zh-v1.5",
        device: str | None = None,
        batch_size: int = 32,
        normalize: bool = True,
    ) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._normalize = normalize
        self._model: Any = None
        self._dim: int = 0
        self._device = device

    # ------------------------------------------------------------------
    # Lazy initialisation (model is heavy — load only when needed)
    # ------------------------------------------------------------------

    def _ensure_model(self) -> None:
        if self._model is not None:
            return

        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model: %s", self._model_name)
        t0 = time.perf_counter()

        self._model = SentenceTransformer(
            self._model_name,
            device=self._device,
        )
        self._dim = self._model.get_sentence_embedding_dimension()

        elapsed = time.perf_counter() - t0
        logger.info(
            "Embedding model loaded in %.1fs (dim=%d)",
            elapsed,
            self._dim,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()

        if not texts:
            return []

        # BGE models benefit from a "query:" / "passage:" instruction prefix.
        # For document-side encoding we use no prefix (the model was trained
        # with asymmetric instructions, but the passage prefix is optional).
        embeddings = self._model.encode(
            texts,
            batch_size=self._batch_size,
            show_progress_bar=False,
            normalize_embeddings=self._normalize,
        )
        return [vec.tolist() for vec in embeddings]

    def embed_query(self, query: str) -> list[float]:
        self._ensure_model()

        # BGE models expect "为这个句子生成表示以用于检索相关文章：" prefix
        # for the query side to trigger the asymmetric instruction tuning.
        if "bge" in self._model_name.lower():
            query = f"为这个句子生成表示以用于检索相关文章：{query}"

        embedding = self._model.encode(
            query,
            normalize_embeddings=self._normalize,
        )
        return embedding.tolist()

    @property
    def dim(self) -> int:
        self._ensure_model()
        return self._dim


# ---------------------------------------------------------------------------
# OpenAI-compatible API provider
# ---------------------------------------------------------------------------


class OpenAIEmbedder(BaseEmbedder):
    """Embedding provider that calls an OpenAI-compatible API.

    Works with OpenAI, DeepSeek, Qwen, and any other provider that exposes
    a ``/v1/embeddings`` endpoint.

    Parameters
    ----------
    model:
        API model name (e.g. ``text-embedding-3-small``).
    api_key:
        API key.  Falls back to ``OPENAI_API_KEY`` / ``DEEPSEEK_API_KEY``
        environment variables.
    base_url:
        API base URL.  Defaults to ``https://api.openai.com/v1``.
    batch_size:
        Maximum texts per API call (the provider may enforce a lower limit).
    """

    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        base_url: str | None = None,
        batch_size: int = 100,
    ) -> None:
        self._model_name = model
        self._batch_size = batch_size

        resolved_key = api_key or os.environ.get(
            "OPENAI_API_KEY"
        ) or os.environ.get("DEEPSEEK_API_KEY")
        if not resolved_key:
            raise ValueError(
                "No API key found for OpenAIEmbedder. "
                "Set OPENAI_API_KEY or DEEPSEEK_API_KEY."
            )
        self._api_key = resolved_key
        self._base_url = base_url or "https://api.openai.com/v1"
        self._dim: int = 0

    def _get_client(self):
        from openai import OpenAI

        return OpenAI(api_key=self._api_key, base_url=self._base_url)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = self._get_client()
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            resp = client.embeddings.create(model=self._model_name, input=batch)
            batch_embs = [d.embedding for d in resp.data]
            all_embeddings.extend(batch_embs)

        return [_l2_normalize(v) for v in all_embeddings]

    def embed_query(self, query: str) -> list[float]:
        return self.embed_documents([query])[0]

    @property
    def dim(self) -> int:
        if self._dim == 0:
            # Probe with a dummy query
            vec = self.embed_query("dimension probe")
            self._dim = len(vec)
        return self._dim


# ---------------------------------------------------------------------------
# Embedding service with caching
# ---------------------------------------------------------------------------


class EmbeddingService:
    """Unified embedding service with LRU cache.

    Parameters
    ----------
    provider:
        A :class:`BaseEmbedder` instance.  If ``None``, creates a default
        :class:`SentenceTransformerEmbedder`.
    cache_size:
        Maximum number of cached embeddings (LRU eviction).
    """

    def __init__(
        self,
        provider: BaseEmbedder | None = None,
        *,
        cache_size: int = 10_000,
    ) -> None:
        self._provider = provider or SentenceTransformerEmbedder()
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_size = cache_size
        self._cache_hits = 0
        self._cache_misses = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of documents, using the cache when possible."""
        if not texts:
            return []

        results: list[list[float]] = [[] for _ in range(len(texts))]
        needs_encode: dict[str, list[int]] = {}  # text_hash -> list of result indices

        for i, text in enumerate(texts):
            key = _hash_text(text)

            # 1. Check persistent LRU cache
            if key in self._cache:
                results[i] = self._cache[key]
                self._cache_hits += 1
                self._cache.move_to_end(key)
                continue

            # 2. Group by unique text for batch encoding
            if key not in needs_encode:
                needs_encode[key] = []
            needs_encode[key].append(i)

        # Encode unique uncached texts in one batch
        if needs_encode:
            unique_texts: list[str] = []
            key_to_text: dict[str, str] = {}
            for key in needs_encode:
                # Find the actual text for this key from the input
                first_idx = needs_encode[key][0]
                text = texts[first_idx]
                unique_texts.append(text)
                key_to_text[key] = text

            unique_embs = self._provider.embed_documents(unique_texts)

            for key, emb in zip(needs_encode, unique_embs):
                # Store in persistent cache
                self._cache[key] = emb
                self._cache.move_to_end(key)
                self._cache_misses += 1

                # Fill all result positions that share this text
                for idx in needs_encode[key]:
                    results[idx] = emb

        # Evict oldest entries if cache exceeds limit
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

        if needs_encode:
            logger.debug(
                "EmbeddingService: cache_hits=%d misses=%d size=%d",
                self._cache_hits,
                self._cache_misses,
                len(self._cache),
            )

        return results

    def embed_query(self, query: str) -> list[float]:
        """Encode a single query string (always goes to the provider — no cache)."""
        return self._provider.embed_query(query)

    @property
    def dim(self) -> int:
        return self._provider.dim

    @property
    def cache_stats(self) -> dict[str, int]:
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "size": len(self._cache),
        }

    def __repr__(self) -> str:
        return (
            f"EmbeddingService(provider={type(self._provider).__name__}, "
            f"dim={self.dim}, cache={len(self._cache)}/{self._cache_size})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_text(text: str) -> str:
    """SHA-256 of *text*, truncated to 16 hex chars — enough to avoid collisions."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0:
        return vec
    return [v / norm for v in vec]
