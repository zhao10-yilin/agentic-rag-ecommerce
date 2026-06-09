"""Internationalization module — Chinese/English toggle for all UI panels.

Usage::

    from agentic_rag.i18n import t, LANG

    print(t("nav.trace_viewer", LANG))  # "Trace Viewer" or "链路追踪"

Call ``set_lang("zh")`` or ``set_lang("en")`` before rendering.
Session state key: ``st.session_state.lang``.
"""

from __future__ import annotations

import streamlit as st
from typing import Callable

# ---------------------------------------------------------------------------
# Translation tables
# ---------------------------------------------------------------------------

ZH: dict[str, str] = {}
EN: dict[str, str] = {}


def _add(key: str, zh: str, en: str):
    ZH[key] = zh
    EN[key] = en


# ============================================================================
# Navigation
# ============================================================================
_add("nav.trace_viewer", "链路追踪", "Trace Viewer")
_add("nav.metrics_dashboard", "指标看板", "Metrics Dashboard")
_add("nav.experiment_config", "实验配置", "Experiment Config")
_add("nav.otel_traces", "分布式追踪", "OTEL Traces")
_add("nav.sidebar_title", "Agentic RAG 管理后台", "Agentic RAG Admin")
_add("nav.sidebar_caption", "生产级运维面板", "Production Admin Panel")
_add("nav.select_trace", "选择一条链路", "Select a trace")
_add("nav.scenario_demo", "场景演示", "Scenario Demo")
_add("nav.custom_input", "自定义对话", "Custom Input")

# ============================================================================
# Trace Viewer
# ============================================================================
_add("trace.title", "链路追踪", "Trace Viewer")
_add("trace.caption", "Agent 执行链路甘特图与依赖可视化", "Agent execution traces with Gantt timeline and dependency visualization")
_add("trace.no_traces", "暂无链路数据，请先运行 Agent。", "No traces yet. Run the agent to populate traces.")
_add("trace.duration", "总耗时", "Duration")
_add("trace.phases", "阶段数", "Phases")
_add("trace.tool_calls", "工具调用", "Tool Calls")
_add("trace.reflections", "反思轮数", "Reflections")
_add("trace.phase_timeline", "阶段时间线 (甘特图)", "Phase Timeline (Gantt)")
_add("trace.step_dep_graph", "步骤依赖图", "Step Dependency Graph")
_add("trace.tool_exec_details", "工具执行详情", "Tool Execution Details")
_add("trace.status", "状态", "Status")
_add("trace.error", "错误", "Error")

# ============================================================================
# Metrics Dashboard
# ============================================================================
_add("metrics.title", "指标看板", "Metrics Dashboard")
_add("metrics.caption", "实时运营指标与转化漏斗", "Real-time operational metrics and conversion funnel")
_add("metrics.no_data", "暂无指标数据，请先运行 Agent。", "No metrics available. Run agent traces to populate data.")
_add("metrics.kpi_header", "运营 KPI", "Operational KPIs")
_add("metrics.success_rate", "成功率", "Success Rate")
_add("metrics.error_rate", "错误率", "Error Rate")
_add("metrics.p50_latency", "P50 延迟", "P50 Latency")
_add("metrics.p99_latency", "P99 延迟", "P99 Latency")
_add("metrics.funnel_header", "转化漏斗", "Conversion Funnel")
_add("metrics.impressions", "展示", "Impressions")
_add("metrics.clarifications", "澄清", "Clarifications")
_add("metrics.successful", "成功", "Successful")
_add("metrics.errors", "错误", "Errors")
_add("metrics.tool_success_header", "工具级成功率", "Tool-Level Success Rates")
_add("metrics.avg_duration", "平均耗时", "Avg Duration")
_add("metrics.sample_size", "样本量", "Sample Size")
_add("metrics.refresh_btn", "刷新指标", "Refresh Metrics")
_add("metrics.refresh_hint", "指标从内存链路存储计算。生产环境每 5 秒自动刷新。", "Metrics computed from in-memory trace store. Auto-refresh every 5s in production.")
_add("metrics.calls", "次调用", "calls")

# ============================================================================
# Experiment Config
# ============================================================================
_add("experiment.title", "实验配置", "Experiment Config")
_add("experiment.caption", "灰度发布控制与 A/B 对比", "Canary rollout control and A/B comparison")
_add("experiment.active_header", "活跃实验", "Active Experiments")
_add("experiment.experiment_name", "实验名称", "Experiment")
_add("experiment.canary_pct", "灰度流量 %", "Canary Traffic %")
_add("experiment.status_active", "运行中", "ACTIVE")
_add("experiment.status_paused", "已暂停", "PAUSED")
_add("experiment.traffic_split", "流量分流", "Traffic Split")
_add("experiment.control_label", "对照组", "Control")
_add("experiment.canary_label", "灰度组", "Canary")
_add("experiment.variant_compare", "变体指标对比", "Variant Metrics Comparison")
_add("experiment.control_detail", "对照组 (生产)", "Control (Production)")
_add("experiment.canary_detail", "灰度组 (实验)", "Canary (Experiment)")
_add("experiment.no_control_data", "暂无对照组数据", "No control traces yet")
_add("experiment.no_canary_data", "暂无灰度组数据", "No canary traces yet")
_add("experiment.btn_expand", "扩大放量 (2x)", "Expand (2x)")
_add("experiment.btn_hold", "保持观望", "Hold")
_add("experiment.btn_rollback", "回滚 (0%)", "Rollback (0%)")
_add("experiment.rollout_history", "发布历史", "Rollout History")

