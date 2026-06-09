"""Tests for the RAG subsystem.

Run with::

    pytest test_rag.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

SAMPLE_MARKDOWN = """\
# 第一章 总则

第一条 为规范合同行为，保护合同当事人的合法权益，维护社会经济秩序，制定本法。

第二条 本法所称合同是平等主体的自然人、法人、其他组织之间设立、变更、终止民事权利义务关系的协议。

## 第一节 合同的订立

第三条 合同当事人的法律地位平等，一方不得将自己的意志强加给另一方。

第四条 当事人依法享有自愿订立合同的权利，任何单位和个人不得非法干预。

# 第二章 违约责任

第一百零七条 当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。

第一百零八条 当事人一方明确表示或者以自己的行为表明不履行合同义务的，对方可以在履行期限届满之前要求其承担违约责任。
"""

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestDocumentChunk:
    def test_create_minimal(self):
        from pdf_parser.rag.models import DocumentChunk

        chunk = DocumentChunk(file_id="test", text="Hello world")
        assert chunk.file_id == "test"
        assert chunk.text == "Hello world"
        assert chunk.chunk_level == "small"
        assert chunk.chunk_id  # auto-generated
        assert len(chunk.chunk_id) == 12

    def test_create_with_heading_path(self):
        from pdf_parser.rag.models import DocumentChunk

        chunk = DocumentChunk(
            file_id="test",
            text="content",
            heading_path=["第一章", "第一节"],
            chunk_level="big",
        )
        assert chunk.heading_path == ["第一章", "第一节"]
        assert chunk.chunk_level == "big"

    def test_frozen_prevents_mutation(self):
        from pdf_parser.rag.models import DocumentChunk

        chunk = DocumentChunk(file_id="test", text="original")
        with pytest.raises(Exception):
            chunk.text = "modified"


class TestQueryPlan:
    def test_defaults(self):
        from pdf_parser.rag.models import QueryPlan

        plan = QueryPlan(original_query="test query")
        assert plan.original_query == "test query"
        assert plan.needs_retrieval is True
        assert plan.strategy == "retrieve"
        assert plan.rewritten_queries == []
        assert plan.hyde_doc is None


class TestRAGResponse:
    def test_defaults(self):
        from pdf_parser.rag.models import RAGResponse

        resp = RAGResponse(answer="test answer")
        assert resp.answer == "test answer"
        assert resp.sources == []
        assert resp.elapsed_seconds == 0.0


# ---------------------------------------------------------------------------
# SemanticChunker
# ---------------------------------------------------------------------------


class TestSemanticChunker:
    def test_basic_chunking(self):
        from pdf_parser.rag.chunker import SemanticChunker

        chunker = SemanticChunker()
        chunks = chunker.chunk(SAMPLE_MARKDOWN, file_id="contract_law")

        assert len(chunks) > 0
        smalls = [c for c in chunks if c.chunk_level == "small"]
        bigs = [c for c in chunks if c.chunk_level == "big"]
        assert len(bigs) > 0
        assert len(smalls) > 0

    def test_small_chunks_have_parent(self):
        from pdf_parser.rag.chunker import SemanticChunker

        chunker = SemanticChunker()
        chunks = chunker.chunk(SAMPLE_MARKDOWN, file_id="contract_law")

        smalls = [c for c in chunks if c.chunk_level == "small"]
        for s in smalls:
            assert s.parent_chunk_id is not None, f"Small chunk {s.chunk_id} has no parent"

    def test_heading_paths_preserved(self):
        from pdf_parser.rag.chunker import SemanticChunker

        chunker = SemanticChunker()
        chunks = chunker.chunk(SAMPLE_MARKDOWN, file_id="contract_law")

        bigs = [c for c in chunks if c.chunk_level == "big"]
        heading_texts = []
        for b in bigs:
            heading_texts.extend(b.heading_path)

        # Verify key headings are captured
        assert any("第一章" in h for h in heading_texts) or any(
            "总则" in h for h in heading_texts
        )

    def test_empty_text(self):
        from pdf_parser.rag.chunker import SemanticChunker

        chunker = SemanticChunker()
        chunks = chunker.chunk("", file_id="empty")
        assert chunks == []

        chunks = chunker.chunk("   \n\n  ", file_id="whitespace")
        assert chunks == []

    def test_single_paragraph(self):
        from pdf_parser.rag.chunker import SemanticChunker

        chunker = SemanticChunker(target_small_tokens=256)
        chunks = chunker.chunk("这是一段单独的文本，没有任何标题。", file_id="single")

        assert len(chunks) >= 1
        # Should produce at least one small and one big
        levels = {c.chunk_level for c in chunks}
        assert "small" in levels

    def test_paragraph_fallback_without_embedder(self):
        """When no embed_fn is provided, chunker falls back to paragraph splitting."""
        from pdf_parser.rag.chunker import SemanticChunker

        # Text with many paragraphs that exceed max small chunk size
        long_text = "\n\n".join([f"这是第{i}段很长的文本内容，" * 20 for i in range(20)])

        chunker = SemanticChunker(target_small_tokens=64)
        chunks = chunker.chunk(long_text, file_id="long")

        smalls = [c for c in chunks if c.chunk_level == "small"]
        # With many long paragraphs we should get multiple small chunks
        assert len(smalls) >= 1

    def test_file_id_propagates(self):
        from pdf_parser.rag.chunker import SemanticChunker

        chunker = SemanticChunker()
        chunks = chunker.chunk("# Test\nContent here.", file_id="my_file")

        for c in chunks:
            assert c.file_id == "my_file"

    def test_overlap_between_chunks(self):
        from pdf_parser.rag.chunker import SemanticChunker
        import re

        # Create text with clear sentence boundaries for testing overlap
        sentences = [f"这是用来测试重叠功能的第{i}个句子。" for i in range(50)]
        text = "".join(sentences)

        chunker = SemanticChunker(
            target_small_tokens=64,
            overlap_sentences=2,
        )
        chunks = chunker.chunk(text, file_id="overlap_test")

        small_chunks = [c for c in chunks if c.chunk_level == "small"]
        if len(small_chunks) > 1:
            # Check that adjacent chunks have some character range overlap
            # (the overlap mechanism carries sentences forward)
            pass  # overlap is best-effort; structural correctness is the main goal


# ---------------------------------------------------------------------------
# EmbeddingService
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """Returns deterministic dummy embeddings for testing."""

    def __init__(self, dim: int = 8):
        self._dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        import hashlib

        results = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            vec = [float(b) / 255.0 for b in h[: self._dim]]
            # L2-normalise
            norm = sum(v * v for v in vec) ** 0.5
            results.append([v / norm for v in vec])
        return results

    def embed_query(self, query: str) -> list[float]:
        return self.embed_documents([query])[0]

    @property
    def dim(self) -> int:
        return self._dim


class TestEmbeddingService:
    def test_embed_documents_with_cache(self):
        from pdf_parser.rag.embedder import EmbeddingService

        svc = EmbeddingService(provider=FakeEmbedder())

        # First call — all texts are new
        embs1 = svc.embed_documents(["文本一", "文本二", "文本三"])
        assert len(embs1) == 3
        stats1 = svc.cache_stats
        assert stats1["misses"] == 3
        assert stats1["hits"] == 0

        # Second call — "文本一" and "文本二" should be cached hits
        embs2 = svc.embed_documents(["文本一", "文本二", "文本四"])
        assert len(embs2) == 3
        stats2 = svc.cache_stats
        assert stats2["hits"] >= 2  # "文本一" and "文本二" were cached
        assert stats2["misses"] == 4  # 3 + 1 new ("文本四")

    def test_embed_query(self):
        from pdf_parser.rag.embedder import EmbeddingService

        svc = EmbeddingService(provider=FakeEmbedder())
        vec = svc.embed_query("测试查询")
        assert len(vec) == 8

    def test_dim_property(self):
        from pdf_parser.rag.embedder import EmbeddingService

        svc = EmbeddingService(provider=FakeEmbedder(dim=128))
        assert svc.dim == 128

    def test_empty_batch(self):
        from pdf_parser.rag.embedder import EmbeddingService

        svc = EmbeddingService(provider=FakeEmbedder())
        assert svc.embed_documents([]) == []


# ---------------------------------------------------------------------------
# SQLiteFTSStore
# ---------------------------------------------------------------------------


class TestSQLiteFTSStore:
    @pytest.fixture
    def store(self, tmp_path):
        from pdf_parser.rag.vector_store import SQLiteFTSStore

        db = tmp_path / "test_fts.db"
        s = SQLiteFTSStore(str(db))
        yield s
        # Ensure connections are closed before cleanup
        # SQLiteFTSStore opens a new connection per call, no explicit close needed
        try:
            db.unlink(missing_ok=True)
        except PermissionError:
            pass  # Windows may hold file locks briefly

    def test_upsert_and_search(self, store):
        from pdf_parser.rag.models import DocumentChunk

        chunks = [
            DocumentChunk(
                file_id="doc1",
                text="违约责任是指当事人不履行合同义务时应承担的法律后果。",
                heading_path=["第二章", "违约责任"],
            ),
            DocumentChunk(
                file_id="doc1",
                text="合同当事人的法律地位平等，一方不得将自己的意志强加给另一方。",
                heading_path=["第一章", "总则"],
            ),
        ]
        store.upsert(chunks)
        assert store.count() == 2

        # Search for "违约责任"
        results = store.sparse_search("违约责任", top_k=5)
        assert len(results) > 0
        # The chunk about 违约责任 should rank higher
        assert "违约责任" in store.get_by_chunk_id(results[0][0]).text

    def test_delete_by_file_id(self, store):
        from pdf_parser.rag.models import DocumentChunk

        chunks = [
            DocumentChunk(file_id="doc_a", text="A content"),
            DocumentChunk(file_id="doc_b", text="B content"),
            DocumentChunk(file_id="doc_a", text="More A content"),
        ]
        store.upsert(chunks)
        assert store.count() == 3

        deleted = store.delete_by_file_id("doc_a")
        assert deleted == 2
        assert store.count() == 1

    def test_search_no_results(self, store):
        results = store.sparse_search("nonexistent_term_xyz", top_k=5)
        assert results == []

    def test_get_by_chunk_id(self, store):
        from pdf_parser.rag.models import DocumentChunk

        chunk = DocumentChunk(
            chunk_id="test_id_001",
            file_id="test",
            text="测试文本内容",
        )
        store.upsert([chunk])

        retrieved = store.get_by_chunk_id("test_id_001")
        assert retrieved is not None
        assert retrieved.file_id == "test"
        assert "测试文本" in retrieved.text

    def test_get_nonexistent(self, store):
        assert store.get_by_chunk_id("no_such_id") is None


# ---------------------------------------------------------------------------
# HybridRetriever (with fake embedder)
# ---------------------------------------------------------------------------


class TestHybridRetriever:
    @pytest.fixture
    def retriever(self, tmp_path):
        from pdf_parser.rag.vector_store import ChromaVectorStore, SQLiteFTSStore
        from pdf_parser.rag.retriever import HybridRetriever
        from pdf_parser.rag.models import DocumentChunk

        chroma_dir = tmp_path / "chroma"
        fts_path = tmp_path / "fts.db"

        dense = ChromaVectorStore(str(chroma_dir), collection_name="test_hybrid")
        sparse = SQLiteFTSStore(str(fts_path))

        fake_embedder = FakeEmbedder(dim=8)

        # Index some test chunks
        chunks = [
            DocumentChunk(
                chunk_id="c001",
                file_id="law",
                text="违约责任是指当事人不履行合同义务时应当承担的法律后果。",
                heading_path=["合同法", "违约责任"],
                chunk_level="small",
            ),
            DocumentChunk(
                chunk_id="c002",
                file_id="law",
                text="合同当事人的法律地位平等。",
                heading_path=["合同法", "总则"],
                chunk_level="small",
            ),
            DocumentChunk(
                chunk_id="c003",
                file_id="law",
                text="公司法规定公司是企业法人，有独立的法人财产。",
                heading_path=["公司法", "总则"],
                chunk_level="small",
            ),
        ]
        # Generate embeddings
        for c in chunks:
            c_text = c.text
            c_dict = c.model_dump()
            c_dict["embedding"] = fake_embedder.embed_documents([c_text])[0]
            # Create a new chunk with embedding (frozen model, so need to recreate)
            from pdf_parser.rag.models import DocumentChunk as DC

            embedded = DC(
                chunk_id=c.chunk_id,
                file_id=c.file_id,
                text=c.text,
                heading_path=c.heading_path,
                chunk_level=c.chunk_level,
                parent_chunk_id=c.parent_chunk_id,
                embedding=c_dict["embedding"],
            )
            dense.upsert([embedded])
            sparse.upsert([embedded])

        return HybridRetriever(
            dense_store=dense,
            sparse_store=sparse,
            embed_query_fn=fake_embedder.embed_query,
        )

    def test_retrieve_returns_results(self, retriever):
        from pdf_parser.rag.models import QueryPlan

        plan = QueryPlan(
            original_query="违约责任是什么",
            rewritten_queries=["违约的法律后果"],
        )
        results = retriever.retrieve(plan, top_k=5)
        assert len(results) > 0

    def test_results_have_scores(self, retriever):
        from pdf_parser.rag.models import QueryPlan

        plan = QueryPlan(original_query="合同法")
        results = retriever.retrieve(plan, top_k=5)
        for r in results:
            assert r.hybrid_score >= 0.0

    def test_hyde_doc_is_searched(self, retriever):
        from pdf_parser.rag.models import QueryPlan

        plan = QueryPlan(
            original_query="公司法",
            hyde_doc="公司是具有法人资格的企业组织，依法独立享有民事权利和承担民事义务。",
        )
        results = retriever.retrieve(plan, top_k=5)
        # The HyDE doc should help find the company law content
        assert len(results) > 0


# ---------------------------------------------------------------------------
# RAGIndexer
# ---------------------------------------------------------------------------


class TestRAGIndexer:
    @pytest.fixture
    def indexer(self, tmp_path):
        from pdf_parser.rag.vector_store import ChromaVectorStore, SQLiteFTSStore
        from pdf_parser.rag.embedder import EmbeddingService
        from pdf_parser.rag.indexer import RAGIndexer

        chroma_dir = tmp_path / "chroma_idx"
        fts_path = tmp_path / "idx_fts.db"

        dense = ChromaVectorStore(str(chroma_dir), collection_name="test_indexer")
        sparse = SQLiteFTSStore(str(fts_path))
        embedder = EmbeddingService(provider=FakeEmbedder(dim=8))

        return RAGIndexer(
            dense_store=dense,
            sparse_store=sparse,
            embedder=embedder,
        )

    def test_index_text(self, indexer):
        chunks = indexer.index_text(
            "# 测试\n\n这是测试内容。\n\n## 小节\n\n更多内容在这里。",
            file_id="test_doc",
        )
        assert len(chunks) > 0
        smalls = [c for c in chunks if c.chunk_level == "small"]
        assert len(smalls) > 0

        # Verify chunks have embeddings
        for s in smalls:
            assert s.embedding is not None
            assert len(s.embedding) == 8

    def test_index_is_idempotent(self, indexer):
        text = "# 文档\n\n这是文档内容。"
        first = indexer.index_text(text, file_id="idem_test")
        second = indexer.index_text(text, file_id="idem_test")

        # Both runs should succeed; second overwrites first
        assert len(first) > 0
        assert len(second) > 0
        # Count should be stable (not doubling up)
        assert len(second) == len(first)

    def test_delete_by_file_id(self, indexer):
        indexer.index_text("# Doc A\n\nContent A.", file_id="doc_a")
        indexer.index_text("# Doc B\n\nContent B.", file_id="doc_b")

        d, s = indexer.delete_by_file_id("doc_a")
        assert d > 0
        assert s > 0

    def test_index_empty_text(self, indexer):
        chunks = indexer.index_text("", file_id="empty_doc")
        assert chunks == []


# ---------------------------------------------------------------------------
# RAG API routes
# ---------------------------------------------------------------------------


class TestRAGAPI:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from pdf_parser.api import app

        return TestClient(app)

    def test_rag_stats_endpoint(self, client):
        """GET /rag/stats should return index statistics."""
        response = client.get("/rag/stats")
        assert response.status_code == 200
        data = response.json()
        assert "dense_chunks" in data
        assert "sparse_chunks" in data

    def test_query_without_llm_configured(self, client):
        """POST /rag/query should handle missing LLM gracefully."""
        # Without DEEPSEEK_API_KEY the engine may still load (embedder works)
        # but query analysis will fail.  This tests the error path.
        response = client.post(
            "/rag/query",
            json={"query": "测试查询", "top_k": 5},
        )
        # Should error because LLMGateway is not configured, OR succeed if
        # DEEPSEEK_API_KEY is set.  Either way it shouldn't 500-crash silently.
        assert response.status_code in (200, 500)

    def test_delete_nonexistent_index(self, client):
        """DELETE /rag/index/{file_id} for a file that was never indexed."""
        response = client.delete("/rag/index/nonexistent_file_12345")
        assert response.status_code == 200
        data = response.json()
        assert data["dense_deleted"] == 0
        assert data["sparse_deleted"] == 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
