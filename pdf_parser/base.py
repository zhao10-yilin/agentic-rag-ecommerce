"""Abstract base class defining the Strategy interface for PDF parsers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pdf_parser.models import ParseResult

logger = logging.getLogger(__name__)


class BasePDFParser(ABC):
    """Abstract strategy for parsing PDF files into structured data.

    All concrete implementations (MinerUParser, MarkerParser, etc.) must
    inherit from this class and implement :meth:`parse`.
    """

    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        """Initialize the parser with optional configuration.

        Args:
            config: Parser-specific configuration dictionary.
        """
        self._config = config or {}
        self._logger = logging.getLogger(self.__class__.__module__)

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the human-readable name of this parser strategy."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Return the version of the underlying parser library."""

    @abstractmethod
    def parse(self, file_path: str | Path, *, file_id: str | None = None) -> ParseResult:
        """Parse a single PDF file.

        This is the **Strategy Interface**. All subclasses must implement
        this method and return a fully populated :class:`ParseResult`.

        Args:
            file_path: Absolute or relative path to the PDF file.
            file_id: Optional external identifier. If not provided, the
                file name (stem) is used.

        Returns:
            A standardized :class:`ParseResult` regardless of success or failure.
            Implementations must **never** raise exceptions from this method;
            failures are captured inside the returned result.
        """

    def _resolve_file_id(self, file_path: str | Path, file_id: str | None) -> str:
        """Derive a stable file_id when the caller does not provide one."""
        if file_id is not None:
            return file_id
        return Path(file_path).stem

    def _pre_flight_check(self, file_path: str | Path) -> Path:
        """Validate that the input file exists and is a PDF.

        Args:
            file_path: Path to validate.

        Returns:
            Resolved :class:`Path` object.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file extension is not ``.pdf``.
        """
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"Expected a .pdf file, got: {path.suffix}")
        return path

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, version={self.version!r})"
