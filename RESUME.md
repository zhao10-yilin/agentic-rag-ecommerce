"""
======================================================================
  Agentic RAG for E-Commerce — 项目简历文案
  复制下面任一版本到你的简历 "项目经验" 栏目
======================================================================
"""

# ============================================================================
# 中文版 — 完整版 (适合项目经验主条目)
# ============================================================================

CN_FULL = """
项目名称：电商场景生产级 Agentic RAG 智能体框架
个人角色：独立架构设计与核心开发
技术栈：Python 3.13 / asyncio / FastAPI / Streamlit / Pydantic /
        ChromaDB + SQLite FTS5 / LlamaIndex / Neo4j / Dify /
        OpenTelemetry / Prometheus / pytest

项目概述：
从零构建了一套面向电商场景的生产级 Agentic RAG 系统，采用 Plan-and-Execute
架构替代传统 ReAct 模式，支持将复杂电商需求（智能导购、个性化推荐、竞品分析、
售后处理）自动分解为有序的工具调用计划，并行执行以降低延迟。系统可作为平台
AI 导购、品牌智能客服、内部运营后台三个位置部署，通过工具注册表适配不同场景。

核心贡献：
1. 设计并实现了完整的 Plan-and-Execute Agent 框架，包含 7 状态状态机、
   意图澄清机制、规划校验器（工具存在性/参数类型/依赖合法性/循环检测/
   意图-工具白名单审计）、并行执行器（asyncio.gather, 2.0x 加速比）、
   LLM 反思纠错循环。

2. 构建五层故障防护体系：输入注入检测（正则引擎, <1ms）→ 计划结构校验
   → 5 种降级策略（Fail Fast / Cache / Skip / Inform / Retry-Backoff）
   → 步骤级语义护栏（实时拦截矛盾操作如"零订单发券"）
   → 灰度自动回滚（护栏触发, 零人工介入）。

3. 实现完整的可观测性栈：OpenTelemetry 分布式追踪、Prometheus 指标采集、
   JSON 结构化日志、内存 Trace Store 供甘特图可视化。

4. 建立工程质量基线：33 个单元测试 + 9 个业务合约测试（VIP 权限/订单状态/
   退款上限）、16 项系统健康自检、性能基准（P50/P99 延迟、5 并发压力）。

5. 构建 Streamlit 可视化管理后台，支持中英双语切换（139 翻译键），
   包含链路追踪甘特图、实时指标看板、灰度实验配置页面。

项目规模：55+ Python 文件, 33+ 测试用例, 6 个 Demo 脚本
"""

# ============================================================================
# 中文版 — 精简版 (适合一页简历, 3-4 条 bullet)
# ============================================================================

CN_SHORT = """
电商场景 Agentic RAG 智能体框架 | Python, asyncio, ChromaDB, LLM

- 从零设计并实现了 Plan-and-Execute Agent 架构（7 状态状态机/并行执行/
  反思纠错），替代传统 ReAct 模式，并行加速比 2.0x，P50 延迟 4.0s。
- 构建五层故障防护：注入检测 → 计划校验 → 5 种降级策略 → 步骤级语义护栏
  → 灰度自动回滚，单层 <1ms 开销，保障生产稳定性。
- 建立工程质量基线：33 测试 / 9 业务合约测试 / 16 项健康自检 / 性能基准 /
  OpenTelemetry 追踪 + Prometheus 指标，支持中英双语可视化管理后台。
"""

# ============================================================================
# 英文版 — Full Version
# ============================================================================

