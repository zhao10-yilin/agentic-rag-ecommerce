"""Production-grade PDF non-standard data parsing pipeline."""

from pdf_parser.base import BasePDFParser
from pdf_parser.checkpoint import CheckpointManager
from pdf_parser.checkpoint_sqlite import TransactionalCheckpoint
from pdf_parser.cleaning import TextCleaner
from pdf_parser.exceptions import MemoryLimitExceeded, ParseTimeoutError, PDFParserError
from pdf_parser.hooks import run_deduplication
from pdf_parser.models import ExtractedImage, ParseMetrics, ParseResult
from pdf_parser.orchestrator import PipelineOrchestrator
from pdf_parser.sink import DataSink
from pdf_parser.strategies import MinerUParser

__all__ = [
    "BasePDFParser",
    "CheckpointManager",
    "DataSink",
    "ExtractedImage",
    "MemoryLimitExceeded",
    "MinerUParser",
    "ParseMetrics",
    "ParseResult",
    "ParseTimeoutError",
    "PDFParserError",
    "PipelineOrchestrator",
    "TextCleaner",
    "TransactionalCheckpoint",
    "run_deduplication",
]
