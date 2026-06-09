"""4-layer text cleaning pipeline for MinerU / Marker Markdown output.

Each layer is idempotent and can be toggled independently via the config dict.
The pipeline operates on raw Markdown strings and returns cleaned Markdown.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer 1 — Rule-based cleaning
# ---------------------------------------------------------------------------


class RuleCleaner:
    """Regex-based heuristics for common PDF-to-Markdown noise.

    Handles:
    * Isolated page numbers (standalone digits on a line)
    * Repeated header/footer lines (short lines that appear many times)
    * Separator lines made of dashes, equals, underscores
    * Excessive blank lines
    * Stray whitespace around image references
    """

    # Stand-alone page numbers: " 12 ", "- 5 -", "Page 3"
    PAGE_NUMBER_RE = re.compile(
        r"^\s*(?:page\s*)?[-—]?\s*\d+\s*[-—]?\s*$",
        re.IGNORECASE,
    )

    # Pure separator lines: "---", "===", "___", "* * *", etc.
    SEPARATOR_RE = re.compile(
        r"^[\s\-=_*~·]+$",
    )

    # Excessive internal whitespace
    MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

    # Blank lines around image refs
    IMAGE_SURROUND_RE = re.compile(r"\n\n(!\[[^\]]*\]\([^)]+\))\n\n")

    def __init__(self, *, remove_page_numbers: bool = True) -> None:
        self._remove_page_numbers = remove_page_numbers

    def clean(self, text: str) -> str:
        lines = text.splitlines()
        cleaned: list[str] = []

        for line in lines:
            stripped = line.strip()

            # Skip pure separator lines
            if self.SEPARATOR_RE.match(stripped):
                continue

            # Skip stand-alone page numbers
            if self._remove_page_numbers and self.PAGE_NUMBER_RE.match(stripped):
                continue

            # Collapse multiple spaces/tabs
            line = self.MULTI_SPACE_RE.sub(" ", line)

            cleaned.append(line)

        # Re-join and normalise blank lines (max 2 consecutive)
        text = "\n".join(cleaned)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Tighten blank lines around image references
        text = self.IMAGE_SURROUND_RE.sub(r"\n\1\n", text)

        return text.strip()


# ---------------------------------------------------------------------------
# Layer 2 — Repetition frequency filter
# ---------------------------------------------------------------------------


class RepetitionFilter:
    """Remove boiler-plate text that appears many times across the document.

    Typical victims:
    * "Confidential — Internal Use Only" on every page
    * Company name / logo text repeated as header
    * Copyright footers

    Algorithm:
    1. Chunk the text into overlapping n-grams (default 5-word windows).
    2. Count frequencies.
    3. Any chunk appearing ≥ *threshold* times is classified as boilerplate.
    4. All occurrences of boilerplate chunks are removed.
    """

    def __init__(
        self,
        *,
        n: int = 5,
        threshold: int = 3,
        min_chunk_length: int = 20,
    ) -> None:
        self._n = max(n, 2)
        self._threshold = max(threshold, 2)
        self._min_chunk_length = min_chunk_length

    def clean(self, text: str) -> str:
        lines = text.splitlines()
        # Only consider non-empty, non-Markdown-syntax lines for boilerplate detection
        candidates = [
            (idx, stripped)
            for idx, stripped in enumerate(l.strip() for l in lines)
            if stripped and not stripped.startswith(("#", "!", "|", "-", "*", ">"))
        ]

        if not candidates:
            return text

        # Build n-gram frequency map
        chunk_counter: Counter[str] = Counter()
        index_to_chunks: dict[int, list[str]] = {}

        for idx, stripped in candidates:
            words = stripped.split()
            if len(words) < self._n:
                continue
            chunks: list[str] = []
            for i in range(len(words) - self._n + 1):
                chunk = " ".join(words[i : i + self._n])
                if len(chunk) >= self._min_chunk_length:
                    chunk_counter[chunk] += 1
                    chunks.append(chunk)
            if chunks:
                index_to_chunks[idx] = chunks

        if not chunk_counter:
            return text

        # Identify boilerplate chunks
        boilerplate = {
            chunk for chunk, count in chunk_counter.items()
            if count >= self._threshold
        }

        if not boilerplate:
            return text

        logger.debug(
            "RepetitionFilter found %d boilerplate chunks", len(boilerplate)
        )

        # Remove lines that are dominated by boilerplate chunks
        to_remove: set[int] = set()
        for idx, chunks in index_to_chunks.items():
            if not chunks:
                continue
            boilerplate_hits = sum(1 for c in chunks if c in boilerplate)
            if boilerplate_hits / len(chunks) >= 0.5:
                to_remove.add(idx)

        # Rebuild text, skipping removed lines
        cleaned_lines = [
            line for i, line in enumerate(lines) if i not in to_remove
        ]
        return "\n".join(cleaned_lines).strip()


# ---------------------------------------------------------------------------
# Layer 3 — OCR correction
# ---------------------------------------------------------------------------


class OCRCorrector:
    """Fix common OCR mis-recognitions and normalise typography.

    Mappings are conservative — only high-confidence substitutions are applied
    so that legitimate text (e.g. source code, chemical formulae) is not
    mangled.
    """

    # Character-level substitutions applied globally
    CHAR_MAP: dict[str, str] = {
        "ﬁ": "fi",   # ligature
        "ﬂ": "fl",   # ligature
        "’": "'",    # smart quote
        "‘": "'",
        "“": '"',
        "”": '"',
        "–": "-",    # en-dash
        "—": "-",    # em-dash
        "…": "...",
        " ": " ",    # non-breaking space
        "　": " ",  # full-width space
    }

    # Full-width -> half-width for common CJK punctuation overlaps
    FULLWIDTH_MAP: dict[str, str] = {
        "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
        "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
        "Ａ": "A", "Ｂ": "B", "Ｃ": "C", "Ｄ": "D", "Ｅ": "E",
        "Ｆ": "F", "Ｇ": "G", "Ｈ": "H", "Ｉ": "I", "Ｊ": "J",
        "Ｋ": "K", "Ｌ": "L", "Ｍ": "M", "Ｎ": "N", "Ｏ": "O",
        "Ｐ": "P", "Ｑ": "Q", "Ｒ": "R", "Ｓ": "S", "Ｔ": "T",
        "Ｕ": "U", "Ｖ": "V", "Ｗ": "W", "Ｘ": "X", "Ｙ": "Y",
        "Ｚ": "Z",
        "ａ": "a", "ｂ": "b", "ｃ": "c", "ｄ": "d", "ｅ": "e",
        "ｆ": "f", "ｇ": "g", "ｈ": "h", "ｉ": "i", "ｊ": "j",
        "ｋ": "k", "ｌ": "l", "ｍ": "m", "ｎ": "n", "ｏ": "o",
        "ｐ": "p", "ｑ": "q", "ｒ": "r", "ｓ": "s", "ｔ": "t",
        "ｕ": "u", "ｖ": "v", "ｗ": "w", "ｘ": "x", "ｙ": "y",
        "ｚ": "z",
    }

    # Regex patterns for common OCR confusion (word-level)
    OCR_PATTERNS: list[tuple[re.Pattern, str]] = [
        # "1" mistaken for "l" at word start (heuristic: only if surrounded by letters)
        (re.compile(r"(?<=[a-zA-Z])0(?=[a-zA-Z])"), "O"),   # 0 in middle of word -> O
    ]

    def __init__(
        self,
        *,
        fix_ligatures: bool = True,
        fix_quotes: bool = True,
        fix_dashes: bool = True,
        normalise_fullwidth: bool = True,
    ) -> None:
        self._fix_ligatures = fix_ligatures
        self._fix_quotes = fix_quotes
        self._fix_dashes = fix_dashes
        self._normalise_fullwidth = normalise_fullwidth

    def clean(self, text: str) -> str:
        # Character-level replacements
        trans_map = {}
        if self._fix_ligatures:
            trans_map.update({"ﬁ": "fi", "ﬂ": "fl"})
        if self._fix_quotes:
            trans_map.update({"’": "'", "‘": "'", "“": '"', "”": '"'})
        if self._fix_dashes:
            trans_map.update({"–": "-", "—": "-", "…": "..."})

        # Always do whitespace normalisation
        trans_map.update({" ": " ", "　": " "})

        if trans_map:
            text = text.translate(str.maketrans(trans_map))

        if self._normalise_fullwidth:
            text = text.translate(str.maketrans(self.FULLWIDTH_MAP))

        # Regex-level substitutions
        for pat, repl in self.OCR_PATTERNS:
            text = pat.sub(repl, text)

        return text


# ---------------------------------------------------------------------------
# Layer 4 — Paragraph repair (layout recovery)
# ---------------------------------------------------------------------------


class ParagraphRepair:
    """Attempt to recover paragraph boundaries damaged by column layout.

    MinerU (and most layout parsers) emit text line-by-line.  When a paragraph
    wraps across a line break, the Markdown output often keeps the lines
    separate, producing a "staircase" of short lines instead of a flowing
    paragraph.

    Heuristic:
    * If a line ends without terminal punctuation (`.!?。！？`) AND
      the next line starts with a lower-case letter or a CJK character,
      join them with a single space.
    * Be conservative — never join across blank lines or Markdown headings.
    """

    TERMINAL_PUNCT = frozenset(".!?。！？")

    def __init__(
        self,
        *,
        join_fragments: bool = True,
    ) -> None:
        self._join_fragments = join_fragments

    def clean(self, text: str) -> str:
        if not self._join_fragments:
            return text

        lines = text.splitlines()
        merged: list[str] = []

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # Never merge headings, list items, tables, images, blockquotes
            if stripped.startswith(("#", "!", "|", "-", "*", ">", "```")):
                merged.append(line)
                i += 1
                continue

            # Accumulate continuation lines
            buffer = stripped
            i += 1
            while i < len(lines):
                next_line = lines[i].strip()

                # Stop at blank lines or structural Markdown
                if not next_line or next_line.startswith(
                    ("#", "!", "|", "-", "*", ">", "```")
                ):
                    break

                # Stop if current buffer ends with terminal punctuation
                if buffer and buffer[-1] in self.TERMINAL_PUNCT:
                    break

                # Stop if next line starts with upper-case (likely new sentence)
                if next_line[0].isupper():
                    break

                # Join
                buffer += " " + next_line
                i += 1

            merged.append(buffer)

        return "\n".join(merged).strip()


# ---------------------------------------------------------------------------
# Orchestrator — compose the 4 layers
# ---------------------------------------------------------------------------


class TextCleaner:
    """Composable 4-layer text cleaning pipeline.

    Usage::

        cleaner = TextCleaner(config={"repetition_filter": True, "n": 5})
        cleaned = cleaner.clean(raw_markdown)
    """

    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._layers: list[Any] = []

        if cfg.get("rule_cleaner", True):
            self._layers.append(
                RuleCleaner(remove_page_numbers=cfg.get("remove_page_numbers", True))
            )

        if cfg.get("repetition_filter", True):
            self._layers.append(
                RepetitionFilter(
                    n=cfg.get("repetition_n", 5),
                    threshold=cfg.get("repetition_threshold", 3),
                    min_chunk_length=cfg.get("repetition_min_length", 20),
                )
            )

        if cfg.get("ocr_corrector", True):
            self._layers.append(
                OCRCorrector(
                    fix_ligatures=cfg.get("fix_ligatures", True),
                    fix_quotes=cfg.get("fix_quotes", True),
                    fix_dashes=cfg.get("fix_dashes", True),
                    normalise_fullwidth=cfg.get("normalise_fullwidth", True),
                )
            )

        if cfg.get("paragraph_repair", True):
            self._layers.append(
                ParagraphRepair(join_fragments=cfg.get("join_fragments", True))
            )

    def clean(self, text: str) -> str:
        """Run the configured cleaning layers sequentially."""
        original_len = len(text)
        for layer in self._layers:
            text = layer.clean(text)
        cleaned_len = len(text)

        if cleaned_len != original_len:
            logger.debug(
                "TextCleaner reduced content %d -> %d chars",
                original_len,
                cleaned_len,
            )
        return text
