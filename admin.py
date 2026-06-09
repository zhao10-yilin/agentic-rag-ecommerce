"""Agentic RAG Admin Panel — Streamlit-based operations dashboard. Chinese/English toggle.

Run::

    streamlit run admin.py
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
from agentic_rag.i18n import t, init_lang_from_session, lang_toggle

st.set_page_config(
    page_title="Agentic RAG Admin",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 10px; padding: 16px; color: white;
        text-align: center; font-weight: 600;
    }
    .metric-card.green { background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%); color: #1a1a2e; }
    .metric-card.red { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }
    .metric-card.orange { background: linear-gradient(135deg, #fa8231 0%, #febd69 100%); color: #1a1a2e; }
    .phase-bar { display: inline-block; height: 24px; border-radius: 4px; margin: 2px; line-height: 24px; padding: 0 8px; font-size: 11px; font-weight: 600; color: white; }
    .phase-planning { background: #1565c0; } .phase-executing { background: #2e7d32; }
    .phase-reflecting { background: #e65100; } .phase-synthesizing { background: #283593; }
    .phase-clarifying { background: #c62828; }
    .tool-ok { color: #2e7d32; font-weight: 600; }
    .tool-degraded { color: #e65100; font-weight: 600; }
    .tool-error { color: #c62828; font-weight: 600; }
    .alert-banner { background: #fff3cd; border: 2px solid #ffc107; border-radius: 8px; padding: 12px; margin: 8px 0; font-weight: 600; }
    .alert-critical { background: #f8d7da; border: 2px solid #dc3545; border-radius: 8px; padding: 12px; margin: 8px 0; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar navigation + language toggle
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🛒 " + t("nav.sidebar_title"))
    st.caption(t("nav.sidebar_caption"))
    st.markdown("---")

    # Language toggle at the top
    lang_toggle()

    st.markdown("---")
    page = st.radio(
        "Navigation",
        [t("nav.trace_viewer"), t("nav.metrics_dashboard"),
         t("nav.experiment_config"), t("nav.otel_traces")],
        label_visibility="collapsed",
    )
    st.markdown("---")

# Sync lang from session
init_lang_from_session()

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _get_trace_store():
    from agentic_rag.observability.trace_store import get_trace_store
    return get_trace_store()


def _seed_demo_traces():
    store = _get_trace_store()
    if store.count > 0:
        return
    import random
    from agentic_rag.models import (AgentAction, AgentPlan, AgentStep,
                                      ToolCallRecord, ToolResult)

    scenarios = [
        ("帮我推荐一台半自动咖啡机", "recommendation", 3, 2),
        ("我要去户外音乐节需要什么装备", "shopping_guide", 5, 1),
        ("订单ORD-001退货处理", "supply_chain", 4, 0),
        ("德龙EC685竞品分析", "operations", 2, 0),
        ("户外露营帐篷推荐 轻量", "shopping_guide", 3, 1),
        ("Breville和德龙哪个更适合拉花", "recommendation", 3, 0),
        ("查询物流状态 SF-2026-0515", "supply_chain", 2, 0),
        ("帮我分析这周咖啡机销量", "operations", 4, 1),
    ]
    tools_pool = ["rag_search", "web_search", "inventory_check", "user_profile",
                  "knowledge_graph", "price_analysis", "order_lookup", "crm_create_return"]
    status_weights = ["success"] * 8 + ["degraded"] * 1 + ["error"] * 1

    for msg, intent, steps_n, refl in scenarios:
        trace_id = store.start_trace(
            msg, experiment_group=random.choice(["control", "canary_v2"]))
        t0 = time.time()
        phases = [
            ("planning", 800 + random.randint(-200, 400)),
        ]
        if random.random() < 0.3:
            phases.append(("clarifying", 200 + random.randint(-50, 200)))
        for s in range(steps_n):
            phases.append((f"executing_step_{s}", 300 + random.randint(-100, 500)))
        phases.append(("reflecting", 600 + random.randint(-200, 300)))
        phases.append(("synthesizing", 1000 + random.randint(-300, 600)))
        t = t0
        for phase_name, duration_ms in phases:
            store.add_phase(trace_id, phase_name, t, t + duration_ms / 1000)
            t += duration_ms / 1000

        plan = AgentPlan(
            original_query=msg, intent=intent,
            steps=[
                AgentStep(step_index=i, description=f"Step {i}",
                          actions=[AgentAction(tool_name=random.choice(tools_pool), input={}, reason="demo")],
                          depends_on=[i - 1] if i > 0 else [])
                for i in range(steps_n)
            ],
        )
        records = []
        for s in range(steps_n):
            tn = random.choice(tools_pool)
            sts = random.choice(status_weights)
            records.append(ToolCallRecord(
                tool_name=tn, input={},
                result=ToolResult(tool_name=tn, status=sts,
                    summary=f"Demo {tn}" if sts == "success" else f"Demo issue for {tn}",
                    elapsed_ms=random.randint(50, 800)),
                step_index=s, idempotency_key=uuid.uuid4().hex[:12],
            ))
        store.complete_trace(trace_id, plan=plan, records=records,
                             final_state="done", reflection_rounds=refl)


# ============================================================================
# Page 1: Trace Viewer
# ============================================================================


def render_trace_viewer():
    st.title(t("trace.title"))
    st.caption(t("trace.caption"))
    _seed_demo_traces()
    store = _get_trace_store()
    traces = store.get_recent(20)

    if not traces:
        st.info(t("trace.no_traces"))
        return

    trace_options = {
        f"[{t.trace_id[:8]}] {t.user_message[:60]} ({t.duration_ms:.0f}ms)": t
        for t in traces
    }
    selected_label = st.selectbox(t("nav.select_trace"), list(trace_options.keys()))
    trace = trace_options[selected_label]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(t("trace.duration"), f"{trace.duration_ms:.0f}ms")
    with col2:
        st.metric(t("trace.phases"), str(len(trace.phases)))
    with col3:
        st.metric(t("trace.tool_calls"), str(len(trace.tool_records)))
    with col4:
        st.metric(t("trace.reflections"), str(trace.reflection_rounds))

    st.markdown(f"### {t('trace.phase_timeline')}")
    if trace.phases:
        total_duration = trace.duration_ms or 1
        colors = {"planning": "#1565c0", "validating": "#7b1fa2", "executing": "#2e7d32",
                   "reflecting": "#e65100", "synthesizing": "#283593", "clarifying": "#c62828"}
        gantt_html = '<div style="font-family:monospace; font-size:12px;">'
        for p in trace.phases:
            phase_type = p.phase.split("_")[0]
            color = colors.get(phase_type, "#607d8b")
            pct = (p.duration_ms / total_duration) * 100
            gantt_html += (
                f'<div style="margin:3px 0;">'
                f'<span style="display:inline-block;width:140px;">{p.phase}</span>'
                f'<span style="display:inline-block;background:{color};height:18px;'
                f'border-radius:3px;width:{max(pct, 2)}%;min-width:50px;'
                f'line-height:18px;padding-left:6px;color:white;font-size:11px;">'
                f'{p.duration_ms:.0f}ms</span></div>'
            )
        gantt_html += '</div>'
        st.markdown(gantt_html, unsafe_allow_html=True)

    if trace.plan and trace.plan.steps:
        st.markdown(f"### {t('trace.step_dep_graph')}")
        for step in trace.plan.steps:
            deps_str = f"depends_on={step.depends_on}" if step.depends_on else t("step.no_deps")
            actions_str = ", ".join(a.tool_name for a in step.actions)
            st.code(f"Step {step.step_index} [{deps_str}]\n  -> {actions_str}\n  -> {step.description}")

    if trace.tool_records:
        st.markdown(f"### {t('trace.tool_exec_details')}")
        cols = st.columns(min(len(trace.tool_records), 4))
        for i, record in enumerate(trace.tool_records):
            with cols[i % len(cols)]:
                status_class = f"tool-{record.result.status}"
                st.markdown(f"""
                <div style="border:1px solid #e0e0e0; border-radius:8px; padding:10px; margin:4px 0;">
                    <b>{record.tool_name}</b><br>
                    <span class="{status_class}">{record.result.status}</span><br>
                    <small>{record.result.elapsed_ms:.0f}ms</small><br>
                    <small>Step {record.step_index}</small>
                </div>
                """, unsafe_allow_html=True)
                if record.result.error:
                    st.caption(f"{t('trace.error')}: {record.result.error[:100]}")


# ============================================================================
# Page 2: Metrics Dashboard
# ============================================================================


def render_metrics_dashboard():
    st.title(t("metrics.title"))
    st.caption(t("metrics.caption"))
    _seed_demo_traces()
    store = _get_trace_store()
    metrics = store.get_metrics_snapshot()

    if not metrics:
        st.info(t("metrics.no_data"))
        return

    st.markdown(f"### {t('metrics.kpi_header')}")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        sr = metrics.get("success_rate", 0)
        cc = "green" if sr > 0.95 else ("orange" if sr > 0.85 else "red")
        st.markdown(f'<div class="metric-card {cc}">{t("metrics.success_rate")}<br><span style="font-size:28px;">{sr:.1%}</span></div>', unsafe_allow_html=True)
    with col2:
        er = metrics.get("error_rate", 0)
        cc = "green" if er < 0.05 else ("orange" if er < 0.10 else "red")
        st.markdown(f'<div class="metric-card {cc}">{t("metrics.error_rate")}<br><span style="font-size:28px;">{er:.1%}</span></div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="metric-card">{t("metrics.p50_latency")}<br><span style="font-size:28px;">{metrics.get("p50_ms", 0):.0f}ms</span></div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f'<div class="metric-card">{t("metrics.p99_latency")}<br><span style="font-size:28px;">{metrics.get("p99_ms", 0):.0f}ms</span></div>', unsafe_allow_html=True)

    st.markdown(f"### {t('metrics.funnel_header')}")
    total = metrics.get("total_traces", 1)
    success = int(metrics.get("success_rate", 0) * total)
    clarification = int(metrics.get("clarification_rate", 0) * total)
    errors = int(metrics.get("error_rate", 0) * total)
    funnel_data = [
        (t("metrics.impressions"), total, "#667eea"),
        (t("metrics.clarifications"), clarification, "#fa8231"),
        (t("metrics.successful"), success, "#43e97b"),
        (t("metrics.errors"), errors, "#f5576c"),
    ]
    funnel_html = ""
    for label, count, color in funnel_data:
        pct = (count / max(total, 1)) * 100
        funnel_html += (
            f'<div style="margin:6px 0;">'
            f'<span style="display:inline-block;width:120px;">{label}</span>'
            f'<span style="display:inline-block;background:{color};height:22px;'
            f'border-radius:4px;width:{max(pct, 3)}%;min-width:40px;'
            f'line-height:22px;padding-left:8px;color:white;font-size:12px;">'
            f'{count} ({pct:.0f}%)</span></div>'
        )
    st.markdown(funnel_html, unsafe_allow_html=True)

    st.markdown(f"### {t('metrics.tool_success_header')}")
    tool_stats = metrics.get("tool_stats", {})
    if tool_stats:
        for name, stats in sorted(tool_stats.items()):
            sr_val = stats.get("success_rate", 0)
            color = "#43e97b" if sr_val > 0.9 else ("#fa8231" if sr_val > 0.7 else "#f5576c")
            total_calls = sum(v for k, v in stats.items() if k != "success_rate")
            st.markdown(
                f'**{name}** '
                f'<span style="color:{color}; font-weight:600;">{sr_val:.1%}</span> '
                f'({total_calls} {t("metrics.calls")})',
                unsafe_allow_html=True,
            )

    st.markdown("---")
    if st.button(t("metrics.refresh_btn"), use_container_width=True):
        st.rerun()
    st.caption(t("metrics.refresh_hint"))


# ============================================================================
# Page 3: Experiment Config
# ============================================================================


def render_experiment_config():
    st.title(t("experiment.title"))
    st.caption(t("experiment.caption"))
    _seed_demo_traces()
    store = _get_trace_store()

    for key in ["canary_pct", "experiment_name", "rollout_history"]:
        if key not in st.session_state:
            st.session_state[key] = {"canary_pct": 5.0, "experiment_name": "planner_prompt_v2", "rollout_history": []}[key]

    st.markdown(f"### {t('experiment.active_header')}")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(t("experiment.experiment_name"), st.session_state.experiment_name)
    with col2:
        new_pct = st.slider(t("experiment.canary_pct"), 0.0, 100.0, st.session_state.canary_pct, 5.0)
        if new_pct != st.session_state.canary_pct:
            st.session_state.canary_pct = new_pct
    with col3:
        status = t("experiment.status_active") if st.session_state.canary_pct > 0 else t("experiment.status_paused")
        color = "green" if st.session_state.canary_pct > 0 else "red"
        st.markdown(
            f'<div style="padding:16px; text-align:center; font-size:24px; font-weight:700; '
            f'color:{"#2e7d32" if color == "green" else "#c62828"};">{status}</div>',
            unsafe_allow_html=True,
        )

    st.markdown(f"### {t('experiment.traffic_split')}")
    cp = 100 - st.session_state.canary_pct
    st.markdown(f"""
    <div style="display:flex; height:40px; border-radius:8px; overflow:hidden; margin:10px 0;">
        <div style="width:{cp}%; background:#667eea; line-height:40px; text-align:center; color:white; font-weight:600;">
            {t('experiment.control_label')} ({cp:.0f}%)
        </div>
        <div style="width:{st.session_state.canary_pct}%; background:#43e97b; line-height:40px; text-align:center; color:#1a1a2e; font-weight:600;">
            {t('experiment.canary_label')} ({st.session_state.canary_pct:.0f}%)
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"### {t('experiment.variant_compare')}")
    traces = store.get_recent(100)
    ct_traces = [t for t in traces if t.experiment_group == "control"]
    cy_traces = [t for t in traces if "canary" in t.experiment_group]

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**{t('experiment.control_detail')}**")
        if ct_traces:
            st.metric(t("metrics.avg_duration"), f"{sum(t.duration_ms for t in ct_traces) / len(ct_traces):.0f}ms")
            st.metric(t("metrics.success_rate"), f"{sum(1 for t in ct_traces if t.final_state == 'done') / len(ct_traces):.1%}")
            st.metric(t("metrics.sample_size"), str(len(ct_traces)))
        else:
            st.caption(t("experiment.no_control_data"))
    with col2:
        st.markdown(f"**{t('experiment.canary_detail')}**")
        if cy_traces:
            avg_dur = sum(t.duration_ms for t in cy_traces) / len(cy_traces)
            succ = sum(1 for t in cy_traces if t.final_state == "done") / len(cy_traces)
            c_succ = (sum(1 for t in ct_traces if t.final_state == "done") / len(ct_traces)) if ct_traces else 0
            st.metric(t("metrics.avg_duration"), f"{avg_dur:.0f}ms")
            st.metric(t("metrics.success_rate"), f"{succ:.1%}", delta=f"{(succ - c_succ) * 100:+.1f}%")
            st.metric(t("metrics.sample_size"), str(len(cy_traces)))
        else:
            st.caption(t("experiment.no_canary_data"))

    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button(t("experiment.btn_expand"), use_container_width=True, type="primary"):
            st.session_state.canary_pct = min(st.session_state.canary_pct * 2, 50)
            st.session_state.rollout_history.append(f"EXPAND to {st.session_state.canary_pct:.0f}%")
            st.rerun()
    with col2:
        if st.button(t("experiment.btn_hold"), use_container_width=True):
            st.session_state.rollout_history.append(f"HOLD at {st.session_state.canary_pct:.0f}%")
    with col3:
        if st.button(t("experiment.btn_rollback"), use_container_width=True, type="secondary"):
            st.session_state.canary_pct = 0.0
            st.session_state.rollout_history.append("ROLLBACK to 0%")
            st.rerun()

    if st.session_state.rollout_history:
        st.markdown(f"### {t('experiment.rollout_history')}")
        for entry in reversed(st.session_state.rollout_history[-10:]):
            st.caption(f"- {entry}")


