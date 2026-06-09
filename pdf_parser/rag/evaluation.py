"""Retrieval quality evaluation with human-annotated relevance judgments.

Provides a complete evaluation pipeline:

1. :class:`EvaluationDataset` — manage human-labeled (query, chunk) pairs
2. :class:`RetrievalEvaluator` — run queries, compute metrics
3. :class:`RetrievalMetrics` — NDCG@k, MRR, Recall@k, Precision@k

Human annotation workflow
-------------------------

**Phase 1 — Generate candidate queries**::

    python -m pdf_parser.rag.eval_cli generate-queries \\
        --num 20 --output queries_20260508.jsonl

**Phase 2 — Annotate relevance** (interactive)::

    python -m pdf_parser.rag.eval_cli annotate \\
        --queries queries_20260508.jsonl \\
        --output labeled_dataset.jsonl

    # For each query, the tool shows the retrieved chunks and asks:
    #   Relevance score (0-3): [0] irrelevant, [1] marginally, [2] relevant, [3] highly

**Phase 3 — Run evaluation**::

    python -m pdf_parser.rag.eval_cli evaluate \\
        --dataset labeled_dataset.jsonl \\
        --output report.json

**Phase 4 — Compare runs** (after tweaking chunk size / model)::

    python -m pdf_parser.rag.eval_cli compare \\
        --baseline eval_run_1.json \\
        --candidate eval_run_2.json

Relevance scale
---------------

==== =========================================================
  0  **Irrelevant** — the chunk has nothing to do with the query.
  1  **Marginally relevant** — touches the topic but doesn't
     answer the question.
  2  **Relevant** — contains useful information that partially
     answers the query.
  3  **Highly relevant** — directly answers the query; this is
     the chunk the user wanted to find.
==== =========================================================
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdf_parser.rag.models import DocumentChunk, QueryPlan, RetrievalResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class RelevanceJudgment:
    """A single human-labeled relevance score for a (query, chunk) pair."""

    query_id: str
    query_text: str
    chunk_id: str
    relevance: int  # 0-3 scale
    chunk_text_snippet: str = ""  # first 200 chars for context
    notes: str = ""  # annotator notes (optional)


@dataclass
class EvalQuery:
    """A query with its relevance judgments."""

    query_id: str
    query_text: str
    judgments: list[RelevanceJudgment] = field(default_factory=list)

    @property
    def relevant_chunks(self) -> dict[str, int]:
        """Return ``{chunk_id: relevance}`` for judged chunks."""
        return {j.chunk_id: j.relevance for j in self.judgments}


@dataclass
class RetrievalMetrics:
    """Aggregated metrics for a single evaluation run."""

    num_queries: int = 0
    # NDCG@k
    ndcg_at_1: float = 0.0
    ndcg_at_5: float = 0.0
    ndcg_at_10: float = 0.0
    ndcg_at_20: float = 0.0
    # MRR
    mrr: float = 0.0
    # Recall@k
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    recall_at_20: float = 0.0
    # Precision@k
    precision_at_5: float = 0.0
    precision_at_10: float = 0.0
    # Per-query breakdown
    per_query: list[dict[str, Any]] = field(default_factory=list)
    # Metadata
    elapsed_seconds: float = 0.0
    config_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "num_queries": self.num_queries,
            "ndcg": {
                "@1": round(self.ndcg_at_1, 4),
                "@5": round(self.ndcg_at_5, 4),
                "@10": round(self.ndcg_at_10, 4),
                "@20": round(self.ndcg_at_20, 4),
            },
            "mrr": round(self.mrr, 4),
            "recall": {
                "@5": round(self.recall_at_5, 4),
                "@10": round(self.recall_at_10, 4),
                "@20": round(self.recall_at_20, 4),
            },
            "precision": {
                "@5": round(self.precision_at_5, 4),
                "@10": round(self.precision_at_10, 4),
            },
            "per_query": self.per_query,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }

    def summary(self) -> str:
        """Return a human-readable one-line summary."""
        return (
            f"NDCG@10={self.ndcg_at_10:.3f}  "
            f"MRR={self.mrr:.3f}  "
            f"Recall@10={self.recall_at_10:.3f}  "
            f"Precision@10={self.precision_at_10:.3f}  "
            f"({self.num_queries} queries)"
        )


# ---------------------------------------------------------------------------
# Evaluation dataset
# ---------------------------------------------------------------------------


class EvaluationDataset:
    """Manage a collection of human-labeled relevance judgments.

    The dataset is stored as JSONL (one line per :class:`EvalQuery`)::

        {"query_id": "q001", "query_text": "违约是什么", "judgments": [
            {"query_id": "q001", "query_text": "违约是什么",
             "chunk_id": "abc123", "relevance": 3,
             "chunk_text_snippet": "违约责任是指...", "notes": ""}
        ]}
    """

    def __init__(self) -> None:
        self.queries: dict[str, EvalQuery] = {}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_query(self, query_id: str, query_text: str) -> EvalQuery:
        """Register a new query (or return existing)."""
        if query_id not in self.queries:
            self.queries[query_id] = EvalQuery(
                query_id=query_id, query_text=query_text
            )
        return self.queries[query_id]

    def add_judgment(
        self,
        query_id: str,
        query_text: str,
        chunk_id: str,
        relevance: int,
        *,
        chunk_text_snippet: str = "",
        notes: str = "",
    ) -> RelevanceJudgment:
        """Add or update a relevance judgment."""
        if relevance not in (0, 1, 2, 3):
            raise ValueError(f"Relevance must be 0-3, got {relevance}")

        q = self.add_query(query_id, query_text)

        # Upsert: replace if chunk_id already judged
        existing = [j for j in q.judgments if j.chunk_id == chunk_id]
        for j in existing:
            q.judgments.remove(j)

        judgment = RelevanceJudgment(
            query_id=query_id,
            query_text=query_text,
            chunk_id=chunk_id,
            relevance=relevance,
            chunk_text_snippet=chunk_text_snippet[:200],
            notes=notes,
        )
        q.judgments.append(judgment)
        return judgment

    def get_query(self, query_id: str) -> EvalQuery | None:
        return self.queries.get(query_id)

    def get_judgments_for(self, query_id: str) -> dict[str, int]:
        """Return ``{chunk_id: relevance}`` for a query."""
        q = self.queries.get(query_id)
        if q is None:
            return {}
        return q.relevant_chunks

    @property
    def num_queries(self) -> int:
        return len(self.queries)

    @property
    def total_judgments(self) -> int:
        return sum(len(q.judgments) for q in self.queries.values())

    def stats(self) -> dict[str, Any]:
        """Return summary statistics."""
        if not self.queries:
            return {"num_queries": 0, "total_judgments": 0}

        rel_counts = {0: 0, 1: 0, 2: 0, 3: 0}
        for q in self.queries.values():
            for j in q.judgments:
                rel_counts[j.relevance] += 1

        queries_with_relevant = sum(
            1 for q in self.queries.values()
            if any(j.relevance >= 2 for j in q.judgments)
        )

        return {
            "num_queries": self.num_queries,
            "total_judgments": self.total_judgments,
            "avg_judgments_per_query": round(
                self.total_judgments / max(self.num_queries, 1), 1
            ),
            "relevance_distribution": rel_counts,
            "queries_with_relevant_chunks": queries_with_relevant,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Write dataset to a JSONL file."""
        path = Path(path)
        with path.open("w", encoding="utf-8") as fh:
            for q in self.queries.values():
                record = {
                    "query_id": q.query_id,
                    "query_text": q.query_text,
                    "judgments": [
                        {
                            "query_id": j.query_id,
                            "query_text": j.query_text,
                            "chunk_id": j.chunk_id,
                            "relevance": j.relevance,
                            "chunk_text_snippet": j.chunk_text_snippet,
                            "notes": j.notes,
                        }
                        for j in q.judgments
                    ],
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info(
            "Saved evaluation dataset: %d queries, %d judgments → %s",
            self.num_queries,
            self.total_judgments,
            path,
        )

    @classmethod
    def load(cls, path: str | Path) -> EvaluationDataset:
        """Load dataset from a JSONL file."""
        dataset = cls()
        path = Path(path)
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                q = dataset.add_query(record["query_id"], record["query_text"])
                for j_data in record.get("judgments", []):
                    dataset.add_judgment(
                        query_id=j_data["query_id"],
                        query_text=j_data.get("query_text", record["query_text"]),
                        chunk_id=j_data["chunk_id"],
                        relevance=j_data["relevance"],
                        chunk_text_snippet=j_data.get("chunk_text_snippet", ""),
                        notes=j_data.get("notes", ""),
                    )
        logger.info(
            "Loaded evaluation dataset: %d queries, %d judgments",
            dataset.num_queries,
            dataset.total_judgments,
        )
        return dataset


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------


def _dcg(relevances: list[int], k: int) -> float:
    """Discounted Cumulative Gain at rank k."""
    dcg = 0.0
    for i, rel in enumerate(relevances[:k]):
        # rel = 0 → gain = 0; rel = 1-3 → gain = 2^rel - 1 = 1, 3, 7
        gain = (2 ** rel) - 1 if rel > 0 else 0
        dcg += gain / math.log2(i + 2)  # i+2 because log2(1) = 0 for first position
    return dcg


def _ndcg(retrieved_relevances: list[int], ideal_relevances: list[int], k: int) -> float:
    """Normalized DCG: DCG / IDCG."""
    dcg_val = _dcg(retrieved_relevances, k)
    idcg_val = _dcg(sorted(ideal_relevances, reverse=True), k)
    if idcg_val == 0:
        return 0.0
    return dcg_val / idcg_val


def _mrr(retrieved_chunks: list[str], relevant_chunks: dict[str, int]) -> float:
    """Mean Reciprocal Rank.

    Returns the reciprocal of the rank of the first relevant (score >= 2) chunk.
    """
    for rank, cid in enumerate(retrieved_chunks, start=1):
        if relevant_chunks.get(cid, 0) >= 2:
            return 1.0 / rank
    return 0.0


def _recall(
    retrieved_chunks: list[str],
    relevant_chunks: dict[str, int],
    k: int,
    min_relevance: int = 2,
) -> float:
    """Recall@k: fraction of relevant chunks found in top-k results."""
    total_relevant = sum(1 for r in relevant_chunks.values() if r >= min_relevance)
    if total_relevant == 0:
        return 0.0
    found = sum(
        1 for cid in retrieved_chunks[:k]
        if relevant_chunks.get(cid, 0) >= min_relevance
    )
    return found / total_relevant


def _precision(
    retrieved_chunks: list[str],
    relevant_chunks: dict[str, int],
    k: int,
    min_relevance: int = 2,
) -> float:
    """Precision@k: fraction of top-k results that are relevant."""
    if k == 0:
        return 0.0
    found = sum(
        1 for cid in retrieved_chunks[:k]
        if relevant_chunks.get(cid, 0) >= min_relevance
    )
    return found / k


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class RetrievalEvaluator:
    """Run retrieval evaluation against a labeled dataset.

    Parameters
    ----------
    retriever:
        A :class:`HybridRetriever` instance (or any object with a
        ``retrieve(query_plan, top_k) → list[RetrievalResult]`` method).
    dataset:
        An :class:`EvaluationDataset` with human-labeled judgments.
    """

    def __init__(
        self,
        retriever: Any,
        dataset: EvaluationDataset,
    ) -> None:
        self._retriever = retriever
        self._dataset = dataset

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        *,
        k_values: tuple[int, ...] = (1, 5, 10, 20),
        config_snapshot: dict[str, Any] | None = None,
    ) -> RetrievalMetrics:
        """Run evaluation and return aggregated metrics.

        Parameters
        ----------
        k_values:
            Cutoff ranks to evaluate (NDCG@k, Recall@k, Precision@k).
        config_snapshot:
            Arbitrary config dict recorded in the result for later comparison
            (e.g. chunk size, embedding model name).
        """
        all_ids = {q.query_id for q in self._dataset.queries.values() if q.judgments}
        return self.evaluate_subset(
            all_ids,
            k_values=k_values,
            config_snapshot=config_snapshot,
        )

    def evaluate_subset(
        self,
        query_ids: set[str],
        *,
        k_values: tuple[int, ...] = (1, 5, 10, 20),
        config_snapshot: dict[str, Any] | None = None,
    ) -> RetrievalMetrics:
        """Run evaluation on a specific subset of queries.

        This is the low-level primitive used by train/test split and k-fold CV.
        """
        t0 = time.perf_counter()

        all_ndcg: dict[int, list[float]] = {k: [] for k in k_values}
        all_recall: dict[int, list[float]] = {k: [] for k in k_values if k > 1}
        all_precision: dict[int, list[float]] = {k: [] for k in k_values if k > 1}
        all_mrr: list[float] = []
        per_query_results: list[dict[str, Any]] = []

        max_k = max(k_values)

        for q in self._dataset.queries.values():
            if q.query_id not in query_ids:
                continue
            if not q.judgments:
                continue

            relevant = q.relevant_chunks

            try:
                plan = QueryPlan(original_query=q.query_text)
                results = self._retriever.retrieve(plan, top_k=max_k)
                retrieved_ids = [r.chunk.chunk_id for r in results]
            except Exception:
                logger.exception("Retrieval failed for query %s", q.query_id)
                retrieved_ids = []

            retrieved_relevances = [
                relevant.get(cid, 0) for cid in retrieved_ids
            ]
            ideal_relevances = sorted(relevant.values(), reverse=True)

            query_metrics: dict[str, Any] = {
                "query_id": q.query_id,
                "query_text": q.query_text[:100],
                "num_judgments": len(q.judgments),
                "num_relevant": sum(1 for r in relevant.values() if r >= 2),
                "retrieved_count": len(retrieved_ids),
            }

            for k in k_values:
                ndcg_val = _ndcg(retrieved_relevances, ideal_relevances, k)
                all_ndcg[k].append(ndcg_val)
                query_metrics[f"ndcg@{k}"] = round(ndcg_val, 4)

            for k in k_values:
                if k > 1:
                    rec = _recall(retrieved_ids, relevant, k)
                    all_recall[k].append(rec)
                    query_metrics[f"recall@{k}"] = round(rec, 4)

                    prec = _precision(retrieved_ids, relevant, k)
                    all_precision[k].append(prec)
                    query_metrics[f"precision@{k}"] = round(prec, 4)

            mrr_val = _mrr(retrieved_ids, relevant)
            all_mrr.append(mrr_val)
            query_metrics["mrr"] = round(mrr_val, 4)

            per_query_results.append(query_metrics)

        elapsed = time.perf_counter() - t0
        metrics = _build_metrics(
            all_ndcg, all_recall, all_precision, all_mrr,
            per_query_results, k_values,
            elapsed_seconds=elapsed,
            config_snapshot=config_snapshot or {},
        )
        return metrics

    @staticmethod
    def compare(baseline: RetrievalMetrics, candidate: RetrievalMetrics) -> str:
        """Produce a human-readable comparison between two evaluation runs."""
        lines = [
            f"{'Metric':<20} {'Baseline':>10} {'Candidate':>10} {'Delta':>10}",
            "-" * 50,
        ]
        fields = [
            ("NDCG@10", baseline.ndcg_at_10, candidate.ndcg_at_10),
            ("NDCG@5", baseline.ndcg_at_5, candidate.ndcg_at_5),
            ("MRR", baseline.mrr, candidate.mrr),
            ("Recall@10", baseline.recall_at_10, candidate.recall_at_10),
            ("Precision@10", baseline.precision_at_10, candidate.precision_at_10),
        ]
        for name, base, cand in fields:
            delta = cand - base
            sign = "+" if delta >= 0 else ""
            lines.append(f"{name:<20} {base:>10.4f} {cand:>10.4f} {sign}{delta:>9.4f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Train / test split — stratified by file_id
# ---------------------------------------------------------------------------


def train_test_split(
    dataset: EvaluationDataset,
    *,
    test_size: float = 0.3,
    seed: int = 42,
    query_file_ids: dict[str, set[str]] | None = None,
) -> tuple[EvaluationDataset, EvaluationDataset]:
    """Split a dataset into train and test subsets.

    When *query_file_ids* is provided, queries are grouped by the files
    they reference, and the split is stratified so that queries touching
    the same files stay together.  This prevents information leakage:
    chunks from the same document share embedding-space neighbourhoods,
    so shuffling them randomly between train and test inflates scores.

    Without *query_file_ids* the split falls back to simple random
    sampling (weaker, but requires no vector-store lookup).

    Parameters
    ----------
    dataset:
        The full evaluation dataset.
    test_size:
        Fraction of queries reserved for testing (default 0.3).
    seed:
        Random seed for reproducibility.
    query_file_ids:
        Optional ``{query_id: {file_id, ...}}`` mapping.  When provided,
        the split is stratified by the **union** of file_ids across all
        queries that share at least one file.

    Returns
    -------
    ``(train_dataset, test_dataset)`` — two independent :class:`EvaluationDataset`
    instances with no overlapping file groups.
    """
    import random as _random

    rng = _random.Random(seed)
    queries = list(dataset.queries.values())

    if not queries:
        return EvaluationDataset(), EvaluationDataset()

    if query_file_ids:
        # ---- Stratified split --------------------------------------------
        # Build file→queries index
        file_to_queries: dict[str, set[str]] = {}
        for q in queries:
            fids = query_file_ids.get(q.query_id, set())
            for fid in fids:
                file_to_queries.setdefault(fid, set()).add(q.query_id)

        # Connected components: queries sharing any file_id are merged
        parent: dict[str, str] = {}

        def _find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def _union(a: str, b: str) -> None:
            ra, rb = _find(a), _find(b)
            if ra != rb:
                parent[ra] = rb

        for q in queries:
            parent[q.query_id] = q.query_id

        for fid, qids in file_to_queries.items():
            qid_list = list(qids)
            for i in range(1, len(qid_list)):
                _union(qid_list[0], qid_list[i])

        # Group queries by connected component
        components: dict[str, list[str]] = {}
        for q in queries:
            root = _find(q.query_id)
            components.setdefault(root, []).append(q.query_id)

        # Shuffle components and assign to train/test
        comp_items = list(components.values())
        rng.shuffle(comp_items)

        test_count = 0
        test_ids: set[str] = set()
        for comp in comp_items:
            if test_count + len(comp) <= int(len(queries) * test_size):
                test_ids.update(comp)
                test_count += len(comp)
            else:
                break

    else:
        # ---- Simple random split (no stratification) --------------------
        qids = [q.query_id for q in queries if q.judgments]
        rng.shuffle(qids)
        split_at = int(len(qids) * (1 - test_size))
        train_ids_list = qids[:split_at]
        test_ids = set(qids[split_at:])

        train_ds = EvaluationDataset()
        for qid in train_ids_list:
            q = dataset.queries[qid]
            train_ds.add_query(qid, q.query_text)
            for j in q.judgments:
                train_ds.add_judgment(
                    query_id=j.query_id, query_text=j.query_text,
                    chunk_id=j.chunk_id, relevance=j.relevance,
                    chunk_text_snippet=j.chunk_text_snippet, notes=j.notes,
                )

        test_ds = EvaluationDataset()
        for qid in test_ids:
            q = dataset.queries[qid]
            test_ds.add_query(qid, q.query_text)
            for j in q.judgments:
                test_ds.add_judgment(
                    query_id=j.query_id, query_text=j.query_text,
                    chunk_id=j.chunk_id, relevance=j.relevance,
                    chunk_text_snippet=j.chunk_text_snippet, notes=j.notes,
                )
        return train_ds, test_ds

    # Build train/test datasets
    all_qids = {q.query_id for q in queries}
    train_ids_set = all_qids - test_ids

    train_ds = EvaluationDataset()
    for qid in train_ids_set:
        q = dataset.queries[qid]
        train_ds.add_query(qid, q.query_text)
        for j in q.judgments:
            train_ds.add_judgment(
                query_id=j.query_id, query_text=j.query_text,
                chunk_id=j.chunk_id, relevance=j.relevance,
                chunk_text_snippet=j.chunk_text_snippet, notes=j.notes,
            )

    test_ds = EvaluationDataset()
    for qid in test_ids:
        q = dataset.queries[qid]
        test_ds.add_query(qid, q.query_text)
        for j in q.judgments:
            test_ds.add_judgment(
                query_id=j.query_id, query_text=j.query_text,
                chunk_id=j.chunk_id, relevance=j.relevance,
                chunk_text_snippet=j.chunk_text_snippet, notes=j.notes,
            )

    logger.info(
        "Train/test split: %d train / %d test (stratified=%s, seed=%d)",
        train_ds.num_queries, test_ds.num_queries,
        bool(query_file_ids), seed,
    )
    return train_ds, test_ds


# ---------------------------------------------------------------------------
# K-fold cross-validation
# ---------------------------------------------------------------------------


def kfold_split(
    dataset: EvaluationDataset,
    *,
    n_folds: int = 5,
    seed: int = 42,
) -> list[tuple[EvaluationDataset, EvaluationDataset]]:
    """Generate *n_folds* train/test pairs for cross-validation.

    Queries are shuffled (not stratified — with small datasets, stratification
    by file_id often fails because each fold would get too few unique files to
    be meaningful).  For reliable stratification, use :func:`train_test_split`
    with ``query_file_ids`` and a sufficiently large dataset.

    Yields
    ------
    ``[(train_0, test_0), ..., (train_{k-1}, test_{k-1})]``
    """
    import random as _random

    rng = _random.Random(seed)
    queries = [q for q in dataset.queries.values() if q.judgments]
    rng.shuffle(queries)

    if len(queries) < n_folds:
        n_folds = max(2, len(queries))
        logger.warning(
            "Too few queries (%d) for %d folds — reducing to %d",
            len(queries), n_folds, n_folds,
        )

    fold_size = len(queries) // n_folds
    folds: list[tuple[EvaluationDataset, EvaluationDataset]] = []

    for i in range(n_folds):
        start = i * fold_size
        end = start + fold_size if i < n_folds - 1 else len(queries)

        test_queries = queries[start:end]
        train_queries = queries[:start] + queries[end:]

        test_ids = {q.query_id for q in test_queries}
        train_ids = {q.query_id for q in train_queries}

        train_ds = _build_subset(dataset, train_ids)
        test_ds = _build_subset(dataset, test_ids)
        folds.append((train_ds, test_ds))

    return folds


def _build_subset(dataset: EvaluationDataset, query_ids: set[str]) -> EvaluationDataset:
    """Build a new dataset containing only *query_ids*."""
    sub = EvaluationDataset()
    for qid in query_ids:
        q = dataset.queries.get(qid)
        if q is None:
            continue
        sub.add_query(qid, q.query_text)
        for j in q.judgments:
            sub.add_judgment(
                query_id=j.query_id, query_text=j.query_text,
                chunk_id=j.chunk_id, relevance=j.relevance,
                chunk_text_snippet=j.chunk_text_snippet, notes=j.notes,
            )
    return sub


# ---------------------------------------------------------------------------
# Paired t-test for statistical significance
# ---------------------------------------------------------------------------


def paired_ttest(
    per_query_a: list[float],
    per_query_b: list[float],
    *,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Perform a two-tailed paired t-test.

    Assumes *per_query_a* and *per_query_b* are aligned — position *i* in
    both lists corresponds to the same query evaluated under two different
    system configurations (e.g. RRF k=60 vs k=30).

    Returns a dict with the t-statistic, p-value, and a verdict.
    """
    if len(per_query_a) != len(per_query_b):
        raise ValueError(
            f"Paired arrays must have equal length, got {len(per_query_a)} vs {len(per_query_b)}"
        )
    n = len(per_query_a)
    if n < 2:
        return {
            "t_statistic": 0.0,
            "p_value": 1.0,
            "significant": False,
            "n": n,
            "mean_diff": 0.0,
            "std_diff": 0.0,
            "message": "Insufficient data (need at least 2 paired observations)",
        }

    diffs = [a - b for a, b in zip(per_query_a, per_query_b)]
    mean_diff = sum(diffs) / n

    # Standard deviation of differences (Bessel's correction, ddof=1)
    if n > 1:
        std_diff = (sum((d - mean_diff) ** 2 for d in diffs) / (n - 1)) ** 0.5
    else:
        std_diff = 0.0

    if std_diff == 0.0:
        t_stat = 0.0
        p_value = 1.0
    else:
        t_stat = mean_diff / (std_diff / math.sqrt(n))
        df = n - 1
        try:
            from scipy import stats

            # Two-tailed: p = 2 * P(T > |t|) = 2 * sf(|t|, df)
            p_value = 2.0 * float(stats.t.sf(abs(t_stat), df))
        except ImportError:
            # Fallback: use a simple approximation for large df
            # For df > 30, t ≈ z (standard normal)
            # For smaller df, this is approximate
            z = abs(t_stat)
            # Standard normal two-tailed p-value approximation
            # 1 - erf(z/sqrt(2)) ≈ exp(-z^2/2) / (z * sqrt(2*pi))
            p_approx = math.exp(-z * z / 2.0)
            if z > 0:
                p_approx = p_approx / (z * math.sqrt(2.0 * math.pi))
            p_value = min(1.0, max(0.0, 2.0 * p_approx))
            if df < 30:
                # Adjust: t has fatter tails than z, so increase p slightly
                p_value = min(1.0, p_value * (1.0 + 2.0 / df))

    significant = p_value < alpha

    return {
        "t_statistic": round(t_stat, 4),
        "p_value": round(p_value, 6),
        "significant": significant,
        "n": n,
        "mean_diff": round(mean_diff, 6),
        "std_diff": round(std_diff, 6),
        "message": (
            f"Statistically significant (p={p_value:.4f} < {alpha})"
            if significant
            else f"NOT significant (p={p_value:.4f} >= {alpha})"
        ),
    }


# ---------------------------------------------------------------------------
# Cross-validation runner
# ---------------------------------------------------------------------------


def run_cross_validation(
    build_retriever_fn: Any,
    dataset: EvaluationDataset,
    *,
    n_folds: int = 5,
    seed: int = 42,
    k_values: tuple[int, ...] = (1, 5, 10, 20),
) -> dict[str, Any]:
    """Run k-fold cross-validation and return aggregated results.

    Parameters
    ----------
    build_retriever_fn:
        A callable ``() → retriever`` that creates a fresh retriever for
        each fold.  This is important — if the retriever has mutable state
        (e.g. cached embeddings), a new instance per fold prevents leakage.
    dataset:
        The full evaluation dataset.
    n_folds:
        Number of cross-validation folds (default 5).
    seed:
        Random seed.
    k_values:
        Cutoff ranks.

    Returns
    -------
    A dict with per-fold metrics, mean, and std for each key metric.
    """
    folds = kfold_split(dataset, n_folds=n_folds, seed=seed)

    all_fold_metrics: list[RetrievalMetrics] = []
    per_query_collector: dict[str, list[float]] = {}

    for fi, (train_ds, test_ds) in enumerate(folds):
        retriever = build_retriever_fn()
        evaluator = RetrievalEvaluator(retriever, test_ds)
        metrics = evaluator.evaluate(k_values=k_values)

        all_fold_metrics.append(metrics)

        for pq in metrics.per_query:
            qid = pq["query_id"]
            ndcg = pq.get("ndcg@10", 0.0)
            per_query_collector.setdefault(qid, []).append(ndcg)

        logger.info(
            "Fold %d/%d: %s",
            fi + 1, n_folds, metrics.summary(),
        )

    # Aggregate
    def _mean_std(vals: list[float]) -> tuple[float, float]:
        if not vals:
            return 0.0, 0.0
        m = sum(vals) / len(vals)
        if len(vals) > 1:
            s = (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5
        else:
            s = 0.0
        return m, s

    ndcg_vals = [m.ndcg_at_10 for m in all_fold_metrics]
    mrr_vals = [m.mrr for m in all_fold_metrics]
    recall_vals = [m.recall_at_10 for m in all_fold_metrics]

    ndcg_mean, ndcg_std = _mean_std(ndcg_vals)
    mrr_mean, mrr_std = _mean_std(mrr_vals)
    recall_mean, recall_std = _mean_std(recall_vals)

    return {
        "n_folds": n_folds,
        "total_queries": sum(m.num_queries for m in all_fold_metrics),
        "fold_metrics": [m.to_dict() for m in all_fold_metrics],
        "aggregated": {
            "ndcg@10": {"mean": round(ndcg_mean, 4), "std": round(ndcg_std, 4)},
            "mrr": {"mean": round(mrr_mean, 4), "std": round(mrr_std, 4)},
            "recall@10": {"mean": round(recall_mean, 4), "std": round(recall_std, 4)},
        },
    }


# ---------------------------------------------------------------------------
# Utility: aggregate metrics from collected lists
# ---------------------------------------------------------------------------


def _build_metrics(
    all_ndcg: dict[int, list[float]],
    all_recall: dict[int, list[float]],
    all_precision: dict[int, list[float]],
    all_mrr: list[float],
    per_query_results: list[dict[str, Any]],
    k_values: tuple[int, ...],
    *,
    elapsed_seconds: float = 0.0,
    config_snapshot: dict[str, Any] | None = None,
) -> RetrievalMetrics:
    """Build a :class:`RetrievalMetrics` from collected per-query lists."""
    metrics = RetrievalMetrics(
        num_queries=len(all_mrr),
        elapsed_seconds=elapsed_seconds,
        config_snapshot=config_snapshot or {},
        per_query=per_query_results,
    )

    for k in k_values:
        vals = all_ndcg[k]
        avg = sum(vals) / len(vals) if vals else 0.0
        if k == 1:
            metrics.ndcg_at_1 = avg
        elif k == 5:
            metrics.ndcg_at_5 = avg
        elif k == 10:
            metrics.ndcg_at_10 = avg
        elif k == 20:
            metrics.ndcg_at_20 = avg

    for k in k_values:
        if k > 1:
            vals = all_recall.get(k, [])
            avg = sum(vals) / len(vals) if vals else 0.0
            if k == 5:
                metrics.recall_at_5 = avg
            elif k == 10:
                metrics.recall_at_10 = avg
            elif k == 20:
                metrics.recall_at_20 = avg

    for k in k_values:
        if k > 1:
            vals = all_precision.get(k, [])
            avg = sum(vals) / len(vals) if vals else 0.0
            if k == 5:
                metrics.precision_at_5 = avg
            elif k == 10:
                metrics.precision_at_10 = avg

    metrics.mrr = sum(all_mrr) / len(all_mrr) if all_mrr else 0.0
    return metrics
