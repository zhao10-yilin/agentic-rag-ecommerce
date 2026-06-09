"""Hybrid retrieval fusing dense (semantic) and sparse (keyword) search.

``HybridRetriever``
    Combines results from :class:`ChromaVectorStore` and :class:`SQLiteFTSStore`
    using **Reciprocal Rank Fusion (RRF)**.

    RRF is parameter-free and robust: it doesn't require score calibration
    between the two indexes.  Each retrieved item gets a rank from each index,
    and the fused score is::

        score(chunk) = Σ  1 / (k + rank_i)

    where *k* is typically 60.

``Reranker``
    Refines the top-*n* candidates from hybrid retrieval using a cross-encoder
    model (``BAAI/bge-reranker-large`` by default).  The cross-encoder takes
    ``(query, document)`` pairs and outputs a scalar relevance score, which is
    far more accurate than bi-encoder cosine similarity — at the cost of being
    too slow to run over the full corpus.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pdf_parser.rag.models import DocumentChunk, QueryPlan, RetrievalResult
from pdf_parser.rag.vector_store import (
    BaseVectorStore,
    ChromaVectorStore,
    SQLiteFTSStore,
)

logger = logging.getLogger(__name__)

# RRF constant — higher values favour top-ranked items less aggressively
RRF_K = 60

# Weight of rerank score vs hybrid score in final_score
RERANK_WEIGHT = 0.7
HYBRID_WEIGHT = 0.3


# ---------------------------------------------------------------------------
# Hybrid Retriever
# ---------------------------------------------------------------------------


class HybridRetriever:
    """Two-stage retrieval: dense + sparse → RRF fusion.

    Parameters
    ----------
    dense_store:
        Vector index for semantic search.
    sparse_store:
        Full-text index for keyword search.
    embed_query_fn:
        Callable ``(text: str) -> list[float]`` that produces a query embedding.
    """

    def __init__(
        self,
        dense_store: BaseVectorStore,
        sparse_store: SQLiteFTSStore,
        *,
        embed_query_fn: Any = None,
    ) -> None:
        self._dense = dense_store
        self._sparse = sparse_store
        self._embed_query = embed_query_fn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query_plan: QueryPlan,
        *,
        top_k: int = 100,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievalResult]:
        """Execute hybrid retrieval for all queries in the query plan.

        Each rewritten query (plus the original) is searched independently
        and the results are merged via RRF.

        Returns candidates ordered by *hybrid_score* descending.
        """
        t0 = time.perf_counter()

        queries = [query_plan.original_query] + query_plan.rewritten_queries
        if query_plan.hyde_doc:
            queries.append(query_plan.hyde_doc)

        # Collect RRF-ranked candidates across all query variants
        rrf_scores: dict[str, float] = {}
        chunk_cache: dict[str, DocumentChunk] = {}

        for q in queries:
            query_results = self._single_query(q, top_k=top_k, filters=filters)
            for rank, (chunk, dense_s, sparse_s) in enumerate(query_results, start=1):
                rrf_scores[chunk.chunk_id] = rrf_scores.get(chunk.chunk_id, 0.0) + (
                    1.0 / (RRF_K + rank)
                )
                if chunk.chunk_id not in chunk_cache:
                    chunk_cache[chunk.chunk_id] = chunk

        # Sort by RRF score descending
        sorted_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)  # type: ignore[arg-type]
        sorted_ids = sorted_ids[:top_k]

        results: list[RetrievalResult] = []
        for cid in sorted_ids:
            chunk = chunk_cache[cid]
            results.append(
                RetrievalResult(
                    chunk=chunk,
                    dense_score=0.0,
                    sparse_score=0.0,
                    hybrid_score=rrf_scores[cid],
                )
            )

        elapsed = time.perf_counter() - t0
        logger.info(
            "Hybrid retrieval: %d queries → %d unique candidates in %.2fs",
            len(queries),
            len(results),
            elapsed,
        )
        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _single_query(
        self,
        query: str,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[tuple[DocumentChunk, float, float]]:
        """Run dense + sparse search for one query, fuse with RRF."""
        dense_results: dict[str, tuple[DocumentChunk, float]] = {}
        sparse_results: dict[str, tuple[DocumentChunk, float]] = {}

        # Dense search
        if self._embed_query:
            try:
                q_emb = self._embed_query(query)
                for chunk_id, score in self._dense.dense_search(
                    q_emb, top_k=top_k, filters=filters
                ):
                    chunk = self._dense.get_by_chunk_id(chunk_id)
                    if chunk:
                        dense_results[chunk_id] = (chunk, score)
            except Exception:
                logger.exception("Dense search failed for query: %s", query[:80])

        # Sparse search
        try:
            for chunk_id, score in self._sparse.sparse_search(
                query, top_k=top_k, filters=filters
            ):
                chunk = self._sparse.get_by_chunk_id(chunk_id)
                if chunk:
                    sparse_results[chunk_id] = (chunk, score)
        except Exception:
            logger.exception("Sparse search failed for query: %s", query[:80])

        # RRF fusion
        all_ids = set(dense_results) | set(sparse_results)
        rrf_scored: list[tuple[float, str, DocumentChunk, float, float]] = []

        for cid in all_ids:
            dense_chunk, dense_s = dense_results.get(cid, (None, 0.0))
            sparse_chunk, sparse_s = sparse_results.get(cid, (None, 0.0))
            chunk = dense_chunk or sparse_chunk
            if chunk is None:
                continue

            rrf = 0.0
            # Find rank in dense results
            dense_rank = _find_rank(cid, dense_results)
            if dense_rank is not None:
                rrf += 1.0 / (RRF_K + dense_rank)
            # Find rank in sparse results
            sparse_rank = _find_rank(cid, sparse_results)
            if sparse_rank is not None:
                rrf += 1.0 / (RRF_K + sparse_rank)

            rrf_scored.append((rrf, cid, chunk, dense_s, sparse_s))

        # Sort by RRF descending
        rrf_scored.sort(key=lambda x: x[0], reverse=True)
        return [
            (chunk, dense_s, sparse_s)
            for (_, _, chunk, dense_s, sparse_s) in rrf_scored[:top_k]
        ]


def _find_rank(
    chunk_id: str,
    results: dict[str, tuple[DocumentChunk, float]],
) -> int | None:
    """Return the 1-based rank of *chunk_id* when results are sorted by score."""
    sorted_items = sorted(results.items(), key=lambda x: x[1][1], reverse=True)
    for rank, (cid, _) in enumerate(sorted_items, start=1):
        if cid == chunk_id:
            return rank
    return None


# ---------------------------------------------------------------------------
# Reranker — cross-encoder refinement
# ---------------------------------------------------------------------------


class Reranker:
    """Cross-encoder re-ranker for refining hybrid retrieval results.

    Uses ``BAAI/bge-reranker-large`` (or any ``sentence-transformers``
    cross-encoder) to score ``(query, document)`` pairs and re-order the
    candidate list.

    Parameters
    ----------
    model_name:
        HuggingFace cross-encoder model identifier.
    device:
        ``'cpu'``, ``'cuda'``, or ``None`` (auto-detect).
    batch_size:
        Number of (query, doc) pairs to score per forward pass.
    """

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-reranker-large",
        device: str | None = None,
        batch_size: int = 32,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._model: Any = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return

        from sentence_transformers import CrossEncoder

        logger.info("Loading reranker model: %s", self._model_name)
        t0 = time.perf_counter()
        self._model = CrossEncoder(
            self._model_name,
            device=self._device,
        )
        logger.info("Reranker loaded in %.1fs", time.perf_counter() - t0)

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
        *,
        top_n: int = 10,
    ) -> list[RetrievalResult]:
        """Re-rank *candidates* and return the top *top_n*.

        Each candidate's ``rerank_score`` and ``final_score`` are updated
        in-place (and the list itself is re-ordered).
        """
        if not candidates:
            return candidates

        self._ensure_model()

        t0 = time.perf_counter()
        pairs: list[tuple[str, str]] = [(query, r.chunk.text) for r in candidates]

        scores: list[float] = self._model.predict(
            pairs,
            batch_size=self._batch_size,
            show_progress_bar=False,
        ).tolist()  # type: ignore[assignment]

        # Normalise scores to [0, 1]
        min_s = min(scores)
        max_s = max(scores)
        span = max_s - min_s if max_s != min_s else 1.0

        for result, raw_score in zip(candidates, scores):
            result.rerank_score = (raw_score - min_s) / span
            result.final_score = (
                RERANK_WEIGHT * result.rerank_score
                + HYBRID_WEIGHT * result.hybrid_score
            )

        candidates.sort(key=lambda r: r.final_score, reverse=True)

        elapsed = time.perf_counter() - t0
        logger.info("Reranked %d candidates → top-%d in %.2fs", len(candidates), top_n, elapsed)
        return candidates[:top_n]