# ============================================================================
# Page 4: OTEL Traces
# ============================================================================


def render_otel_traces():
    st.title(t("otel.title"))
    st.caption(t("otel.caption"))
    _seed_demo_traces()
    store = _get_trace_store()
    traces = store.get_recent(10)

    if not traces:
        st.info(t("trace.no_traces"))
        return

    st.markdown(f"### {t('otel.waterfall')}")
    for trace_obj in traces[:5]:
        with st.expander(f"Trace {trace_obj.trace_id[:8]} — {trace_obj.user_message[:60]} ({trace_obj.duration_ms:.0f}ms)"):
            st.markdown(f"**{t('otel.trace_id')}**: `{trace_obj.trace_id}`")
            st.markdown(f"**{t('otel.experiment_group')}**: `{trace_obj.experiment_group}`")

            if trace_obj.phases:
                min_t = trace_obj.phases[0].started_at
                waterfall = '<div style="font-family:monospace; font-size:11px; position:relative;">'
                cmap = {"planning": "#1565c0", "executing": "#2e7d32", "reflecting": "#e65100",
                         "synthesizing": "#283593", "clarifying": "#c62828"}
                for p in trace_obj.phases:
                    offset_ms = (p.started_at - min_t) * 1000
                    width_ms = p.duration_ms
                    color = cmap.get(p.phase.split("_")[0], "#607d8b")
                    waterfall += (
                        f'<div style="margin:2px 0;">'
                        f'<span style="display:inline-block;width:160px;">{p.phase}</span>'
                        f'<span style="display:inline-block;margin-left:{offset_ms/10:.0f}px;'
                        f'background:{color};height:16px;width:{max(width_ms/10, 20):.0f}px;'
                        f'border-radius:3px;line-height:16px;padding-left:4px;color:white;font-size:10px;">'
                        f'{p.duration_ms:.0f}ms</span></div>'
                    )
                waterfall += '</div>'
                st.markdown(waterfall, unsafe_allow_html=True)

            st.markdown(f"**{t('otel.export_json')}**")
            st.json({
                "traceId": trace_obj.trace_id,
                "spans": [
                    {"spanId": uuid.uuid4().hex[:16], "parentSpanId": None if i == 0 else "root",
                     "name": p.phase, "startTimeUnixNano": int(p.started_at * 1e9),
                     "endTimeUnixNano": int(p.ended_at * 1e9), "attributes": p.metadata}
                    for i, p in enumerate(trace_obj.phases)
                ],
            })

    st.markdown("---")
    st.info(t("otel.jaeger_hint"))


# ============================================================================
# Router
# ============================================================================


PAGE_MAP = {
    "Trace Viewer": render_trace_viewer,
    "链路追踪": render_trace_viewer,
    "Metrics Dashboard": render_metrics_dashboard,
    "指标看板": render_metrics_dashboard,
    "Experiment Config": render_experiment_config,
    "实验配置": render_experiment_config,
    "OTEL Traces": render_otel_traces,
    "分布式追踪": render_otel_traces,
}

if __name__ == "__main__":
    render_fn = PAGE_MAP.get(page, render_trace_viewer)
    render_fn()
