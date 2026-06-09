"""CLI toolkit for retrieval quality evaluation with human annotation.

Usage
-----

Generate candidate queries from the corpus::

    python -m pdf_parser.rag.eval_cli generate-queries \\
        --num 20 --output queries.jsonl

Annotate relevance interactively::

    python -m pdf_parser.rag.eval_cli annotate \\
        --queries queries.jsonl \\
        --output labeled_dataset.jsonl

Run evaluation against a labeled dataset::

    python -m pdf_parser.rag.eval_cli evaluate \\
        --dataset labeled_dataset.jsonl \\
        --output report.json

Compare two evaluation runs::

    python -m pdf_parser.rag.eval_cli compare \\
        --baseline report_v1.json \\
        --candidate report_v2.json
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("eval_cli")

# ---------------------------------------------------------------------------
# Shared infrastructure (lazy-loaded)
# ---------------------------------------------------------------------------

_embedder: Any = None
_dense_store: Any = None
_sparse_store: Any = None
_retriever: Any = None


def _get_retriever() -> Any:
    """Lazy-init the hybrid retriever from existing indexes."""
    global _embedder, _dense_store, _sparse_store, _retriever
    if _retriever is not None:
        return _retriever

    from pdf_parser.rag.embedder import EmbeddingService, SentenceTransformerEmbedder
    from pdf_parser.rag.vector_store import ChromaVectorStore, SQLiteFTSStore
    from pdf_parser.rag.retriever import HybridRetriever

    logger.info("Loading embedding model (this may take a few seconds)...")

    _embedder = EmbeddingService(SentenceTransformerEmbedder())
    _dense_store = ChromaVectorStore()
    _sparse_store = SQLiteFTSStore()
    _retriever = HybridRetriever(
        dense_store=_dense_store,
        sparse_store=_sparse_store,
        embed_query_fn=_embedder.embed_query,
    )
    logger.info(
        "Retriever ready: dense=%d chunks, sparse=%d chunks",
        _dense_store.count(),
        _sparse_store.count(),
    )
    return _retriever


def _get_all_chunks() -> list[str]:
    """Return all chunk_ids from the sparse store."""
    global _sparse_store
    if _sparse_store is None:
        _get_retriever()
    # Use a dummy search that matches everything
    try:
        # Get chunks via metadata table
        import sqlite3

        assert _sparse_store is not None
        with sqlite3.connect(str(_sparse_store._db_path)) as conn:
            rows = conn.execute(
                "SELECT chunk_id FROM chunk_meta WHERE chunk_level='small'"
            ).fetchall()
            return [r[0] for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Command: generate-queries
# ---------------------------------------------------------------------------


def cmd_generate_queries(args: list[str]) -> None:
    """Generate candidate queries for annotation.

    Strategy: sample diverse headings and important-sounding chunks from
    the corpus as query seeds.  The annotator can later edit/refine them.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Generate candidate queries")
    parser.add_argument("--num", type=int, default=20, help="Number of queries to generate")
    parser.add_argument("--output", type=str, default="queries.jsonl", help="Output file")
    opts = parser.parse_args(args)

    retriever = _get_retriever()
    sparse = _sparse_store
    if sparse is None or sparse.count() == 0:
        print("[ERROR] No chunks found in the index.  Index some documents first.")
        print("  Example: POST /rag/index with your PDF files")
        sys.exit(1)

    import sqlite3

    queries: list[dict[str, str]] = []
    seen_headings: set[str] = set()

    with sqlite3.connect(str(sparse._db_path)) as conn:
        # Sample diverse heading paths
        rows = conn.execute(
            """
            SELECT DISTINCT heading_path FROM chunk_meta
            WHERE heading_path != '' AND chunk_level = 'small'
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (opts.num * 3,),
        ).fetchall()

        for row in rows:
            heading = row[0]
            if heading in seen_headings:
                continue
            seen_headings.add(heading)

            # Generate a question-style query from the heading
            parts = heading.split(" > ")
            topic = parts[-1] if parts else heading
            query_text = f"{topic}是什么？"
            queries.append({"query_id": f"q{len(queries):04d}", "query_text": query_text})

            if len(queries) >= opts.num:
                break

    # If headings are sparse, also generate from chunk content
    if len(queries) < opts.num:
        with sqlite3.connect(str(sparse._db_path)) as conn:
            rows = conn.execute(
                """
                SELECT f.text FROM fts_chunks f
                JOIN chunk_meta cm ON f.chunk_id = cm.chunk_id
                WHERE cm.chunk_level = 'small'
                ORDER BY RANDOM()
                LIMIT ?
                """,
                (opts.num - len(queries),),
            ).fetchall()

            for row in rows:
                text = row[0][:80].rstrip("。，,. ")
                if text:
                    queries.append({
                        "query_id": f"q{len(queries):04d}",
                        "query_text": f"请解释以下内容：{text}...",
                    })

    # Write output
    out_path = Path(opts.output)
    with out_path.open("w", encoding="utf-8") as fh:
        for q in queries:
            fh.write(json.dumps(q, ensure_ascii=False) + "\n")

    print(f"[DONE] Generated {len(queries)} candidate queries → {out_path}")
    print("")
    print("Next step: Review and edit the queries file, then run:")
    print(f"  python -m pdf_parser.rag.eval_cli annotate --queries {opts.output} --output labeled_dataset.jsonl")


# ---------------------------------------------------------------------------
# Command: annotate
# ---------------------------------------------------------------------------


def cmd_annotate(args: list[str]) -> None:
    """Interactive human annotation of query-chunk relevance.

    For each query, the tool retrieves the top-N candidate chunks and
    prompts the annotator to score each one on a 0-3 scale.

    Controls during annotation:
        Enter / 0-3  — submit relevance score and advance
        s            — skip this chunk (no judgment)
        q            — quit and save progress
        h            — show help
    """
    import argparse

    parser = argparse.ArgumentParser(description="Annotate relevance interactively")
    parser.add_argument("--queries", type=str, required=True, help="Queries JSONL file")
    parser.add_argument("--output", type=str, default="labeled_dataset.jsonl", help="Output dataset")
    parser.add_argument("--candidates-per-query", type=int, default=10, help="Chunks to retrieve per query")
    parser.add_argument("--resume", action="store_true", help="Resume from existing output file")
    opts = parser.parse_args(args)

    # Load queries
    queries_path = Path(opts.queries)
    if not queries_path.exists():
        print(f"[ERROR] Queries file not found: {queries_path}")
        sys.exit(1)

    query_list: list[dict[str, str]] = []
    with queries_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                query_list.append(json.loads(line))

    if not query_list:
        print("[ERROR] No queries found in file.")
        sys.exit(1)

    # Load existing dataset if resuming
    from pdf_parser.rag.evaluation import EvaluationDataset

    out_path = Path(opts.output)
    dataset = EvaluationDataset()
    if opts.resume and out_path.exists():
        dataset = EvaluationDataset.load(out_path)
        print(f"[RESUME] Loaded {dataset.num_queries} already-annotated queries")

    # Count already-annotated queries to skip them
    already_annotated = set()
    for q in dataset.queries.values():
        if q.judgments:
            already_annotated.add(q.query_id)

    retriever = _get_retriever()

    print("=" * 60)
    print("  RELEVANCE ANNOTATION TOOL")
    print("=" * 60)
    print("")
    print("Score each (query, chunk) pair:")
    print("  0 = Irrelevant     — nothing to do with the query")
    print("  1 = Marginal       — touches the topic, doesn't answer")
    print("  2 = Relevant       — partially answers the query")
    print("  3 = Highly relevant — directly answers the query")
    print("")
    print("Controls: 0/1/2/3 = score | s = skip | q = quit & save | h = help")
    print("")

    try:
        for qi, q_data in enumerate(query_list, 1):
            qid = q_data["query_id"]
            qtext = q_data["query_text"]

            # Skip if already annotated (unless resuming and this one has no judgments yet)
            if qid in already_annotated:
                continue

            print(f"\n{'─' * 55}")
            print(f"[{qi}/{len(query_list)}] Query: {qtext}")
            print(f"{'─' * 55}")

            # Retrieve candidate chunks
            from pdf_parser.rag.models import QueryPlan

            plan = QueryPlan(original_query=qtext)
            results = retriever.retrieve(plan, top_k=opts.candidates_per_query)

            if not results:
                print("  (No results found for this query — skipping)")
                continue

            for ri, result in enumerate(results, 1):
                chunk = result.chunk
                snippet = chunk.text[:300].replace("\n", " ")
                heading = " > ".join(chunk.heading_path) if chunk.heading_path else "(no heading)"

                print(f"\n  ┌─ Chunk {ri}/{len(results)}")
                print(f"  │  ID: {chunk.chunk_id}")
                print(f"  │  File: {chunk.file_id}  |  {heading}")
                print(f"  │  Score: {result.hybrid_score:.3f}")
                print(f"  │  Text: {snippet}")
                if len(chunk.text) > 300:
                    print(f"  │  ... (+{len(chunk.text) - 300} more chars)")
                print(f"  └{'─' * 38}")

                while True:
                    try:
                        raw = input(f"  Relevance (0-3) [s=skip, q=quit]: ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        print("\n\n[SAVING] Interrupted. Saving progress...")
                        dataset.save(out_path)
                        print(f"[SAVED] {dataset.num_queries} queries → {out_path}")
                        return

                    if raw == "q":
                        print(f"\n[SAVING] Quitting. Progress saved.")
                        dataset.save(out_path)
                        print(f"[SAVED] {dataset.num_queries} queries, {dataset.total_judgments} judgments → {out_path}")
                        return
                    if raw == "s":
                        break
                    if raw == "h":
                        print("  0=Irrelevant 1=Marginal 2=Relevant 3=Highly relevant")
                        print("  s=skip chunk  q=quit and save")
                        continue
                    if raw in ("0", "1", "2", "3"):
                        dataset.add_judgment(
                            query_id=qid,
                            query_text=qtext,
                            chunk_id=chunk.chunk_id,
                            relevance=int(raw),
                            chunk_text_snippet=chunk.text[:200],
                        )
                        print(f"  ✓ Scored as {raw}")
                        break
                    print("  [Invalid] Enter 0-3, s, or q")

            # Save after each query
            dataset.save(out_path)

    except KeyboardInterrupt:
        print("\n\n[SAVING] Interrupted.")
    finally:
        dataset.save(out_path)
        print(f"\n[DONE] Saved {dataset.num_queries} queries with {dataset.total_judgments} judgments")
        print(f"       → {out_path}")
        stats = dataset.stats()
        print(f"\nDataset statistics:")
        print(f"  Queries with relevant chunks (>=2): {stats['queries_with_relevant_chunks']}")
        print(f"  Relevance distribution: {stats['relevance_distribution']}")
        print(f"\nNext step: Run evaluation:")
        print(f"  python -m pdf_parser.rag.eval_cli evaluate --dataset {opts.output} --output report.json")


# ---------------------------------------------------------------------------
# Command: evaluate
# ---------------------------------------------------------------------------


def cmd_evaluate(args: list[str]) -> None:
    """Run evaluation against a labeled dataset and print the report."""
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate retrieval quality")
    parser.add_argument("--dataset", type=str, required=True, help="Labeled dataset JSONL")
    parser.add_argument("--output", type=str, default=None, help="Save metrics JSON")
    parser.add_argument("--per-query", action="store_true", help="Show per-query breakdown")
    parser.add_argument(
        "--split", type=float, default=None,
        help="Hold-out fraction for testing (e.g. 0.3). When set, the dataset is "
             "split into train/test stratified by file_id. Metrics reported are on "
             "the held-out test set.",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for train/test split (default 42).",
    )
    parser.add_argument(
        "--cv", type=int, default=None,
        help="Run k-fold cross-validation with k folds. Overrides --split.",
    )
    opts = parser.parse_args(args)

    dataset_path = Path(opts.dataset)
    if not dataset_path.exists():
        print(f"[ERROR] Dataset not found: {dataset_path}")
        sys.exit(1)

    dataset = EvaluationDataset.load(dataset_path)
    if dataset.num_queries == 0:
        print("[ERROR] Dataset is empty.")
        sys.exit(1)

    print(f"\nLoaded dataset: {dataset.num_queries} queries, {dataset.total_judgments} judgments")
    stats = dataset.stats()
    print(f"  Relevance distribution: {stats['relevance_distribution']}")
    print(f"  Queries with relevant chunks: {stats['queries_with_relevant_chunks']}")

    retriever = _get_retriever()
    config = {
        "chunker_target_small_tokens": 256,
        "chunker_target_big_tokens": 1024,
        "embedding_model": "BAAI/bge-large-zh-v1.5",
    }

    # ---- K-fold cross-validation -------------------------------------------
    if opts.cv:
        from pdf_parser.rag.evaluation import run_cross_validation

        n_folds = opts.cv
        if n_folds < 2:
            print("[ERROR] --cv requires at least 2 folds")
            sys.exit(1)
        if n_folds > dataset.num_queries:
            n_folds = max(2, dataset.num_queries)
            print(f"[WARNING] Reducing folds to {n_folds} (not enough queries)")

        print(f"\nRunning {n_folds}-fold cross-validation (seed={opts.seed})...")

        cv_results = run_cross_validation(
            build_retriever_fn=lambda: retriever,
            dataset=dataset,
            n_folds=n_folds,
            seed=opts.seed,
        )

        # Print CV report
        agg = cv_results["aggregated"]
        print("\n" + "=" * 60)
        print(f"  CROSS-VALIDATION REPORT  ({n_folds} folds, {cv_results['total_queries']} queries)")
        print("=" * 60)
        print(f"  {'Metric':<18} {'Mean':>10} {'Std':>10}")
        print(f"{'─' * 60}")
        for name in ["ndcg@10", "mrr", "recall@10"]:
            m = agg[name]
            print(f"  {name:<18} {m['mean']:>10.4f} {m['std']:>10.4f}")
        print("=" * 60)
        print("\nInterpretation:")
        ndcg_mean = agg["ndcg@10"]["mean"]
        ndcg_std = agg["ndcg@10"]["std"]
        if ndcg_std < 0.02:
            print(f"  ✓ NDCG@10 is stable (std={ndcg_std:.3f}) — results are reproducible.")
        elif ndcg_std < 0.05:
            print(f"  ~ NDCG@10 has moderate variance (std={ndcg_std:.3f}). "
                  f"Consider more queries or stratified splitting.")
        else:
            print(f"  ✗ NDCG@10 is unstable (std={ndcg_std:.3f}). "
                  f"Dataset may be too small ({dataset.num_queries} queries) or "
                  f"queries too diverse for reliable measurement.")

        if opts.output:
            out_path = Path(opts.output)
            with out_path.open("w", encoding="utf-8") as fh:
                json.dump(cv_results, fh, ensure_ascii=False, indent=2)
            print(f"\n[SAVED] CV report → {out_path}")
        return

    # ---- Hold-out split ---------------------------------------------------
    if opts.split:
        from pdf_parser.rag.evaluation import train_test_split

        test_frac = opts.split
        if not (0.1 <= test_frac <= 0.5):
            print("[ERROR] --split should be between 0.1 and 0.5")
            sys.exit(1)

        # Build query→file_ids mapping from the stores
        query_file_ids: dict[str, set[str]] = {}
        sparse = _sparse_store
        if sparse is not None:
            import sqlite3
            with sqlite3.connect(str(sparse._db_path)) as conn:
                for q in dataset.queries.values():
                    fids: set[str] = set()
                    for j in q.judgments:
                        row = conn.execute(
                            "SELECT file_id FROM chunk_meta WHERE chunk_id = ?",
                            (j.chunk_id,),
                        ).fetchone()
                        if row:
                            fids.add(row["file_id"])
                    if fids:
                        query_file_ids[q.query_id] = fids

        if query_file_ids:
            n_with_fids = len(query_file_ids)
            n_queries = dataset.num_queries
            print(f"\n  (Stratified split: {n_with_fids}/{n_queries} queries have file_id mappings)")
        else:
            print("\n  (Simple random split — no file_id mappings available)")

        train_ds, test_ds = train_test_split(
            dataset,
            test_size=test_frac,
            seed=opts.seed,
            query_file_ids=query_file_ids if query_file_ids else None,
        )

        print(f"  Train set: {train_ds.num_queries} queries")
        print(f"  Test set:  {test_ds.num_queries} queries (held out)")

        evaluator = RetrievalEvaluator(retriever=retriever, dataset=test_ds)
        print("\nRunning evaluation on held-out test set...")
        metrics = evaluator.evaluate(config_snapshot=config)

        _print_eval_report(metrics, label="EVALUATION REPORT (HELD-OUT TEST SET)")

        if opts.per_query:
            _print_per_query(metrics)

        if opts.output:
            out_path = Path(opts.output)
            report = {
                "metrics": metrics.to_dict(),
                "dataset_stats": stats,
                "config": config,
                "split_info": {
                    "test_size": test_frac,
                    "seed": opts.seed,
                    "train_queries": train_ds.num_queries,
                    "test_queries": test_ds.num_queries,
                    "stratified": bool(query_file_ids),
                },
            }
            with out_path.open("w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
            print(f"\n[SAVED] Report → {out_path}")
            print("  Note: This score is on HELD-OUT data, not the same data used for tuning.")
        return

    # ---- Full evaluation (no split) ---------------------------------------
    evaluator = RetrievalEvaluator(retriever=retriever, dataset=dataset)

    print("\nRunning evaluation on full dataset...")
    print("  WARNING: If you tuned parameters on this same dataset, the scores are optimistic.")
    print("  Use --split 0.3 or --cv 5 for reliable evaluation.")

    metrics = evaluator.evaluate(config_snapshot=config)

    _print_eval_report(metrics, label="RETRIEVAL EVALUATION REPORT (FULL DATASET)")

    if opts.per_query:
        _print_per_query(metrics)

    if opts.output:
        out_path = Path(opts.output)
        report = {
            "metrics": metrics.to_dict(),
            "dataset_stats": stats,
            "config": config,
            "warning": "Scores may be optimistic if parameters were tuned on this dataset.",
        }
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\n[SAVED] Report → {out_path}")


def _print_eval_report(metrics: RetrievalMetrics, *, label: str = "RETRIEVAL EVALUATION REPORT") -> None:
    """Print a formatted evaluation report."""
    print("\n" + "=" * 55)
    print(f"  {label}")
    print("=" * 55)
    print(f"  Queries evaluated:  {metrics.num_queries}")
    print(f"  Time:               {metrics.elapsed_seconds:.2f}s")
    print(f"{'─' * 55}")
    print(f"  NDCG@1:             {metrics.ndcg_at_1:.4f}")
    print(f"  NDCG@5:             {metrics.ndcg_at_5:.4f}")
    print(f"  NDCG@10:            {metrics.ndcg_at_10:.4f}")
    print(f"  NDCG@20:            {metrics.ndcg_at_20:.4f}")
    print(f"{'─' * 55}")
    print(f"  MRR:                {metrics.mrr:.4f}")
    print(f"{'─' * 55}")
    print(f"  Recall@5:           {metrics.recall_at_5:.4f}")
    print(f"  Recall@10:          {metrics.recall_at_10:.4f}")
    print(f"  Recall@20:          {metrics.recall_at_20:.4f}")
    print(f"{'─' * 55}")
    print(f"  Precision@5:        {metrics.precision_at_5:.4f}")
    print(f"  Precision@10:       {metrics.precision_at_10:.4f}")
    print("=" * 55)


def _print_per_query(metrics: RetrievalMetrics) -> None:
    """Print per-query metrics breakdown."""
    print("\n\nPer-query breakdown:")
    for pq in metrics.per_query:
        status = "✓" if pq.get("mrr", 0) > 0 else "✗"
        print(f"  {status} {pq['query_id']}: {pq['query_text'][:60]}...")
        print(f"     MRR={pq['mrr']:.3f}  NDCG@10={pq['ndcg@10']:.3f}  "
              f"Recall@10={pq['recall@10']:.3f}")


def _print_interpretation(metrics: Any) -> None:
    """Print human-readable interpretation of the metrics."""
    ndcg = metrics.ndcg_at_10
    mrr = metrics.mrr

    if ndcg >= 0.8:
        quality = "Excellent"
    elif ndcg >= 0.6:
        quality = "Good"
    elif ndcg >= 0.4:
        quality = "Fair"
    else:
        quality = "Poor"

    print(f"  Overall quality: {quality}")

    suggestions = []
    if mrr < 0.5:
        suggestions.append("• MRR is low — the first relevant result appears too deep in the list. "
                          "Consider increasing reranker weight or tuning RRF parameters.")
    if ndcg < 0.4:
        suggestions.append("• NDCG is low — relevant chunks are not being found or ranked well. "
                          "Try: (1) smaller chunk sizes, (2) a different embedding model, "
                          "(3) enabling query rewriting with LLM.")
    if not suggestions:
        suggestions.append("• Metrics look reasonable. Continue annotating more queries "
                          "to increase statistical confidence.")

    for s in suggestions:
        print(f"  {s}")


# ---------------------------------------------------------------------------
# Command: compare
# ---------------------------------------------------------------------------


def cmd_compare(args: list[str]) -> None:
    """Compare two evaluation reports."""
    import argparse

    parser = argparse.ArgumentParser(description="Compare two evaluation runs")
    parser.add_argument("--baseline", type=str, required=True, help="Baseline report JSON")
    parser.add_argument("--candidate", type=str, required=True, help="Candidate report JSON")
    opts = parser.parse_args(args)

    def _load(path: str) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    baseline_data = _load(opts.baseline)
    candidate_data = _load(opts.candidate)

    b = baseline_data["metrics"]
    c = candidate_data["metrics"]

    print("\n" + "=" * 65)
    print("  EVALUATION COMPARISON")
    print("=" * 65)
    print(f"  Baseline : {opts.baseline}")
    print(f"  Candidate: {opts.candidate}")
    print(f"{'─' * 65}")
    print(f"  {'Metric':<18} {'Baseline':>12} {'Candidate':>12} {'Delta':>12}")
    print(f"{'─' * 65}")

    comparisons = [
        ("NDCG@1", b["ndcg"]["@1"], c["ndcg"]["@1"]),
        ("NDCG@5", b["ndcg"]["@5"], c["ndcg"]["@5"]),
        ("NDCG@10", b["ndcg"]["@10"], c["ndcg"]["@10"]),
        ("NDCG@20", b["ndcg"]["@20"], c["ndcg"]["@20"]),
        ("MRR", b["mrr"], c["mrr"]),
        ("Recall@5", b["recall"]["@5"], c["recall"]["@5"]),
        ("Recall@10", b["recall"]["@10"], c["recall"]["@10"]),
        ("Recall@20", b["recall"]["@20"], c["recall"]["@20"]),
        ("Precision@5", b["precision"]["@5"], c["precision"]["@5"]),
        ("Precision@10", b["precision"]["@10"], c["precision"]["@10"]),
    ]

    for name, base, cand in comparisons:
        delta = cand - base
        sign = "+" if delta >= 0 else ""
        direction = "▲" if delta > 0 else ("▼" if delta < 0 else "─")
        print(f"  {name:<18} {base:>12.4f} {cand:>12.4f} {sign}{delta:>11.4f} {direction}")

    print("=" * 65)

    # Overall verdict
    ndcg_delta = c["ndcg"]["@10"] - b["ndcg"]["@10"]
    if ndcg_delta > 0.01:
        print("\n  Verdict: Candidate is BETTER (NDCG@10 improved)")
    elif ndcg_delta < -0.01:
        print("\n  Verdict: Baseline is BETTER (NDCG@10 degraded)")
    else:
        print("\n  Verdict: No significant difference")


# ---------------------------------------------------------------------------
# Command: significance
# ---------------------------------------------------------------------------


def cmd_significance(args: list[str]) -> None:
    """Test whether the difference between two systems is statistically significant.

    Requires two evaluation reports with per-query data (generated by
    ``evaluate --per-query --output report.json``).
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Paired t-test for statistical significance"
    )
    parser.add_argument("--report-a", type=str, required=True, help="First evaluation report JSON")
    parser.add_argument("--report-b", type=str, required=True, help="Second evaluation report JSON")
    parser.add_argument(
        "--metric", type=str, default="ndcg@10",
        choices=["ndcg@1", "ndcg@5", "ndcg@10", "ndcg@20", "mrr", "recall@10"],
        help="Metric to test (default: ndcg@10)",
    )
    parser.add_argument(
        "--alpha", type=float, default=0.05,
        help="Significance level (default: 0.05)",
    )
    opts = parser.parse_args(args)

    from pdf_parser.rag.evaluation import paired_ttest

    def _load(path: str) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    report_a = _load(opts.report_a)
    report_b = _load(opts.report_b)

    # Extract per-query metric values, aligned by query_id
    pq_a = {pq["query_id"]: pq.get(opts.metric, 0.0) for pq in report_a["metrics"]["per_query"]}
    pq_b = {pq["query_id"]: pq.get(opts.metric, 0.0) for pq in report_b["metrics"]["per_query"]}

    # Only keep queries present in both reports
    common_ids = set(pq_a) & set(pq_b)
    if not common_ids:
        print("[ERROR] No common queries found between the two reports.")
        sys.exit(1)

    per_query_a = [pq_a[qid] for qid in sorted(common_ids)]
    per_query_b = [pq_b[qid] for qid in sorted(common_ids)]

    result = paired_ttest(per_query_a, per_query_b, alpha=opts.alpha)

    mean_a = sum(per_query_a) / len(per_query_a)
    mean_b = sum(per_query_b) / len(per_query_b)

    print("\n" + "=" * 60)
    print("  STATISTICAL SIGNIFICANCE TEST")
    print("=" * 60)
    print(f"  Metric:        {opts.metric}")
    print(f"  Report A mean: {mean_a:.4f}")
    print(f"  Report B mean: {mean_b:.4f}")
    print(f"  Mean diff:     {result['mean_diff']:.4f}")
    print(f"  Std diff:      {result['std_diff']:.4f}")
    print(f"  n (paired):    {result['n']}")
    print(f"{'─' * 60}")
    print(f"  t-statistic:   {result['t_statistic']:.4f}")
    print(f"  p-value:       {result['p_value']:.6f}")
    print(f"{'─' * 60}")
    print(f"  Verdict: {result['message']}")
    print("=" * 60)

    if result["significant"]:
        direction = "better" if mean_b > mean_a else "worse"
        print(f"\n  The difference is statistically significant (p < {opts.alpha}).")
        print(f"  System B ({opts.report_b}) is {direction} than System A "
              f"({opts.report_a}) on {opts.metric}.")
    else:
        print(f"\n  The difference is NOT statistically significant (p >= {opts.alpha}).")
        print(f"  The observed {mean_b - mean_a:+.4f} difference may be due to chance.")

    print(f"\n  To improve confidence:")
    print(f"  - Annotate more queries (currently {result['n']} paired observations)")
    print(f"  - Reduce per-query variance (std_diff={result['std_diff']:.4f})")
    print(f"  - Use a larger alpha if false positives are acceptable")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m pdf_parser.rag.eval_cli <command> [args]")
        print("")
        print("Commands:")
        print("  generate-queries   Create candidate queries from the index")
        print("  annotate           Interactive relevance annotation")
        print("  evaluate           Run evaluation (with --split or --cv for reliable results)")
        print("  compare            Compare two evaluation reports")
        print("  significance       Paired t-test for statistical significance")
        print("")
        print("Quick start:")
        print("  1. ... generate-queries --num 20 --output queries.jsonl")
        print("  2. ... annotate --queries queries.jsonl --output labeled.jsonl")
        print("  3. ... evaluate --dataset labeled.jsonl --split 0.3 --seed 42 --output report.json")
        print("")
        print("Reliable evaluation:")
        print("  4. ... evaluate --dataset labeled.jsonl --cv 5 --output cv_report.json")
        print("  5. ... significance --report-a report_v1.json --report-b report_v2.json")
        sys.exit(1)

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "generate-queries":
        cmd_generate_queries(rest)
    elif cmd == "annotate":
        cmd_annotate(rest)
    elif cmd == "evaluate":
        cmd_evaluate(rest)
    elif cmd == "compare":
        cmd_compare(rest)
    elif cmd == "significance":
        cmd_significance(rest)
    else:
        print(f"Unknown command: {cmd}")
        print("Available: generate-queries, annotate, evaluate, compare")
        sys.exit(1)


if __name__ == "__main__":
    main()
