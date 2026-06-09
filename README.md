# 🛒 Agentic RAG for E-Commerce

A production-grade Agentic RAG framework for e-commerce scenarios. Built with a custom Plan-and-Execute architecture featuring a 7-state state machine, parallel tool execution, multi-layer fault resilience, and real-time review monitoring with Feishu integration.

> **Status**: Phase 1 complete (Agent framework + Demo). 33 tests, 16 health checks, 6 demo scripts.

## Architecture

```
User Input → InputSanitizer → Planner → PlanValidator → Executor → Reflector → Synthesizer → Response
                  │               ↑            │            │           │
                  │         (replan)            │       SemanticGuard    │
                  │                             │            │           │
                  ▼                             ▼            ▼           ▼
             Injection Gate              [Clarifier]   asyncio.gather   Critique
```

- **Plan-and-Execute**: Global planning → parallel execution → post-hoc reflection. More predictable and lower latency than ReAct for structured e-commerce tasks.
- **7-State State Machine**: `CLARIFYING → PLANNING → VALIDATING → EXECUTING → REFLECTING → SYNTHESIZING → DONE`
- **5-Layer Fault Resilience**: Injection detection → Plan validation → Degradation policies → Semantic guard → Canary auto-rollback

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests (33/33)
python -m pytest tests/agentic_rag/ -v

# System health check (16/16)
python health_check.py

# Performance benchmarks
python benchmark.py

# Run demo scripts
python demo_festival_gear.py      # Full agent trace (music festival gear)
python demo_resilience.py         # Failure → self-rescue scenarios
python demo_rollback.py           # Canary auto-rollback
python demo_interview.py          # 5 interview Q&A demos

# Launch Streamlit panels
streamlit run app.py              # Scenario demo (negative reviews, womenswear, competitor analysis, returns)
streamlit run admin.py            # Admin dashboard (trace viewer, metrics, experiment config)
```

## Demo Scenarios

| Scenario | Description | Key Feature |
|---|---|---|
| **Review Monitor** | Negative reviews (≤3 stars) automatically classified, pushed to Feishu group, recorded in Bitable | Real-time alerting |
| **Womenswear Guide** | Vague intent → clarification → personalized recommendation + complementary items | Intent understanding |
| **Competitor Analysis** | Multi-platform pricing comparison (Theory blazer across Tmall/JD/Douyin/PDD) | Operations intelligence |
| **Return Processing** | Quality/sizing/color issues → RMA creation → Feishu notification | Supply chain automation |

## Key Features

- **Intent Clarification**: Vague requests trigger targeted follow-up questions, max 2 rounds before Best-Effort fallback
- **Parallel Execution**: Independent tools run via `asyncio.gather` (2.0x speedup over sequential)
- **SemanticGuard**: Step-level rules engine catches contradictory operations in real-time (e.g., "send coupon to user with 0 orders")
- **5 Degradation Policies**: Fail Fast / Return Cached / Skip / Inform User / Retry with Backoff
- **Intent-Tool Whitelist**: Prevents Planner injection from calling unauthorized tools
- **Canary Rollback**: Automated rollback when error rate exceeds guardrail threshold
- **Token Budget**: Diagnostic-priority compression preserves failure info over success summaries
- **OpenTelemetry**: Distributed tracing across all agent phases
- **i18n**: Chinese/English toggle for all Streamlit panels (139 translation keys)

## Project Structure

```
agentic_rag/              # Core package (~40 files)
├── agent/                # Plan-and-Execute Agent engine (7 files)
├── tools/                # Tool system + adapters + contracts (12 files)
├── reflection/           # Reflector + rule validator (2 files)
├── observability/        # Logging, tracing, metrics, token budget (5 files)
├── evaluation/           # Attribution, rollout, experiments (3 files)
├── memory/               # Tiered memory compressor (2 files)
├── dify/                 # Dify integration + callback router (2 files)
├── integrations/         # Feishu bot + Bitable (1 file)
└── demo/                 # Mock data + simulator (2 files)

app.py                    # Streamlit scenario demo
admin.py                  # Streamlit admin dashboard
demo_*.py                 # CLI demo scripts (6 total)
benchmark.py              # Performance benchmark suite
health_check.py           # 16-item system health check
tests/                    # 33 unit + 9 contract tests

pdf_parser/rag/           # Existing RAG pipeline (ChromaDB + FTS5 + Cross-Encoder)
```

## Feishu Integration

Set your webhook URL in environment variables:

```bash
export AGENTIC_RAG_FEISHU_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_HOOK_ID"
```

When enabled, negative review alerts are pushed to your Feishu group chat as interactive cards, and records are stored in the Bitable (simulated as local JSON in demo mode).

## Tech Stack

Python 3.13 / asyncio / FastAPI / Streamlit / Pydantic / ChromaDB + SQLite FTS5 / LlamaIndex / Neo4j / Dify / OpenTelemetry / Prometheus / pytest