# ============================================================================
# OTEL Traces
# ============================================================================
_add("otel.title", "分布式追踪", "OTEL Traces")
_add("otel.caption", "跨 Agent 阶段的分布式追踪", "Distributed tracing across agent phases")
_add("otel.waterfall", "追踪瀑布图 (OTEL 兼容格式)", "Trace Waterfall (OTEL-compatible format)")
_add("otel.trace_id", "链路 ID", "Trace ID")
_add("otel.experiment_group", "实验分组", "Experiment Group")
_add("otel.export_json", "OTEL 导出 (JSON)", "OTEL Export (JSON)")
_otel_jaeger_zh = (
    "要查看 Jaeger 中的追踪：启动本地 Jaeger 实例 "
    "`docker run -p 16686:16686 -p 4317:4317 jaegertracing/all-in-one:latest`。"
    "设置 `AGENTIC_RAG_OTEL_EXPORTER_ENDPOINT=http://localhost:4317`。"
)
_otel_jaeger_en = (
    "To view traces in Jaeger: start a local Jaeger instance with "
    "`docker run -p 16686:16686 -p 4317:4317 jaegertracing/all-in-one:latest`. "
    "Set `AGENTIC_RAG_OTEL_EXPORTER_ENDPOINT=http://localhost:4317` in your `.env`."
)
_add("otel.jaeger_hint", _otel_jaeger_zh, _otel_jaeger_en)

# ============================================================================
# Scenario Demo (app.py)
# ============================================================================
_add("demo.title", "Agentic RAG — 电商智能助手", "Agentic RAG — E-Commerce Assistant")
_add("demo.caption", "基于 Plan-and-Execute 架构的生产级 Agentic RAG 系统 · Dify + LlamaIndex · 面试演示版",
     "Production-grade Agentic RAG based on Plan-and-Execute · Dify + LlamaIndex · Interview Demo")
_add("demo.tools_registered", "工具数", "Tools")
_add("demo.scenarios_ready", "场景", "Scenarios")
_add("demo.quick_scenarios", "快速场景演示", "Quick Scenario Demo")
_add("demo.custom_dialog", "自定义对话", "Custom Dialog")
_add("demo.input_placeholder", "输入你的电商相关需求...", "Enter your e-commerce query...")
_add("demo.send_btn", "发送", "Send")
_add("demo.welcome_title", "这个 Demo 展示了什么？", "What does this demo show?")
_add("demo.welcome_1", "Agent 自主规划 — 将复杂任务分解为有序的工具调用步骤",
     "Autonomous Planning — Decomposes complex tasks into ordered tool-call steps")
_add("demo.welcome_2", "意图澄清 — 模糊需求自动触发追问机制",
     "Intent Clarification — Vague requests trigger follow-up questions automatically")
_add("demo.welcome_3", "并行工具执行 — 无依赖的工具同时运行，降低延迟",
     "Parallel Execution — Independent tools run concurrently to reduce latency")
_add("demo.welcome_4", "降级处理 — 工具失败时自动降级，不中断流程",
     "Degradation Handling — Failed tools degrade gracefully without aborting the flow")
_add("demo.welcome_5", "知识融合 — 多路信息源综合生成精准回答",
     "Knowledge Fusion — Multi-source synthesis for precise answers")
_add("demo.select_hint", "请选择一个演示场景，或在下方输入自定义需求开始体验。",
     "Select a demo scenario above, or type a custom query below to get started.")

# Scenario buttons
_add("scenario.festival_gear", "智能导购\n户外音乐节装备", "Smart Guide\nFestival Gear")
_add("scenario.negative_review", "差评监控\n评价分类", "Review Monitor\nNeg. Review Classify")
_add("scenario.womenswear", "个性化推荐\n职场女装推荐", "Personalized Rec\nWomenswear")
_add("scenario.competitor", "运营分析\n竞品定价分析", "Operations\nCompetitor Pricing")
_add("scenario.return_order", "供应链\n退货处理", "Supply Chain\nReturn Handling")