EN_FULL = """
Project: Production-Grade Agentic RAG Framework for E-Commerce
Role: Independent Architecture Design & Core Development
Stack: Python 3.13 / asyncio / FastAPI / Streamlit / Pydantic /
       ChromaDB + SQLite FTS5 / LlamaIndex / Neo4j / Dify /
       OpenTelemetry / Prometheus / pytest

Summary:
Built a production-grade Agentic RAG system from scratch for e-commerce
scenarios. Adopted Plan-and-Execute architecture over traditional ReAct,
enabling automatic decomposition of complex e-commerce requests (smart
shopping guide, personalized recommendations, competitor analysis,
after-sales processing) into ordered, parallel-executed tool-call plans.
Deployable as platform AI shopping assistant, brand customer service,
or internal operations dashboard via configurable tool registry.

Key Contributions:
1. Designed and implemented a complete Plan-and-Execute Agent framework
   with 7-state state machine, intent clarification mechanism, plan
   validator (tool existence / parameter types / dependency ordering /
   cycle detection / intent-tool whitelist audit), parallel executor
   (asyncio.gather, 2.0x speedup), and LLM reflection loop.

2. Built a five-layer fault-resilience stack: input injection detection
   (regex engine, <1ms) -> plan structure validation -> 5 degradation
   policies (Fail Fast / Cache / Skip / Inform User / Retry with Backoff)
   -> step-level semantic guard (real-time blocking of contradictory
   operations) -> canary auto-rollback (guardrail-triggered, no human
   intervention).

3. Implemented full observability: OpenTelemetry distributed tracing,
   Prometheus metrics, structured JSON logging, in-memory trace store
   for Gantt chart visualization.

4. Established engineering quality baseline: 33 unit tests + 9 business
   contract tests (VIP permissions / order status / refund limits),
   16-item system health check, performance benchmarks (P50/P99 latency,
   5-concurrency stress test).

5. Built Streamlit admin dashboard with Chinese/English i18n (139 keys),
   including trace Gantt viewer, real-time metrics dashboard, and canary
   experiment configuration panel.

Scale: 55+ Python files, 33+ test cases, 6 demo scripts
"""

# ============================================================================
# 英文版 — Short Version
# ============================================================================

EN_SHORT = """
Agentic RAG Framework for E-Commerce | Python, asyncio, ChromaDB, LLM

- Designed and implemented Plan-and-Execute Agent architecture (7-state
  FSM / parallel execution / reflection loop) from scratch, achieving
  2.0x parallelism speedup over sequential execution.
- Built five-layer fault-resilience stack: injection guard -> plan validation
  -> 5 degradation policies -> semantic guard -> canary auto-rollback.
- Established engineering baseline: 33 tests + 9 contract tests + 16-item
  health check + performance benchmarks + OpenTelemetry tracing + Prometheus.
"""

# ============================================================================
# 面试自我介绍可以用的一句话（电梯演讲）
# ============================================================================

ELEVATOR_PITCH_CN = """
我独立构建了一个面向电商场景的生产级 Agentic RAG 系统。它采用 Plan-and-Execute
架构——先让 LLM 全局规划、再用 asyncio 并行执行工具、最后反思纠错——比常见的
ReAct 模式在电商场景中延迟更低、行为更可预测。我设计了一套五层故障防护机制和
自动化灰度回滚系统，并建立了包括 33 个单元测试、16 项健康自检和性能基准在内的
工程质量基线。55 个 Python 文件，零外部 API 依赖即可完整演示。
"""

ELEVATOR_PITCH_EN = """
I independently built a production-grade Agentic RAG system for e-commerce.
It uses a Plan-and-Execute architecture—LLM plans globally, asyncio executes
tools in parallel, then a Reflector critiques and corrects—achieving lower
latency and more predictable behavior than common ReAct approaches. I designed
a five-layer fault-resilience stack with automated canary rollback, and
established an engineering baseline of 33 tests, 16 health checks, and
performance benchmarks. 55 Python files, fully demonstrable with zero
external API dependencies.
"""

if __name__ == "__main__":
    print("=== 中文完整版 ===")
    print(CN_FULL)
    print("\n=== 中文精简版 ===")
    print(CN_SHORT)
    print("\n=== English Full ===")
    print(EN_FULL)
    print("\n=== English Short ===")
    print(EN_SHORT)
