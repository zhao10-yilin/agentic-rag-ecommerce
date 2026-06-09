"""Tests for RAGCheckpointBridge and fixed is_indexed()."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest


# ---------------------------------------------------------------------------
# Fix 1: SQLiteFTSStore.is_indexed()
# ---------------------------------------------------------------------------


class TestFTSIsIndexed:
    @pytest.fixture
    def store(self, tmp_path):
        from pdf_parser.rag.vector_store import SQLiteFTSStore

        db = tmp_path / "test_is_indexed.db"
        s = SQLiteFTSStore(str(db))
        yield s
        try:
            db.unlink(missing_ok=True)
        except PermissionError:
            pass

    def test_not_indexed_initially(self, store):
        assert not store.is_indexed("nonexistent_file")

    def test_indexed_after_upsert(self, store):
        from pdf_parser.rag.models import DocumentChunk

        chunks = [
            DocumentChunk(file_id="doc_a", text="content A"),
            DocumentChunk(file_id="doc_b", text="content B"),
        ]
        store.upsert(chunks)
        assert store.is_indexed("doc_a")
        assert store.is_indexed("doc_b")
        assert not store.is_indexed("doc_c")

    def test_not_indexed_after_delete(self, store):
        from pdf_parser.rag.models import DocumentChunk

        store.upsert([DocumentChunk(file_id="doc_a", text="content")])
        assert store.is_indexed("doc_a")
        store.delete_by_file_id("doc_a")
        assert not store.is_indexed("doc_a")

    def test_get_indexed_file_ids(self, store):
        from pdf_parser.rag.models import DocumentChunk

        store.upsert([
            DocumentChunk(file_id="doc_a", text="A1"),
            DocumentChunk(file_id="doc_a", text="A2"),
            DocumentChunk(file_id="doc_b", text="B1"),
        ])

        ids = store.get_indexed_file_ids()
        assert ids == {"doc_a", "doc_b"}


# ---------------------------------------------------------------------------
# Fix 2: RAGIndexer.is_indexed() delegates to FTS
# ---------------------------------------------------------------------------


class TestIndexerIsIndexed:
    @pytest.fixture
    def indexer(self, tmp_path):
        from pdf_parser.rag.embedder import EmbeddingService
        from pdf_parser.rag.indexer import RAGIndexer
        from pdf_parser.rag.vector_store import ChromaVectorStore, SQLiteFTSStore

        # ChromaDB needs a real directory
        chroma_dir = tmp_path / "chroma_idx"
        fts_path = tmp_path / "idx_fts.db"

        class FakeEmbedder:
            dim = 8

            def embed_documents(self, texts):
                import hashlib
                results = []
                for t in texts:
                    h = hashlib.sha256(t.encode()).digest()
                    vec = [float(b) / 255.0 for b in h[:8]]
                    norm = sum(v * v for v in vec) ** 0.5
                    results.append([v / norm for v in vec])
                return results

            def embed_query(self, query):
                return self.embed_documents([query])[0]

        return RAGIndexer(
            dense_store=ChromaVectorStore(str(chroma_dir), collection_name="test_idxr"),
            sparse_store=SQLiteFTSStore(str(fts_path)),
            embedder=EmbeddingService(provider=FakeEmbedder()),
        )

    def test_not_indexed_initially(self, indexer):
        assert not indexer.is_indexed("nothing_yet")

    def test_indexed_after_indexing(self, indexer):
        indexer.index_text("# Test\n\nContent here.", file_id="test_file")
        assert indexer.is_indexed("test_file")

    def test_not_indexed_after_delete(self, indexer):
        indexer.index_text("# Doc\n\nText.", file_id="temp_doc")
        assert indexer.is_indexed("temp_doc")

        indexer.delete_by_file_id("temp_doc")
        assert not indexer.is_indexed("temp_doc")


# ---------------------------------------------------------------------------
# RAGCheckpointBridge
# ---------------------------------------------------------------------------


class TestRAGCheckpointBridge:
    @pytest.fixture
    def bridge(self, tmp_path):
        from pdf_parser.rag.bridge import RAGCheckpointBridge
        from pdf_parser.rag.vector_store import SQLiteFTSStore

        cp_db = tmp_path / "checkpoint.db"
        fts_db = tmp_path / "fts.db"

        import sqlite3
        # Set up checkpoint schema
        with sqlite3.connect(str(cp_db)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoint (
                    file_id TEXT PRIMARY KEY,
                    file_hash TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'processing', 'success', 'failed')),
                    started_at TEXT,
                    completed_at TEXT,
                    error_msg TEXT,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    output_dir TEXT
                )
                """
            )

        # Thin wrapper that satisfies the protocol
        class _FakeCheckpoint:
            def __init__(self, path):
                self.checkpoint_file = Path(path)

            @property
            def processed_count(self):
                with sqlite3.connect(str(self.checkpoint_file)) as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM checkpoint WHERE status = 'success'"
                    ).fetchone()
                    return row[0] if row else 0

            def is_processed(self, file_id):
                with sqlite3.connect(str(self.checkpoint_file)) as conn:
                    row = conn.execute(
                        "SELECT 1 FROM checkpoint WHERE file_id = ? AND status = 'success'",
                        (file_id,),
                    ).fetchone()
                    return row is not None

            def get_failure_count(self, file_id):
                return 0

        fake_cp = _FakeCheckpoint(str(cp_db))
        fts = SQLiteFTSStore(str(fts_db))

        bridge = RAGCheckpointBridge(checkpoint=fake_cp, fts_store=fts)
        # Store path for inserting test data
        bridge._cp_path = cp_db
        return bridge

    def _add_checkpoint_entry(self, bridge, file_id: str, status: str = "success"):
        import sqlite3

        with sqlite3.connect(str(bridge._cp_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO checkpoint (file_id, status) VALUES (?, ?)",
                (file_id, status),
            )

    def test_initially_files_to_index(self, bridge):
        self._add_checkpoint_entry(bridge, "doc_a", "success")
        self._add_checkpoint_entry(bridge, "doc_b", "success")

        to_index = bridge.get_files_to_index()
        assert "doc_a" in to_index
        assert "doc_b" in to_index

    def test_mark_indexed_removes_from_to_index(self, bridge):
        self._add_checkpoint_entry(bridge, "doc_a", "success")
        self._add_checkpoint_entry(bridge, "doc_b", "success")

        bridge.mark_indexed("doc_a")
        to_index = bridge.get_files_to_index()
        assert "doc_a" not in to_index
        assert "doc_b" in to_index

    def test_is_indexed(self, bridge):
        assert not bridge.is_indexed("doc_x")
        bridge.mark_indexed("doc_x")
        assert bridge.is_indexed("doc_x")

    def test_mark_unindexed(self, bridge):
        bridge.mark_indexed("doc_y")
        assert bridge.is_indexed("doc_y")
        bridge.mark_unindexed("doc_y")
        assert not bridge.is_indexed("doc_y")

    def test_orphaned_indexes(self, bridge):
        # Index a file that has no checkpoint entry
        from pdf_parser.rag.models import DocumentChunk

        bridge._fts.upsert([
            DocumentChunk(file_id="orphan_doc", text="content"),
        ])
        bridge.mark_indexed("orphan_doc")

        orphaned = bridge.get_orphaned_indexes()
        assert "orphan_doc" in orphaned

    def test_report_consistent(self, bridge):
        self._add_checkpoint_entry(bridge, "doc_a", "success")
        bridge.mark_indexed("doc_a")

        report = bridge.report()
        assert report["parsed_success"] == 1
        assert report["indexed"] == 1
        assert report["files_to_index"] == 0
        assert report["consistent"]

    def test_report_inconsistent(self, bridge):
        self._add_checkpoint_entry(bridge, "doc_a", "success")
        self._add_checkpoint_entry(bridge, "doc_b", "success")
        bridge.mark_indexed("doc_a")
        # doc_b is parsed but not indexed

        report = bridge.report()
        assert not report["consistent"]
        assert report["files_to_index"] == 1

    def test_sync_backfills(self, bridge):
        from pdf_parser.rag.models import DocumentChunk

        # Chunks exist but no index_log entry
        bridge._fts.upsert([
            DocumentChunk(file_id="backfill_me", text="content"),
        ])

        result = bridge.sync()
        assert result["backfilled"] == 1
        assert bridge.is_indexed("backfill_me")

    def test_sync_cleans(self, bridge):
        # index_log entry exists but no chunks in chunk_meta
        bridge.mark_indexed("ghost_file")
        assert bridge.is_indexed("ghost_file")

        result = bridge.sync()
        assert result["cleaned"] == 1
        assert not bridge.is_indexed("ghost_file")

    def test_indexed_count(self, bridge):
        assert bridge.indexed_count == 0
        bridge.mark_indexed("a")
        bridge.mark_indexed("b")
        assert bridge.indexed_count == 2

    def test_stale_indexes(self, bridge):
        from pdf_parser.rag.models import DocumentChunk

        self._add_checkpoint_entry(bridge, "stale_doc", "failed")
        bridge._fts.upsert([
            DocumentChunk(file_id="stale_doc", text="content"),
        ])
        bridge.mark_indexed("stale_doc")

        stale = bridge.get_stale_indexes()
        assert "stale_doc" in stale


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