# Agent execution labels
_add("agent.plan_title", "Agent 执行计划", "Agent Execution Plan")
_add("agent.plan_detail", "查看完整计划详情", "View Full Plan Details")
_add("agent.state_machine", "状态机", "State Machine")
_add("agent.intent", "意图", "Intent")
_add("agent.clarity", "清晰度", "Clarity")
_add("agent.rewritten_query", "改写查询", "Rewritten Query")
_add("agent.synthesis_hint", "合成提示", "Synthesis Hint")
_add("agent.clarify_label", "Agent 追问", "Agent asks")
_add("agent.clarify_continue", "请在上方点击场景按钮重新进入，模拟用户回答追问后的完整流程",
     "Click a scenario button above to continue, simulating the user's response to the clarification")
_add("agent.tool_trace_title", "工具执行追踪", "Tool Execution Trace")
_add("agent.exec_trace_calls", "次调用", "calls")
_add("agent.final_answer", "最终回答", "Final Answer")
_add("agent.total_elapsed", "总耗时", "Total Duration")
_add("agent.tool_success_rate", "工具成功率", "Tool Success Rate")
_add("agent.plan_steps", "计划步数", "Plan Steps")

# Step labels
_add("step.no_deps", "无依赖", "no deps")
_add("step.deps_on", "依赖", "deps on")
_add("step.parallel_tag", "(并行)", "(PARALLEL)")
_add("step.view_detail", "查看详情", "View Details")
_add("step.summary", "摘要", "Summary")
_add("step.tool_label", "工具", "Tool")

# Status labels
_add("status.success", "成功", "success")
_add("status.degraded", "降级", "degraded")
_add("status.error", "错误", "error")
_add("status.timeout", "超时", "timeout")

# Phase names
_add("phase.planning", "规划中", "Planning")
_add("phase.validating", "校验中", "Validating")
_add("phase.executing", "执行中", "Executing")
_add("phase.reflecting", "反思中", "Reflecting")
_add("phase.synthesizing", "合成中", "Synthesizing")
_add("phase.clarifying", "澄清中", "Clarifying")
_add("phase.done", "完成", "Done")

# System architecture sidebar
_add("arch.title", "系统架构", "System Architecture")
_add("arch.state_machine_title", "Agent 状态机", "Agent State Machine")
_add("arch.tools_title", "已注册工具", "Registered Tools")
_add("arch.tools.rag_search", "知识库检索", "Knowledge Base Search")
_add("arch.tools.rag_chat", "多轮对话检索", "Multi-turn Chat Search")
_add("arch.tools.web_search", "外部网络搜索", "External Web Search")
_add("arch.tools.user_profile", "用户画像", "User Profile")
_add("arch.tools.inventory", "实时库存", "Real-time Inventory")
_add("arch.tools.kg", "KG 多跳推理", "KG Multi-hop Reasoning")
_add("arch.tools.price", "竞品分析", "Competitor Analysis")
_add("arch.tools.order", "订单查询", "Order Lookup")
_add("arch.tools.logistics", "物流追踪", "Logistics Tracking")
_add("arch.tools.crm", "退货工单", "CRM Return")
_add("arch.degradation_title", "降级策略", "Degradation Policies")
_add("arch.degradation.fail_fast", "直接报错，让 Reflector 处理", "Fail immediately, let Reflector handle")
_add("arch.degradation.return_cached", "返回上次缓存结果", "Return last cached result")
_add("arch.degradation.skip", "跳过，标注降级", "Skip with degradation note")
_add("arch.degradation.inform_user", "告知用户该功能不可用", "Tell user this feature is unavailable")
_add("arch.degradation.retry", "指数退避重试 3 次", "Exponential backoff retry 3x")

# ============================================================================
# Language selector
# ============================================================================
_add("lang.selector", "语言 / Language", "Language / 语言")
_add("lang.zh", "中文", "Chinese")
_add("lang.en", "English", "English")

# ============================================================================
# Helper
# ============================================================================

_LANG: str = "zh"


def set_lang(lang: str) -> None:
    global _LANG
    if lang in ("zh", "en"):
        _LANG = lang


def get_lang() -> str:
    return _LANG


def t(key: str, lang: str | None = None) -> str:
    """Look up a translation key. Falls back to key if not found."""
    l = lang or _LANG
    table = ZH if l == "zh" else EN
    return table.get(key, key)


def init_lang_from_session():
    """Read language preference from Streamlit session state."""
    if "lang" not in st.session_state:
        st.session_state.lang = "zh"
    set_lang(st.session_state.lang)


def lang_toggle():
    """Render a language toggle in the sidebar. Returns current lang."""
    init_lang_from_session()

    def on_change():
        st.session_state.lang = st.session_state._lang_selector
        set_lang(st.session_state._lang_selector)

    st.selectbox(
        t("lang.selector"),
        options=["zh", "en"],
        format_func=lambda x: "中文" if x == "zh" else "English",
        key="_lang_selector",
        on_change=on_change,
        index=0 if st.session_state.lang == "zh" else 1,
    )
    return st.session_state.lang
