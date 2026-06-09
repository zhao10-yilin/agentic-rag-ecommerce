"""Semantic chunking for Markdown documents.

The chunker uses a two-pass strategy:

1. **Structural pass** — split on Markdown headings (``#`` … ``######``) to
   produce coarse sections that respect the document's logical hierarchy.
2. **Semantic pass** — for sections that exceed the target size, detect
   semantic boundaries by measuring cosine distance between adjacent
   sentence embeddings.  When no embedder is available it falls back to
   paragraph-level splitting.

The output is a set of **small chunks** (for retrieval) linked to **big
chunks** (for LLM context) via ``parent_chunk_id``.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Callable

from pdf_parser.rag.models import DocumentChunk

logger = logging.getLogger(__name__)

# Approximate token-to-character ratio for Chinese + English mixed text.
# Chinese: ~1.5 chars/token; English: ~4 chars/token.  We use 2.5 as a
# conservative blended estimate.
CHARS_PER_TOKEN = 2.5

# Default target sizes in tokens
DEFAULT_SMALL_CHUNK_TOKENS = 256
DEFAULT_BIG_CHUNK_TOKENS = 1024
MAX_SMALL_CHUNK_TOKENS = 512  # hard cap — split regardless of semantics

# Sentence boundary regex: matches Chinese/English sentence delimiters
_SENTENCE_RE = re.compile(
    r"(?<=[。！？.!?\n])\s*",
)

# Markdown heading pattern
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def _estimate_tokens(text: str) -> int:
    """Rough token count for mixed Chinese/English text."""
    return max(1, int(len(text) / CHARS_PER_TOKEN))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class SemanticChunker:
    """Split Markdown text into retrieval-optimised chunks while preserving
    document structure and semantic coherence.

    Parameters
    ----------
    target_small_tokens:
        Target size for retrieval chunks in tokens (default 256).
    target_big_tokens:
        Target size for context chunks in tokens (default 1024).
    overlap_sentences:
        Number of sentences to overlap between adjacent small chunks.
    semantic_threshold:
        Cosine *distance* (1 - similarity) above which a sentence boundary
        is considered a semantic break.  Lower values produce more splits.
        Only used when an *embed_fn* is provided.
    embed_fn:
        Optional callable ``(texts: list[str]) -> list[list[float]]`` for
        semantic boundary detection.  When ``None`` the chunker falls back
        to paragraph splitting.
    """

    def __init__(
        self,
        *,
        target_small_tokens: int = DEFAULT_SMALL_CHUNK_TOKENS,
        target_big_tokens: int = DEFAULT_BIG_CHUNK_TOKENS,
        overlap_sentences: int = 2,
        semantic_threshold: float = 0.35,
        embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> None:
        self._target_small = target_small_tokens
        self._target_big = target_big_tokens
        self._overlap = overlap_sentences
        self._semantic_threshold = semantic_threshold
        self._embed_fn = embed_fn

        self._target_small_chars = int(target_small_tokens * CHARS_PER_TOKEN)
        self._target_big_chars = int(target_big_tokens * CHARS_PER_TOKEN)
        self._max_small_chars = int(MAX_SMALL_CHUNK_TOKENS * CHARS_PER_TOKEN)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def chunk(self, text: str, *, file_id: str) -> list[DocumentChunk]:
        """Split *text* into small and big chunks linked by parent IDs.

        Returns a flat list containing both levels.  Callers typically
        index only ``chunk_level='small'`` chunks for retrieval and use
        ``parent_chunk_id`` to fetch context at query time.
        """
        if not text.strip():
            return []

        sections = self._split_by_headings(text)
        all_chunks: list[DocumentChunk] = []

        for section in sections:
            heading_path, section_text, char_offset = section

            if not section_text.strip():
                continue

            # Create the big chunk (context-level)
            big = self._make_chunk(
                file_id=file_id,
                text=section_text,
                heading_path=heading_path,
                level="big",
                char_offset=char_offset,
            )
            all_chunks.append(big)

            # Subdivide into small chunks (retrieval-level)
            smalls = self._split_semantic(
                text=section_text,
                file_id=file_id,
                heading_path=heading_path,
                parent_id=big.chunk_id,
                char_offset=char_offset,
            )
            all_chunks.extend(smalls)

        logger.info(
            "SemanticChunker produced %d chunks for %s (%d big, %d small)",
            len(all_chunks),
            file_id,
            sum(1 for c in all_chunks if c.chunk_level == "big"),
            sum(1 for c in all_chunks if c.chunk_level == "small"),
        )
        return all_chunks

    # ------------------------------------------------------------------
    # Structural pass — heading-based splitting
    # ------------------------------------------------------------------

    @staticmethod
    def _split_by_headings(text: str) -> list[tuple[list[str], str, int]]:
        """Parse *text* into a flat list of ``(heading_path, body, offset)``.

        Each time a heading is encountered a new section begins.  The
        heading path is a breadcrumb like ``['第三章', '第107条']`` built
        from the current heading stack.
        """
        lines = text.splitlines()
        sections: list[tuple[list[str], str, int]] = []
        heading_stack: list[tuple[int, str]] = []  # [(level, title), ...]
        body_lines: list[str] = []
        section_start = 0
        char_cursor = 0

        def _path() -> list[str]:
            return [title for _, title in heading_stack]

        for line in lines:
            m = _HEADING_RE.match(line)
            if m:
                # Flush current section
                if body_lines:
                    body = "\n".join(body_lines).strip()
                    if body:
                        sections.append((_path(), body, section_start))
                    body_lines = []

                level = len(m.group(1))
                title = m.group(2).strip()

                # Pop headings at same or deeper level
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, title))

                section_start = char_cursor + len(line) + 1  # +1 for newline
            else:
                body_lines.append(line)

            char_cursor += len(line) + 1

        # Flush final section
        if body_lines:
            body = "\n".join(body_lines).strip()
            if body:
                sections.append((_path(), body, section_start))

        return sections

    # ------------------------------------------------------------------
    # Semantic pass — sentence-level splitting
    # ------------------------------------------------------------------

    def _split_semantic(
        self,
        text: str,
        *,
        file_id: str,
        heading_path: list[str],
        parent_id: str,
        char_offset: int,
    ) -> list[DocumentChunk]:
        """Split a section body into small retrieval chunks.

        Strategy (in priority order):
        1. If the section fits in one small chunk, return it as-is.
        2. If an embed function is available, detect semantic boundaries
           at the sentence level and split where the semantic distance
           between adjacent sentences exceeds *semantic_threshold*.
        3. Otherwise fall back to paragraph-level splitting.
        """
        if _estimate_tokens(text) <= self._target_small:
            chunk = self._make_chunk(
                file_id=file_id,
                text=text,
                heading_path=heading_path,
                level="small",
                parent_id=parent_id,
                char_offset=char_offset,
            )
            return [chunk]

        if self._embed_fn is not None:
            return self._semantic_split(
                text,
                file_id=file_id,
                heading_path=heading_path,
                parent_id=parent_id,
                char_offset=char_offset,
            )

        return self._paragraph_split(
            text,
            file_id=file_id,
            heading_path=heading_path,
            parent_id=parent_id,
            char_offset=char_offset,
        )

    def _semantic_split(
        self,
        text: str,
        *,
        file_id: str,
        heading_path: list[str],
        parent_id: str,
        char_offset: int,
    ) -> list[DocumentChunk]:
        """Split using sentence-embedding similarity to find natural breaks."""
        sentences = self._sentencize(text)
        if len(sentences) <= 1:
            return [
                self._make_chunk(
                    file_id=file_id,
                    text=text,
                    heading_path=heading_path,
                    level="small",
                    parent_id=parent_id,
                    char_offset=char_offset,
                )
            ]

        # Compute embeddings for every sentence
        try:
            embeddings = self._embed_fn(sentences)  # type: ignore[call-arg]
        except Exception:
            logger.warning(
                "Embedding call failed during semantic split — "
                "falling back to paragraph splitting"
            )
            return self._paragraph_split(
                text,
                file_id=file_id,
                heading_path=heading_path,
                parent_id=parent_id,
                char_offset=char_offset,
            )

        # Compute cosine distances between adjacent sentences
        distances: list[float] = []
        for i in range(len(embeddings) - 1):
            dist = _cosine_distance(embeddings[i], embeddings[i + 1])
            distances.append(dist)

        # Find breakpoints where distance exceeds threshold
        breakpoints: set[int] = set()
        for i, dist in enumerate(distances):
            if dist > self._semantic_threshold:
                breakpoints.add(i + 1)  # split AFTER sentence i

        # Greedy merge into chunks respecting target size + breakpoints
        chunks: list[DocumentChunk] = []
        buf: list[str] = []
        buf_chars = 0
        buf_start = 0
        cursor = 0

        for i, sent in enumerate(sentences):
            sent_chars = len(sent)

            should_break = (
                i in breakpoints
                and buf
                and buf_chars + sent_chars > self._max_small_chars // 3
            )
            would_overflow = (
                buf_chars + sent_chars > self._max_small_chars and buf
            )

            if should_break or would_overflow:
                chunks.append(
                    self._make_chunk(
                        file_id=file_id,
                        text="".join(buf),
                        heading_path=heading_path,
                        level="small",
                        parent_id=parent_id,
                        char_offset=char_offset + buf_start,
                    )
                )

                # Carry over overlap sentences
                overlap_count = min(self._overlap, len(buf))
                if overlap_count > 0 and i > 0:
                    overlap_sents = sentences[max(0, i - overlap_count) : i]
                    buf = list(overlap_sents)
                    buf_chars = sum(len(s) for s in buf)
                    # Recompute buf_start based on overlap window
                    buf_start = cursor - buf_chars
                else:
                    buf = []
                    buf_chars = 0
                    buf_start = cursor

            buf.append(sent)
            buf_chars += sent_chars
            cursor += sent_chars

        if buf:
            chunks.append(
                self._make_chunk(
                    file_id=file_id,
                    text="".join(buf),
                    heading_path=heading_path,
                    level="small",
                    parent_id=parent_id,
                    char_offset=char_offset + buf_start,
                )
            )

        return chunks

    def _paragraph_split(
        self,
        text: str,
        *,
        file_id: str,
        heading_path: list[str],
        parent_id: str,
        char_offset: int,
    ) -> list[DocumentChunk]:
        """Fallback: split by paragraphs, merging up to target size."""
        paragraphs = re.split(r"\n{2,}", text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        if not paragraphs:
            return []

        chunks: list[DocumentChunk] = []
        buf: list[str] = []
        buf_chars = 0
        buf_start = 0
        cursor = 0

        for para in paragraphs:
            para_chars = len(para)
            para_offset = text.index(para, cursor) if para in text[cursor:] else cursor

            if buf_chars + para_chars > self._max_small_chars and buf:
                chunks.append(
                    self._make_chunk(
                        file_id=file_id,
                        text="\n\n".join(buf),
                        heading_path=heading_path,
                        level="small",
                        parent_id=parent_id,
                        char_offset=char_offset + buf_start,
                    )
                )
                buf = []
                buf_chars = 0
                buf_start = para_offset

            buf.append(para)
            buf_chars += para_chars
            cursor = para_offset + para_chars

        if buf:
            chunks.append(
                self._make_chunk(
                    file_id=file_id,
                    text="\n\n".join(buf),
                    heading_path=heading_path,
                    level="small",
                    parent_id=parent_id,
                    char_offset=char_offset + buf_start,
                )
            )

        return chunks

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_chunk(
        *,
        file_id: str,
        text: str,
        heading_path: list[str],
        level: str,
        char_offset: int,
        parent_id: str | None = None,
    ) -> DocumentChunk:
        """Build a :class:`DocumentChunk` with consistent metadata."""
        return DocumentChunk(
            chunk_id=uuid.uuid4().hex[:12],
            file_id=file_id,
            text=text.strip(),
            heading_path=list(heading_path),
            chunk_level=level,
            parent_chunk_id=parent_id,
            char_start=char_offset,
            char_end=char_offset + len(text),
        )

    @staticmethod
    def _sentencize(text: str) -> list[str]:
        """Split *text* into sentences, keeping delimiters attached.

        Handles both Chinese (``。！？``) and English (``.!?``) punctuation,
        plus newlines between paragraphs.
        """
        # Normalise newlines: treat paragraph breaks as sentence boundaries
        text = re.sub(r"\n{2,}", "\n", text)
        text = re.sub(r"\n", "。\n", text)

        parts = _SENTENCE_RE.split(text)
        sentences: list[str] = []
        buf = ""
        for part in parts:
            buf += part
            if buf.rstrip() and (
                buf.rstrip()[-1] in "。！？.!?\n" or len(buf) > 200
            ):
                stripped = buf.strip()
                if stripped:
                    sentences.append(stripped)
                buf = ""
        if buf.strip():
            sentences.append(buf.strip())
        return sentences


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """Return ``1 - cosine_similarity(a, b)``, assuming L2-normalised vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    # Clamp to [-1, 1] to guard against floating-point drift
    sim = max(-1.0, min(1.0, dot))
    return 1.0 - sim
