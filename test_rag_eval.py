"""Tests for the retrieval evaluation module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest


# ---------------------------------------------------------------------------
# EvaluationDataset
# ---------------------------------------------------------------------------


class TestEvaluationDataset:
    @pytest.fixture
    def dataset(self):
        from pdf_parser.rag.evaluation import EvaluationDataset

        return EvaluationDataset()

    def test_add_query(self, dataset):
        q = dataset.add_query("q001", "什么是违约责任")
        assert q.query_id == "q001"
        assert q.query_text == "什么是违约责任"
        assert dataset.num_queries == 1

    def test_add_query_idempotent(self, dataset):
        dataset.add_query("q001", "什么是违约责任")
        dataset.add_query("q001", "不同的文本")  # won't overwrite
        assert dataset.num_queries == 1
        assert dataset.get_query("q001").query_text == "什么是违约责任"

    def test_add_judgment(self, dataset):
        dataset.add_judgment("q001", "违约", "chunk_a", 3)
        dataset.add_judgment("q001", "违约", "chunk_b", 1)

        assert dataset.total_judgments == 2
        judgments = dataset.get_judgments_for("q001")
        assert judgments["chunk_a"] == 3
        assert judgments["chunk_b"] == 1

    def test_add_judgment_upsert(self, dataset):
        """Adding a judgment for the same chunk should replace the old one."""
        dataset.add_judgment("q001", "违约", "chunk_a", 1)
        dataset.add_judgment("q001", "违约", "chunk_a", 3)  # upgrade

        assert dataset.total_judgments == 1
        assert dataset.get_judgments_for("q001")["chunk_a"] == 3

    def test_invalid_relevance(self, dataset):
        with pytest.raises(ValueError, match="must be 0-3"):
            dataset.add_judgment("q001", "test", "chunk_a", 5)

    def test_save_and_load(self, dataset, tmp_path):
        dataset.add_judgment("q001", "违约是什么", "abc", 3, chunk_text_snippet="违约责任是指...")
        dataset.add_judgment("q002", "合同的订立", "def", 2, chunk_text_snippet="合同订立需要...")

        path = tmp_path / "dataset.jsonl"
        dataset.save(path)

        loaded = dataset.__class__.load(path)
        assert loaded.num_queries == 2
        assert loaded.total_judgments == 2
        assert loaded.get_judgments_for("q001")["abc"] == 3
        # Verify snippet was preserved
        q1 = loaded.get_query("q001")
        assert q1.judgments[0].chunk_text_snippet == "违约责任是指..."

    def test_load_empty_file(self, dataset, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        loaded = dataset.__class__.load(path)
        assert loaded.num_queries == 0

    def test_stats(self, dataset):
        dataset.add_judgment("q001", "q1", "c1", 3)
        dataset.add_judgment("q001", "q1", "c2", 0)
        dataset.add_judgment("q002", "q2", "c3", 2)

        stats = dataset.stats()
        assert stats["num_queries"] == 2
        assert stats["total_judgments"] == 3
        assert stats["avg_judgments_per_query"] == 1.5
        assert stats["relevance_distribution"] == {0: 1, 1: 0, 2: 1, 3: 1}
        assert stats["queries_with_relevant_chunks"] == 2

    def test_get_nonexistent_query(self, dataset):
        assert dataset.get_query("no_such_id") is None
        assert dataset.get_judgments_for("no_such_id") == {}


# ---------------------------------------------------------------------------
# RetrievalMetrics
# ---------------------------------------------------------------------------


class TestRetrievalMetrics:
    def test_empty(self):
        from pdf_parser.rag.evaluation import RetrievalMetrics

        m = RetrievalMetrics()
        d = m.to_dict()
        assert d["num_queries"] == 0
        assert d["ndcg"]["@10"] == 0.0

    def test_summary(self):
        from pdf_parser.rag.evaluation import RetrievalMetrics

        m = RetrievalMetrics(num_queries=10, ndcg_at_10=0.75, mrr=0.80)
        assert "0.750" in m.summary()
        assert "0.800" in m.summary()


# ---------------------------------------------------------------------------
# Metrics functions
# ---------------------------------------------------------------------------


class TestDCG:
    def test_perfect_ordering(self):
        from pdf_parser.rag.evaluation import _dcg

        # [3, 2, 1, 0] — perfectly ordered
        dcg = _dcg([3, 2, 1, 0], 4)
        assert dcg > 0
        # gain(3)=7, gain(2)=3, gain(1)=1, gain(0)=0
        expected = 7 / 1.0 + 3 / math.log2(3) + 1 / math.log2(4) + 0
        assert abs(dcg - expected) < 0.001

    def test_worst_ordering(self):
        from pdf_parser.rag.evaluation import _dcg

        dcg = _dcg([0, 0, 0, 0], 4)
        assert dcg == 0.0

    def test_truncated(self):
        from pdf_parser.rag.evaluation import _dcg

        dcg_2 = _dcg([3, 2, 1], 2)
        expected = 7 / 1.0 + 3 / math.log2(3)
        assert abs(dcg_2 - expected) < 0.001


class TestNDCG:
    def test_perfect_ndcg(self):
        from pdf_parser.rag.evaluation import _ndcg

        ndcg = _ndcg([3, 2, 1, 0], [3, 2, 1, 0], 4)
        assert abs(ndcg - 1.0) < 0.001

    def test_bad_ordering(self):
        from pdf_parser.rag.evaluation import _ndcg

        # Retrieved: [0, 1, 3, 2] — bad ordering
        # Ideal: [3, 2, 1, 0]
        ndcg = _ndcg([0, 1, 3, 2], [3, 2, 1, 0], 4)
        assert ndcg < 1.0
        assert ndcg > 0.0

    def test_no_relevant(self):
        from pdf_parser.rag.evaluation import _ndcg

        ndcg = _ndcg([0, 0, 0], [0, 0, 0], 3)
        assert ndcg == 0.0


class TestMRR:
    def test_first_at_rank_1(self):
        from pdf_parser.rag.evaluation import _mrr

        mrr = _mrr(["c1", "c2", "c3"], {"c1": 2})
        assert abs(mrr - 1.0) < 0.001

    def test_first_at_rank_3(self):
        from pdf_parser.rag.evaluation import _mrr

        mrr = _mrr(["c1", "c2", "c3"], {"c3": 2})
        assert abs(mrr - 1.0 / 3) < 0.001

    def test_none_relevant(self):
        from pdf_parser.rag.evaluation import _mrr

        mrr = _mrr(["c1", "c2"], {"c3": 3})
        assert mrr == 0.0

    def test_marginally_relevant_not_counted(self):
        from pdf_parser.rag.evaluation import _mrr

        # score=1 is marginal, not counted as "relevant" for MRR
        mrr = _mrr(["c1"], {"c1": 1})
        assert mrr == 0.0


class TestRecall:
    def test_all_found(self):
        from pdf_parser.rag.evaluation import _recall

        recall = _recall(["c1", "c2", "c3"], {"c1": 2, "c2": 2}, k=10)
        assert abs(recall - 1.0) < 0.001

    def test_half_found(self):
        from pdf_parser.rag.evaluation import _recall

        recall = _recall(["c1", "cX", "cY"], {"c1": 2, "c2": 2}, k=10)
        assert abs(recall - 0.5) < 0.001

    def test_truncated(self):
        from pdf_parser.rag.evaluation import _recall

        # c2 is relevant but at position 3 (beyond k=2)
        recall = _recall(["c1", "cX", "c2"], {"c1": 2, "c2": 2}, k=2)
        assert abs(recall - 0.5) < 0.001


class TestPrecision:
    def test_all_relevant(self):
        from pdf_parser.rag.evaluation import _precision

        prec = _precision(["c1", "c2"], {"c1": 3, "c2": 2}, k=5)
        assert abs(prec - 2.0 / 5) < 0.001

    def test_none_relevant(self):
        from pdf_parser.rag.evaluation import _precision

        prec = _precision(["c1", "c2"], {"c3": 2}, k=5)
        assert prec == 0.0


# ---------------------------------------------------------------------------
# RetrievalEvaluator (with fake retriever)
# ---------------------------------------------------------------------------


class FakeRetriever:
    """Returns pre-determined results for testing evaluation logic."""

    def __init__(self, results_map: dict[str, list[str]]):
        self._map = results_map
        self.retrieve_calls: list[tuple[str, int]] = []

    def retrieve(self, query_plan, *, top_k=100):
        from pdf_parser.rag.models import DocumentChunk, RetrievalResult

        qtext = query_plan.original_query
        self.retrieve_calls.append((qtext, top_k))

        chunk_ids = self._map.get(qtext, [])
        return [
            RetrievalResult(
                chunk=DocumentChunk(file_id="test", text=f"Chunk {cid}", chunk_id=cid),
                hybrid_score=1.0 - i * 0.1,
            )
            for i, cid in enumerate(chunk_ids)
        ]


class TestRetrievalEvaluator:
    def test_perfect_retrieval(self):
        from pdf_parser.rag.evaluation import EvaluationDataset, RetrievalEvaluator

        dataset = EvaluationDataset()
        dataset.add_judgment("q1", "违约", "best", 3)
        dataset.add_judgment("q1", "违约", "good", 2)
        dataset.add_judgment("q1", "违约", "bad", 0)

        retriever = FakeRetriever({"违约": ["best", "good", "bad"]})
        evaluator = RetrievalEvaluator(retriever, dataset)
        metrics = evaluator.evaluate()

        # Perfect ordering: NDCG = 1.0
        assert metrics.num_queries == 1
        assert abs(metrics.ndcg_at_10 - 1.0) < 0.001
        assert abs(metrics.mrr - 1.0) < 0.001
        assert abs(metrics.recall_at_10 - 1.0) < 0.001

    def test_poor_retrieval(self):
        from pdf_parser.rag.evaluation import EvaluationDataset, RetrievalEvaluator

        dataset = EvaluationDataset()
        dataset.add_judgment("q1", "test", "relevant", 3)
        dataset.add_judgment("q1", "test", "also_relevant", 2)

        # Retrieved results are all irrelevant
        retriever = FakeRetriever({"test": ["junk1", "junk2", "junk3"]})
        evaluator = RetrievalEvaluator(retriever, dataset)
        metrics = evaluator.evaluate()

        assert metrics.ndcg_at_10 < 0.01
        assert metrics.mrr < 0.01

    def test_mixed_quality(self):
        from pdf_parser.rag.evaluation import EvaluationDataset, RetrievalEvaluator

        dataset = EvaluationDataset()
        dataset.add_judgment("q1", "test", "r1", 3)
        dataset.add_judgment("q1", "test", "r2", 2)

        # Good: r1 at position 1, r2 at position 3 (some irrelevant in between)
        retriever = FakeRetriever({"test": ["r1", "bad1", "r2", "bad2"]})
        evaluator = RetrievalEvaluator(retriever, dataset)
        metrics = evaluator.evaluate()

        # NDCG should be decent but not perfect
        assert 0.4 < metrics.ndcg_at_10 < 1.0
        assert metrics.mrr == 1.0  # first relevant at rank 1

    def test_multiple_queries(self):
        from pdf_parser.rag.evaluation import EvaluationDataset, RetrievalEvaluator

        dataset = EvaluationDataset()
        dataset.add_judgment("q1", "query A", "a1", 3)
        dataset.add_judgment("q2", "query B", "b1", 2)
        dataset.add_judgment("q3", "query C", "c1", 0)

        retriever = FakeRetriever({
            "query A": ["a1", "x"],
            "query B": ["x", "b1"],
            "query C": ["x", "y"],
        })
        evaluator = RetrievalEvaluator(retriever, dataset)
        metrics = evaluator.evaluate()

        assert metrics.num_queries == 3
        # Per-query breakdown should have 3 entries
        assert len(metrics.per_query) == 3

    def test_compare(self):
        from pdf_parser.rag.evaluation import RetrievalEvaluator, RetrievalMetrics

        baseline = RetrievalMetrics(num_queries=10, ndcg_at_10=0.60, mrr=0.70)
        candidate = RetrievalMetrics(num_queries=10, ndcg_at_10=0.75, mrr=0.82)

        report = RetrievalEvaluator.compare(baseline, candidate)
        assert "NDCG@10" in report
        assert "0.7500" in report

    def test_config_snapshot_recorded(self):
        from pdf_parser.rag.evaluation import EvaluationDataset, RetrievalEvaluator

        dataset = EvaluationDataset()
        dataset.add_judgment("q1", "test", "best", 3)

        retriever = FakeRetriever({"test": ["best"]})
        evaluator = RetrievalEvaluator(retriever, dataset)
        metrics = evaluator.evaluate(
            config_snapshot={"chunk_size": 256, "model": "bge-large-zh"}
        )

        assert metrics.config_snapshot["chunk_size"] == 256
        assert metrics.config_snapshot["model"] == "bge-large-zh"

    def test_skip_queries_without_judgments(self):
        from pdf_parser.rag.evaluation import EvaluationDataset, RetrievalEvaluator

        dataset = EvaluationDataset()
        dataset.add_query("no_judgments_yet", "this query has no chunks labeled")

        retriever = FakeRetriever({"this query has no chunks labeled": ["a", "b"]})
        evaluator = RetrievalEvaluator(retriever, dataset)
        metrics = evaluator.evaluate()

        # Should skip queries without judgments
        assert metrics.num_queries == 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

import math

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
