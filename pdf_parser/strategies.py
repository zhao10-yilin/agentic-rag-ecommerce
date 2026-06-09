"""Concrete parser strategies."""

from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any

from pdf_parser.base import BasePDFParser
from pdf_parser.exceptions import ParseTimeoutError
from pdf_parser.models import ExtractedImage, ParseMetrics, ParseResult, Status

logger = logging.getLogger(__name__)


class MinerUParser(BasePDFParser):
    """PDF parser strategy powered by MinerU (open-source PDF parsing toolkit).

    Uses ``magic_pdf`` (``PymuDocDataset`` + ``doc_analyze``) for layout analysis,
    formula recognition and table detection.

    Production safety features:
        * **Timeout control** — per-file wall-clock timeout via
          :class:`concurrent.futures.ThreadPoolExecutor`.
        * **Graceful degradation** — all exceptions are captured and returned as
          :class:`ParseResult` with ``status=failed``; the caller never receives
          a raw traceback.
        * **Structured logging** — every major step emits JSON logs with
          ``file_id`` context for distributed tracing.

    Reference:
        https://github.com/opendatalab/MinerU
    """

    DEFAULT_TIMEOUT: int = 300  # 5 minutes

    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config)
        self._enable_ocr: bool = self._config.get("enable_ocr", True)
        self._extract_images: bool = self._config.get("extract_images", True)
        self._timeout_seconds: int = self._config.get(
            "timeout_seconds", self.DEFAULT_TIMEOUT
        )
        self._output_dir: Path | None = None
        if out_dir := self._config.get("output_dir"):
            self._output_dir = Path(out_dir).expanduser().resolve()

    @property
    def name(self) -> str:
        return "mineru"

    @property
    def version(self) -> str:
        try:
            import magic_pdf
            return getattr(magic_pdf, "__version__", "unknown")
        except ImportError:
            return "not-installed"

    def parse(
        self, file_path: str | Path, *, file_id: str | None = None
    ) -> ParseResult:
        """Parse a PDF using MinerU with timeout protection and full error handling.

        The method follows a strict contract: **never raise**.  All failures,
        including timeouts and malformed PDFs, are translated into a
        :class:`ParseResult` with ``status=Status.FAILED``.
        """
        file_id = self._resolve_file_id(file_path, file_id)
        start_time = time.perf_counter()

        self._logger.info(
            "Starting PDF parse",
            extra={
                "file_id": file_id,
                "file_path": str(file_path),
                "parser": self.name,
                "version": self.version,
                "timeout_seconds": self._timeout_seconds,
            },
        )

        # ------------------------------------------------------------------
        # 1. Pre-flight checks
        # ------------------------------------------------------------------
        try:
            path = self._pre_flight_check(file_path)
        except (FileNotFoundError, ValueError) as exc:
            elapsed = time.perf_counter() - start_time
            self._logger.error(
                "Pre-flight check failed",
                extra={
                    "file_id": file_id,
                    "error": str(exc),
                    "elapsed_seconds": round(elapsed, 3),
                },
            )
            return ParseResult.build_failure(
                file_id=file_id,
                error_msg=str(exc),
                parser_name=self.name,
                elapsed_seconds=elapsed,
            )

        # ------------------------------------------------------------------
        # 2. Core parsing with timeout guard
        # ------------------------------------------------------------------
        try:
            markdown_content, images, page_count = self._invoke_mineru_with_timeout(
                path, file_id
            )

            elapsed = time.perf_counter() - start_time
            metrics = ParseMetrics(
                elapsed_seconds=round(elapsed, 3),
                page_count=page_count,
                processed_pages=page_count,
                image_count=len(images),
                parser_name=self.name,
                parser_version=self.version,
            )

            self._logger.info(
                "PDF parse completed successfully",
                extra={
                    "file_id": file_id,
                    "page_count": page_count,
                    "elapsed_seconds": metrics.elapsed_seconds,
                    "image_count": metrics.image_count,
                },
            )

            return ParseResult(
                file_id=file_id,
                status=Status.SUCCESS,
                markdown_content=markdown_content,
                extracted_images=images,
                metrics=metrics,
            )

        # ------------------------------------------------------------------
        # 3. Exception taxonomy → standardized failure result
        # ------------------------------------------------------------------
        except ParseTimeoutError as exc:
            elapsed = time.perf_counter() - start_time
            self._logger.error(
                "PDF parse timed out",
                extra={
                    "file_id": file_id,
                    "timeout_seconds": self._timeout_seconds,
                    "elapsed_seconds": round(elapsed, 3),
                },
            )
            return ParseResult.build_failure(
                file_id=file_id,
                error_msg=str(exc),
                parser_name=self.name,
                elapsed_seconds=elapsed,
            )

        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - start_time
            self._logger.exception(
                "PDF parse failed",
                extra={
                    "file_id": file_id,
                    "parser": self.name,
                    "error_type": type(exc).__name__,
                    "elapsed_seconds": round(elapsed, 3),
                },
            )
            return ParseResult.build_failure(
                file_id=file_id,
                error_msg=f"{type(exc).__name__}: {exc}",
                parser_name=self.name,
                elapsed_seconds=elapsed,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _invoke_mineru_with_timeout(
        self, path: Path, file_id: str
    ) -> tuple[str, list[ExtractedImage], int]:
        """Wrap the blocking MinerU call in a thread with a hard timeout.

        .. note::
            ``ThreadPoolExecutor`` cannot force-terminate a running Python
            thread.  If MinerU hangs inside a C / CUDA extension, the thread
            continues until the OS reclaims the process.  For stricter resource
            control (e.g. kill the worker), migrate to
            ``multiprocessing.Process`` with ``Process.join(timeout=...)``.
        """
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="mineru_") as executor:
            future = executor.submit(self._invoke_mineru, path, file_id)
            try:
                return future.result(timeout=self._timeout_seconds)
            except FutureTimeoutError:
                self._logger.warning(
                    "MinerU inference timed out, cancelling future",
                    extra={"file_id": file_id, "timeout": self._timeout_seconds},
                )
                # Best-effort cancellation; the thread may still be alive.
                future.cancel()
                raise ParseTimeoutError(
                    f"PDF parsing exceeded the {self._timeout_seconds}s timeout limit",
                    timeout_seconds=self._timeout_seconds,
                    file_id=file_id,
                ) from None

    def _invoke_mineru(
        self, path: Path, file_id: str
    ) -> tuple[str, list[ExtractedImage], int]:
        """Execute the actual MinerU pipeline.

        Pipeline stages:
            1. Read PDF bytes.
            2. Build :class:`PymuDocDataset` and classify (OCR vs text).
            3. Run ``doc_analyze`` (layout + formula + table model).
            4. Export Markdown and persist extracted images.
            5. Rewrite image paths in Markdown to relative paths.
        """
        # -- Lazy import so the module can be imported without magic_pdf installed.
        try:
            from magic_pdf.config.enums import SupportedPdfParseMethod
            from magic_pdf.data.data_reader_writer import (
                FileBasedDataReader,
                FileBasedDataWriter,
            )
            from magic_pdf.data.dataset import PymuDocDataset
            from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze
        except ImportError as exc:
            raise ImportError(
                "MinerU (magic_pdf) is not installed. "
                "Install it with: pip install mineru"
            ) from exc

        # -- 1. Prepare directory layout ----------------------------------
        if self._output_dir:
            work_dir = self._output_dir / file_id
        else:
            work_dir = path.parent / file_id
        work_dir.mkdir(parents=True, exist_ok=True)

        image_dir = work_dir / "images"
        image_dir.mkdir(exist_ok=True)

        self._logger.info(
            "MinerU workspace prepared",
            extra={
                "file_id": file_id,
                "work_dir": str(work_dir),
                "image_dir": str(image_dir),
                "ocr_enabled": self._enable_ocr,
            },
        )

        # -- 2. Load PDF --------------------------------------------------
        reader = FileBasedDataReader("")
        pdf_bytes = reader.read(str(path))

        # Determine page count (best-effort via PyMuPDF, already a transitive dep)
        page_count = self._get_page_count(pdf_bytes)

        # -- 3. Dataset & classification ----------------------------------
        dataset = PymuDocDataset(pdf_bytes)
        parse_method = dataset.classify()

        self._logger.info(
            "PDF classified by MinerU",
            extra={
                "file_id": file_id,
                "parse_method": (
                    parse_method.name
                    if hasattr(parse_method, "name")
                    else str(parse_method)
                ),
                "page_count": page_count,
            },
        )

        # -- 4. Model inference -------------------------------------------
        image_writer = FileBasedDataWriter(str(image_dir))

        if parse_method == SupportedPdfParseMethod.OCR:
            infer_result = dataset.apply(doc_analyze, ocr=True)
            pipe_result = infer_result.pipe_ocr_mode(image_writer)
        else:
            infer_result = dataset.apply(doc_analyze, ocr=False)
            pipe_result = infer_result.pipe_txt_mode(image_writer)

        # -- 5. Export Markdown -------------------------------------------
        raw_md = pipe_result.get_markdown(str(image_dir))

        # -- 6. Post-process images & paths -------------------------------
        processed_md, images = self._process_images(
            raw_md, image_dir, file_id
        )

        self._logger.info(
            "MinerU pipeline finished",
            extra={
                "file_id": file_id,
                "page_count": page_count,
                "image_count": len(images),
            },
        )

        return processed_md, images, page_count

    @staticmethod
    def _get_page_count(pdf_bytes: bytes) -> int:
        """Return page count using PyMuPDF (fitz), a transitive dependency of MinerU."""
        try:
            import fitz  # noqa: F401  # PyMuPDF

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            count = len(doc)
            doc.close()
            return count
        except Exception:
            return 0

    def _process_images(
        self,
        markdown: str,
        image_dir: Path,
        file_id: str,
    ) -> tuple[str, list[ExtractedImage]]:
        """Scan Markdown for image references, replace paths with relative ones.

        For every ``![alt](path)`` found in the Markdown:
            * Resolve the file on disk inside *image_dir*.
            * Build an :class:`ExtractedImage` record (with dimensions if PIL
              is available).
            * Rewrite the Markdown link to ``images/<filename>``.

        Returns:
            ``(processed_markdown, list_of_extracted_images)``
        """
        images: list[ExtractedImage] = []
        pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

        def _replace(match: re.Match) -> str:
            alt_text = match.group(1)
            original_path = match.group(2)

            # Resolve path: absolute, or relative to image_dir
            img_path = Path(original_path)
            if not img_path.is_absolute():
                img_path = image_dir / img_path.name
            if not img_path.exists():
                # Fallback: try basename only
                img_path = image_dir / Path(original_path).name

            if not img_path.exists():
                self._logger.warning(
                    "Image referenced in Markdown but not found on disk",
                    extra={
                        "original_path": original_path,
                        "image_dir": str(image_dir),
                        "file_id": file_id,
                    },
                )
                # Leave the original reference untouched
                return match.group(0)

            idx = len(images)
            image_id = f"{file_id}_img_{idx:04d}"
            relative_path = f"images/{img_path.name}"

            # Best-effort dimension extraction
            width = height = None
            try:
                from PIL import Image as PILImage

                with PILImage.open(img_path) as pil_img:
                    width, height = pil_img.size
            except Exception:
                pass  # PIL unavailable or image unreadable — not fatal

            images.append(
                ExtractedImage(
                    image_id=image_id,
                    page_number=0,  # MinerU pipeline doesn't expose per-element page mapping easily
                    image_path=relative_path,
                    format=img_path.suffix.lstrip(".").lower() or None,
                    width=width,
                    height=height,
                )
            )

            return f"![{alt_text}]({relative_path})"

        processed_md = pattern.sub(_replace, markdown)
        return processed_md, images
