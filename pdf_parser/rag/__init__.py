"""Production-grade RAG (Retrieval-Augmented Generation) subsystem.

Components
----------
* :class:`SemanticChunker` — Markdown-structure-aware semantic chunking
* :class:`EmbeddingService` — Local / API embedding with LRU cache
* :class:`ChromaVectorStore` — Dense vector storage via ChromaDB
* :class:`SQLiteFTSStore` — Sparse keyword index via SQLite FTS5
* :class:`HybridRetriever` — Dense + sparse retrieval with RRF fusion
* :class:`Reranker` — Cross-encoder re-ranking
* :class:`LLMGateway` — Unified LLM client (DeepSeek / OpenAI / Qwen)
* :class:`RAGQueryEngine` — End-to-end query orchestration
* :class:`RAGIndexer` — ParseResult → vector store ingestion pipeline
* :class:`EvaluationDataset` — Human-labeled relevance judgments
* :class:`RetrievalEvaluator` — NDCG/MRR/Recall evaluation
* :class:`SessionManager` — Multi-turn conversation session manager
"""

from pdf_parser.rag.models import (
    ChatMessage,
    ChatResponse,
    DocumentChunk,
    QueryPlan,
    RAGResponse,
    RetrievalResult,
)
from pdf_parser.rag.chunker import SemanticChunker
from pdf_parser.rag.embedder import EmbeddingService, SentenceTransformerEmbedder
from pdf_parser.rag.vector_store import (
    BaseVectorStore,
    ChromaVectorStore,
    SQLiteFTSStore,
)
from pdf_parser.rag.retriever import HybridRetriever, Reranker
from pdf_parser.rag.llm_gateway import LLMGateway
from pdf_parser.rag.query_engine import RAGQueryEngine
from pdf_parser.rag.indexer import RAGIndexer
from pdf_parser.rag.evaluation import (
    EvaluationDataset,
    RelevanceJudgment,
    RetrievalEvaluator,
    RetrievalMetrics,
)
from pdf_parser.rag.session import ChatSession, SessionManager
from pdf_parser.rag.bridge import RAGCheckpointBridge

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "ChatSession",
    "DocumentChunk",
    "QueryPlan",
    "RAGResponse",
    "RetrievalResult",
    "SemanticChunker",
    "EmbeddingService",
    "SentenceTransformerEmbedder",
    "BaseVectorStore",
    "ChromaVectorStore",
    "SQLiteFTSStore",
    "HybridRetriever",
    "Reranker",
    "LLMGateway",
    "RAGQueryEngine",
    "RAGIndexer",
    "EvaluationDataset",
    "RelevanceJudgment",
    "RetrievalEvaluator",
    "RetrievalMetrics",
    "SessionManager",
    "RAGCheckpointBridge",
]
