"""Post-processing hooks for the PDF parsing pipeline.

This module contains extension points that are called *after* the batch
orchestrator has finished.  They operate on the immutable result artifacts
produced by the pipeline (JSONL files, image directories, etc.).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def run_deduplication(jsonl_path: Path) -> None:
    """Hook: deduplicate parsed results using LSH (Locality-Sensitive Hashing).

    This function is a **placeholder / integration point**.  Once you have an
    existing LSH deduplication module, replace the body with a call to it.

    Typical integration pattern::

        from my_lsh_module import deduplicate_jsonl

        deduplicate_jsonl(
            input_path=str(jsonl_path),
            output_path=str(jsonl_path.with_suffix(".dedup.jsonl")),
        )

    The function receives the path to the ``parsed_results.jsonl`` file that
    contains one JSON object per line.  Each object has at minimum::

        {
            "file_id": "...",
            "markdown_content": "...",
            "extracted_images": [...]
        }

    Args:
        jsonl_path: Absolute path to the accumulated ``parsed_results.jsonl``.
    """
    logger.info(
        "Deduplication hook invoked — integrate your LSH module here",
        extra={"jsonl_path": str(jsonl_path)},
    )

    # ------------------------------------------------------------------
    # TODO: replace the stub below with your actual LSH integration.
    # ------------------------------------------------------------------
    # Example skeleton (uncomment and adapt):
    #
    # from my_lsh_module import build_minhash, lsh_deduplicate
    #
    # records = []
    # with jsonl_path.open("r", encoding="utf-8") as fh:
    #     for line in fh:
    #         records.append(json.loads(line))
    #
    # signatures = [build_minhash(r["markdown_content"]) for r in records]
    # dup_indices = lsh_deduplicate(signatures, threshold=0.85)
    #
    # logger.info("LSH found %d duplicates", len(dup_indices))
    # ------------------------------------------------------------------
