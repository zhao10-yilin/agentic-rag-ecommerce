"""Data models for standardized PDF parsing output."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Status(str, Enum):
    """Parsing status enumeration."""

    SUCCESS = "success"
    FAILED = "failed"


class ExtractedImage(BaseModel):
    """Represents an image extracted from a PDF page."""

    image_id: str = Field(..., description="Unique identifier for the image")
    page_number: int = Field(..., ge=1, description="1-based page number")
    bbox: tuple[float, float, float, float] | None = Field(
        default=None,
        description="Bounding box (x1, y1, x2, y2) in page coordinates",
    )
    image_path: str | None = Field(
        default=None,
        description="Filesystem path or URI to the extracted image file",
    )
    format: str | None = Field(
        default=None,
        description="Image format, e.g. 'png', 'jpeg'",
    )
    width: int | None = Field(default=None, description="Image width in pixels")
    height: int | None = Field(default=None, description="Image height in pixels")
    ocr_text: str | None = Field(
        default=None,
        description="OCR-extracted text if available",
    )

    model_config = {"frozen": True}


class ParseMetrics(BaseModel):
    """Performance and quality metrics for a single parse run."""

    elapsed_seconds: float = Field(
        ...,
        ge=0.0,
        description="Total wall-clock time for parsing",
    )
    page_count: int = Field(
        ...,
        ge=0,
        description="Total number of pages in the PDF",
    )
    processed_pages: int = Field(
        ...,
        ge=0,
        description="Number of pages successfully processed",
    )
    image_count: int = Field(
        default=0,
        ge=0,
        description="Number of images extracted",
    )
    table_count: int = Field(
        default=0,
        ge=0,
        description="Number of tables detected",
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="UTC timestamp when parsing completed",
    )
    parser_name: str = Field(
        ...,
        description="Name of the concrete parser implementation used",
    )
    parser_version: str = Field(
        default="unknown",
        description="Version of the parser library",
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Parser-specific additional metrics",
    )

    model_config = {"frozen": True}


class ParseResult(BaseModel):
    """Standardized output returned by every PDF parser strategy."""

    file_id: str = Field(..., description="Unique identifier for the input file")
    status: Status = Field(..., description="Overall parsing status")
    markdown_content: str = Field(
        default="",
        description="Extracted content in Markdown format (raw)",
    )
    cleaned_markdown: str | None = Field(
        default=None,
        description="Post-processed Markdown after text cleaning pipeline",
    )
    extracted_images: list[ExtractedImage] = Field(
        default_factory=list,
        description="List of images extracted during parsing",
    )
    metrics: ParseMetrics | None = Field(
        default=None,
        description="Detailed parsing metrics",
    )
    error_msg: str | None = Field(
        default=None,
        description="Human-readable error message when status is failed",
    )
    raw_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw PDF metadata (title, author, etc.)",
    )

    model_config = {"frozen": True}

    @classmethod
    def build_failure(
        cls,
        *,
        file_id: str,
        error_msg: str,
        parser_name: str,
        elapsed_seconds: float = 0.0,
        page_count: int = 0,
    ) -> ParseResult:
        """Factory helper for creating a failed result with consistent metrics."""
        return cls(
            file_id=file_id,
            status=Status.FAILED,
            error_msg=error_msg,
            metrics=ParseMetrics(
                elapsed_seconds=elapsed_seconds,
                page_count=page_count,
                processed_pages=0,
                parser_name=parser_name,
            ),
        )
