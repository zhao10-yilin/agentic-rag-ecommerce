"""Agentic RAG for E-Commerce — Interactive Demo with Chinese/English toggle.

Streamlit application for demonstrating the Plan-and-Execute Agentic RAG
system across four e-commerce scenarios.

Run::

    streamlit run app.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
from agentic_rag.i18n import t, init_lang_from_session, lang_toggle

st.set_page_config(
    page_title="Agentic RAG Demo",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    .agent-phase { padding: 8px 12px; border-radius: 6px; margin: 4px 0; font-weight: 600; font-size: 14px; }
    .phase-planning { background: #e3f2fd; color: #1565c0; border-left: 4px solid #1565c0; }
    .phase-validating { background: #f3e5f5; color: #7b1fa2; border-left: 4px solid #7b1fa2; }
    .phase-executing { background: #e8f5e9; color: #2e7d32; border-left: 4px solid #2e7d32; }
    .phase-reflecting { background: #fff3e0; color: #e65100; border-left: 4px solid #e65100; }
    .phase-synthesizing { background: #e8eaf6; color: #283593; border-left: 4px solid #283593; }
    .phase-clarifying { background: #fce4ec; color: #c62828; border-left: 4px solid #c62828; }
    .tool-success { color: #2e7d32; } .tool-degraded { color: #e65100; } .tool-error { color: #c62828; }
    .tool-card { border: 1px solid #e0e0e0; border-radius: 8px; padding: 10px; margin: 6px 0; background: #fafafa; }
    .answer-block { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar — Architecture Overview + Language Toggle
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("---")

    # Language toggle at the top
    current_lang = lang_toggle()

    st.markdown("---")
    st.markdown(f"### {t('arch.title')}")
    st.markdown("""
    ```
    ┌─────────────────────────────────┐
    │        Streamlit UI             │
    ├─────────────────────────────────┤
    │    Plan-and-Execute Agent       │
    │  ┌──────┐  ┌──────┐  ┌───────┐ │
    │  │Plan  │→│Valid.│→│Execute│ │
    │  └──────┘  └──────┘  └───┬───┘ │
    │       ↑          ↓       │     │
    │       └─Reflect──┘       │     │
    ├──────────────────────────┼─────┤
    │       Tool Layer          │     │
    │  RAG | Web | KG | Inv | CRM    │
    ├─────────────────────────────────┤
    │  pdf_parser.rag (existing)     │
    │  ChromaDB | FTS5 | LLM Gateway │
    └─────────────────────────────────┘
    ```
    """)

    st.markdown("---")
    st.markdown(f"### {t('arch.state_machine_title')}")
    st.markdown("""
    ```
    CLARIFYING → PLANNING → VALIDATING
                     ↑           ↓
              (replan)      EXECUTING
                     ↑           ↓
              ┌──────┘     REFLECTING
              │                ↓
              └──── REJECT ───┘
                           SYNTHESIZING
                                ↓
                              DONE
    ```
    """)

    st.markdown("---")
    st.markdown(f"### {t('arch.tools_title')}")
    tools_list = [
        ("rag_search", "arch.tools.rag_search"),
        ("rag_chat", "arch.tools.rag_chat"),
        ("web_search", "arch.tools.web_search"),
        ("user_profile", "arch.tools.user_profile"),
        ("inventory_check", "arch.tools.inventory"),
        ("knowledge_graph", "arch.tools.kg"),
        ("price_analysis", "arch.tools.price"),
        ("order_lookup", "arch.tools.order"),
        ("logistics_track", "arch.tools.logistics"),
        ("crm_create_return", "arch.tools.crm"),
    ]
    for name, i18n_key in tools_list:
        st.markdown(f"  {name} — {t(i18n_key)}")

    st.markdown("---")
    st.markdown(f"### {t('arch.degradation_title')}")
    st.markdown(f"- `FAIL_FAST` — {t('arch.degradation.fail_fast')}")
    st.markdown(f"- `RETURN_CACHED` — {t('arch.degradation.return_cached')}")
    st.markdown(f"- `SKIP` — {t('arch.degradation.skip')}")
    st.markdown(f"- `INFORM_USER` — {t('arch.degradation.inform_user')}")
    st.markdown(f"- `RETRY_WITH_BACKOFF` — {t('arch.degradation.retry')}")

init_lang_from_session()

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    st.title(t("demo.title"))
    st.caption(t("demo.caption"))
with col2:
    st.metric(t("demo.tools_registered"), "10", "registered")
with col3:
    st.metric(t("demo.scenarios_ready"), "4", "pre-loaded")

st.markdown("---")

# ---------------------------------------------------------------------------
# Scenario quick-select
# ---------------------------------------------------------------------------

st.markdown(f"### {t('demo.quick_scenarios')}")

scenario_cols = st.columns(4)
with scenario_cols[0]:
    if st.button(t("scenario.negative_review"), use_container_width=True, key="sc1"):
        st.session_state.demo_scenario = "negative_review"
        st.session_state.demo_stage = "start"
        st.session_state.user_msg = "差评监控"
        st.session_state.clarify_round = 0
        st.session_state.last_clarify_response = None
        st.session_state.pop("custom_input", None)
        st.rerun()

with scenario_cols[1]:
    if st.button(t("scenario.womenswear"), use_container_width=True, key="sc2"):
        st.session_state.demo_scenario = "womenswear"
        st.session_state.demo_stage = "start"
        st.session_state.user_msg = "我想买一件适合职场穿的百搭女装外套"
        st.session_state.clarify_round = 0
        st.session_state.last_clarify_response = None
        st.session_state.pop("custom_input", None)
        st.rerun()

with scenario_cols[2]:
    if st.button(t("scenario.competitor"), use_container_width=True, key="sc3"):
        st.session_state.demo_scenario = "competitor_analysis"
        st.session_state.demo_stage = "one_shot"
        st.session_state.user_msg = "帮我分析一下 Theory 西装外套在市场上的定价情况，看看我们需要调整吗"
        st.session_state.clarify_round = 0
        st.session_state.last_clarify_response = None
        st.session_state.pop("custom_input", None)
        st.rerun()

with scenario_cols[3]:
    if st.button(t("scenario.return_order"), use_container_width=True, key="sc4"):
        st.session_state.demo_scenario = "return_order"
        st.session_state.demo_stage = "start"
        st.session_state.user_msg = "我的订单 #ORD-20260501-001 收到的大衣尺码不合适，我要退货"
        st.session_state.clarify_round = 0
        st.session_state.last_clarify_response = None
        st.session_state.pop("custom_input", None)
        st.rerun()

# ---------------------------------------------------------------------------
# Custom input
# ---------------------------------------------------------------------------

st.markdown("---")
st.markdown(f"### {t('demo.custom_dialog')}")

with st.container():
    ci, cb = st.columns([4, 1])
    with ci:
        custom_msg = st.text_input(
            t("demo.input_placeholder"),
            placeholder=t("demo.input_placeholder"),
            key="custom_input",
        )
    with cb:
        if st.button(t("demo.send_btn"), use_container_width=True, type="primary"):
            if custom_msg:
                st.session_state.demo_scenario = "custom"
                st.session_state.demo_stage = "start"
                st.session_state.user_msg = custom_msg
                st.session_state.clarify_round = 0
                st.session_state.last_clarify_response = None
                # Continue existing conversation (don't reset history)
                st.rerun()

st.markdown("---")

# ---------------------------------------------------------------------------
# Demo runner
# ---------------------------------------------------------------------------


def run_demo():
    from agentic_rag.demo.simulator import DemoSimulator

    if "demo_scenario" not in st.session_state:
        st.info(t("demo.select_hint"))
        st.markdown(f"""
        ### {t('demo.welcome_title')}
        1. {t('demo.welcome_1')}
        2. {t('demo.welcome_2')}
        3. {t('demo.welcome_3')}
        4. {t('demo.welcome_4')}
        5. {t('demo.welcome_5')}
        """)
        return

    scenario = st.session_state.demo_scenario
    stage = st.session_state.get("demo_stage", "start")
    user_msg = st.session_state.get("user_msg", "")
    simulator = DemoSimulator()

    # Ensure session state is initialized
    if "clarify_round" not in st.session_state:
        st.session_state.clarify_round = 0
    if "last_clarify_response" not in st.session_state:
        st.session_state.last_clarify_response = None
    if "conversation_history" not in st.session_state:
        st.session_state.conversation_history = []
    if "conversation_round" not in st.session_state:
        st.session_state.conversation_round = 0

    # --- Handle pending clarification response ---
    if stage == "start" and st.session_state.get("last_clarify_response"):
        clarify_resp = st.session_state.last_clarify_response
        round_num = st.session_state.get("clarify_round", 1)

        _render_user_message(clarify_resp)
        with st.spinner("..."):
            time.sleep(0.8)

        if scenario == "festival_gear":
            response = asyncio.run(simulator.simulate_festival_gear(
                user_msg, clarifying_response=clarify_resp,
                _clarification_round=round_num,
            ))
        elif scenario == "womenswear":
            response = asyncio.run(simulator.simulate_womenswear(
                user_msg, clarifying_response=clarify_resp,
                _clarification_round=round_num,
            ))
        elif scenario == "return_order":
            response = asyncio.run(simulator.simulate_return_order(
                user_msg, clarifying_response=clarify_resp,
                _clarification_round=round_num,
            ))
        elif scenario == "negative_review":
            response = asyncio.run(simulator.simulate_negative_review(
                review_text=clarify_resp,
            ))
        else:
            response = _handle_custom(clarify_resp)

        # Clear pending
        st.session_state.last_clarify_response = None

        # If STILL clarifying and not yet at max rounds -> show another question
        if response.clarifying_question and round_num < 2:
            _render_clarification_ui(response, round_num + 1)
            return

        # If at max rounds OR got a real answer -> render full result
        _render_full_result(response)
        st.session_state.clarify_round = 0  # Reset for next scenario
        return

    if stage == "start":
        _render_user_message(user_msg)
        with st.spinner("..."):
            time.sleep(0.5)

        if scenario == "festival_gear":
            response = asyncio.run(simulator.simulate_festival_gear(user_msg))
        elif scenario == "womenswear":
            response = asyncio.run(simulator.simulate_womenswear(user_msg))
        elif scenario == "competitor_analysis":
            response = asyncio.run(simulator.simulate_competitor_analysis(user_msg))
        elif scenario == "return_order":
            response = asyncio.run(simulator.simulate_return_order(user_msg))
        elif scenario == "negative_review":
            response = asyncio.run(simulator.simulate_negative_review())
        else:
            # Custom multi-turn conversation
            history = st.session_state.conversation_history
            c_round = st.session_state.conversation_round
            # Add user message to history (avoid duplicates on rerun)
            if not history or history[-1]["content"] != user_msg:
                history.append({"role": "user", "content": user_msg})
                # Trim to 20 user turns (40 messages)
                if len(history) > 40:
                    history = history[-40:]
                st.session_state.conversation_history = history
            response = _handle_custom(user_msg, history=history, round_num=c_round)
            # Add assistant response to history
            if response.answer:
                history.append({"role": "assistant", "content": response.answer[:500]})
            elif response.clarifying_question:
                history.append({"role": "assistant", "content": response.clarifying_question})
            st.session_state.conversation_history = history[-40:]
            st.session_state.conversation_round = c_round + 1

        if response.plan and response.plan.steps:
            st.markdown("---")
            st.markdown(f"### {t('agent.plan_title')}")
            with st.expander(t("agent.plan_detail"), expanded=True):
                _render_plan(response.plan)

        if response.clarifying_question:
            round_num = st.session_state.get("clarify_round", 0) + 1
            _render_clarification_ui(response, round_num)
            return

        _render_full_result(response)

        # Show conversation history for custom conversations
        if scenario == "custom" and st.session_state.conversation_history:
            st.markdown("---")
            st.markdown(f"### Conversation ({len([m for m in st.session_state.conversation_history if m['role']=='user'])} turns, max 20)")

            # Continue conversation input
            st.markdown(f"**{t('demo.custom_dialog')}**")
            cc1, cc2, cc3 = st.columns([3, 1, 1])
            with cc1:
                follow_up = st.text_input(
                    "Continue the conversation..." if st.session_state.get("lang", "zh") == "en" else "继续对话...",
                    placeholder="继续提问，如'有没有更便宜的'、'对比一下前两个'",
                    key="conversation_continue",
                    label_visibility="collapsed",
                )
            with cc2:
                if st.button("Send", key="conv_send", use_container_width=True):
                    if follow_up and follow_up.strip():
                        st.session_state.demo_scenario = "custom"
                        st.session_state.demo_stage = "start"
                        st.session_state.user_msg = follow_up.strip()
                        st.rerun()
            with cc3:
                if st.button("New Chat", key="conv_reset", use_container_width=True):
                    st.session_state.conversation_history = []
                    st.session_state.conversation_round = 0
                    st.rerun()

            # Show history
            for msg in st.session_state.conversation_history[-12:]:  # Last 6 exchanges
                role_label = "You" if msg["role"] == "user" else "Agent"
                if msg["role"] == "user":
                    st.chat_message("user").markdown(msg["content"][:300])
                else:
                    st.chat_message("assistant").markdown(msg["content"][:500])

    elif stage == "one_shot":
        _render_user_message(user_msg)
        with st.spinner("..."):
            time.sleep(0.5)
        response = asyncio.run(simulator.simulate_competitor_analysis(user_msg))
        _render_full_result(response)


def _handle_custom(user_msg: str, history: list | None = None, round_num: int = 0):
    """Handle custom input with multi-turn conversation and memory (max 20 turns)."""
    from agentic_rag.demo.simulator import DemoSimulator
    simulator = DemoSimulator()
    return asyncio.run(simulator.simulate_conversation(
        user_msg, history=history, round_num=round_num,
    ))


def _render_user_message(msg: str):
    st.markdown("---")
    st.chat_message("user").markdown(f"**User**: {msg}")


def _render_plan(plan):
    phases = ["clarifying", "planning", "validating", "executing", "reflecting", "synthesizing", "done"]
    current = "executing"
    phtml = " → ".join(
        f"<span style='color:{'#4caf50' if p == current else '#9e9e9e'}; font-weight:{'bold' if p == current else 'normal'}'>"
        f"{'[OK] ' if phases.index(p) < phases.index(current) else ''}{t('phase.' + p)}</span>"
        for p in phases[:5])
    st.markdown(f"**{t('agent.state_machine')}**: {phtml} → ...", unsafe_allow_html=True)
    st.markdown(f"**{t('agent.intent')}**: `{plan.intent}` | **{t('agent.clarity')}**: `{plan.intent_clarity:.0%}` | **{t('agent.rewritten_query')}**: _{plan.rewritten_query}_")
    if plan.final_synthesis_hint:
        st.caption(f"[...] {t('agent.synthesis_hint')}: {plan.final_synthesis_hint}")

    for step in plan.steps:
        dep_tag = ""
        if step.depends_on:
            dep_tag = f" ({t('step.deps_on')} {step.depends_on})"
        else:
            dep_tag = f" ({t('step.no_deps')})"
        st.markdown(f"""
        <div style="border:1px solid #e0e0e0; border-radius:8px; padding:10px; margin:8px 0; background:#fafafa;">
            <b>Step {step.step_index + 1}{dep_tag}</b>: {step.description}<br>
            <small>{t('step.tool_label')}: {', '.join(a.tool_name for a in step.actions)}</small>
        </div>
        """, unsafe_allow_html=True)


def _render_step_execution(step_idx: int, desc: str, records: list):
    parallel_tag = f" {t('step.parallel_tag')}" if len(records) > 1 else ""
    with st.expander(f"Step {step_idx + 1}: {desc}{parallel_tag} ({len(records)} calls)", expanded=(step_idx <= 1)):
        cols = st.columns(len(records))
        for i, r in enumerate(records):
            with cols[i]:
                icon_map = {"success": "[OK]", "degraded": "[WARN]", "error": "[FAIL]", "timeout": "[TIMEOUT]"}
                icon = icon_map.get(r.result.status, "[?]")
                st.markdown(f"""
                <div class="tool-card">
                    <b>{icon} {r.tool_name}</b><br>
                    <small>{t('trace.status')}: <span class="tool-{r.result.status}">{r.result.status}</span></small><br>
                    <small>{r.result.elapsed_ms:.0f}ms</small><br>
                </div>
                """, unsafe_allow_html=True)
                with st.popover(t("step.view_detail")):
                    st.markdown(f"**{t('step.summary')}**: {r.result.summary[:500]}")
                    if r.result.structured_data:
                        st.json(r.result.structured_data, expanded=False)
                    if r.result.error:
                        st.error(f"{t('trace.error')}: {r.result.error}")


def _render_answer(response):
    st.markdown(response.answer)
    if response.degradation_notes:
        st.warning(f"[!] {t('status.degraded')}: {'; '.join(response.degradation_notes)}")


def _render_metrics(response):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(t("agent.total_elapsed"), f"{response.elapsed_seconds:.2f}s")
    with col2:
        st.metric(t("agent.exec_trace_calls"), str(len(response.tool_calls_made)))
    with col3:
        succ = sum(1 for r in response.tool_calls_made if r.result.status == "success") / max(len(response.tool_calls_made), 1)
        st.metric(t("agent.tool_success_rate"), f"{succ:.0%}")
    with col4:
        ps = len(response.plan.steps) if response.plan else 0
        st.metric(t("agent.plan_steps"), str(ps))


# ---------------------------------------------------------------------------
# Shared render helpers for clarification + full result
# ---------------------------------------------------------------------------


def _render_clarification_ui(response, round_num: int = 1):
    """Show a clarification question with text input for user response."""
    max_rounds = 2
    cur_lang = st.session_state.get("lang", "zh")

    cols = st.columns([1, 10])
    with cols[0]:
        st.markdown(f"### ... [{round_num}/{max_rounds}]")
    with cols[1]:
        st.info(f"**{t('agent.clarify_label')} ({round_num}/{max_rounds})**: {response.clarifying_question}")
        if round_num >= max_rounds:
            st.warning("Next round will trigger Best-Effort Plan (generic recommendation without assuming your needs).")

    st.markdown("")

    # --- Primary: Custom text input (prominent) ---
    st.markdown(f"### Your Reply")

    # Scenario-aware placeholder
    scenario = st.session_state.get("demo_scenario", "")
    scenario_placeholders = {
        "festival_gear": (
            "描述你的具体需求，例如：'夏季杭州户外音乐节，过夜露营两晚，预算2000以内'",
            "Describe your needs, e.g. 'Summer festival in Hangzhou, camping 2 nights, budget under $300'",
            "夏季杭州露营两晚 预算2000",
            "Summer Hangzhou 2 nights $300",
        ),
        "negative_review": (
            "输入差评内容，例如：'给2星，衣服袖口线头太多，扣子也松了，质量有问题'",
            "Enter review text, e.g. '2 stars, loose threads on cuffs, button fell off, poor quality'",
            "给2星 袖口线头多 扣子松了 质量问题",
            "2 stars, quality issue, loose threads",
        ),
        "womenswear": (
            "描述你的具体需求，例如：'简约通勤风，预算3000左右，M码，想要百搭外套'",
            "Describe your needs, e.g. 'Minimalist office style, budget around $400, size M, looking for a versatile blazer'",
            "简约通勤风 M码 预算3000",
            "Minimalist office, size M, $400",
        ),
        "return_order": (
            "描述你的具体需求，例如：'大衣尺码不合适，M换L，订单号ORD-20260501-001'",
            "Describe your needs, e.g. 'Coat size too small, M to L, order #ORD-20260501-001'",
            "尺码不合适 M换L ORD-001",
            "Size exchange M to L",
        ),
    }
    placeholders = scenario_placeholders.get(scenario, scenario_placeholders.get("festival_gear", scenario_placeholders["festival_gear"]))

    c1, c2 = st.columns([4, 1])
    with c1:
        user_input = st.text_area(
            "Your response" if cur_lang == "en" else "输入你的回复...",
            placeholder=placeholders[0] if cur_lang == "zh" else placeholders[1],
            key=f"clarify_input_{round_num}",
            height=80,
            label_visibility="collapsed",
        )
    with c2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button(t("demo.send_btn"), key=f"clarify_send_{round_num}", use_container_width=True, type="primary"):
            val = user_input if user_input else st.session_state.get(f"clarify_input_{round_num}", "")
            if val and val.strip():
                st.session_state.clarify_round = round_num
                st.session_state.last_clarify_response = val.strip()
                st.rerun()

    # --- Secondary: Quick demo shortcuts (small, at bottom) ---
    st.markdown("---")

    # Return-specific reasons (instead of vague response buttons)
    if scenario == "return_order":
        st.caption("Quick select a return reason:")
        return_reasons = ["质量问题", "版型问题", "色差问题", "面料问题", "尺码不符", "其他问题"]
        rcols = st.columns(len(return_reasons))
        for i, reason in enumerate(return_reasons):
            with rcols[i]:
                if st.button(reason, key=f"return_reason_{reason}_{round_num}", use_container_width=True):
                    st.session_state.clarify_round = round_num
                    st.session_state.last_clarify_response = reason
                    st.rerun()

    elif scenario == "negative_review":
        st.caption("选择一条差评样例，或在下方的输入框中自行输入差评内容：")
        sample_reviews = [
            ("2星 质量问题", "给2星。衣服拿到手袖口线头很多，穿了一次扣子就松了，质量太差。"),
            ("1星 版型问题", "1星。M码的肩宽太窄了，袖子也偏短，版型完全不对。"),
            ("3星 色差问题", "3星。实物颜色比图片深很多，图片上是浅灰，拿到手是深灰。"),
            ("3星 面料问题", "3星。面料手感很粗糙，穿了半天就起静电，不透气。"),
        ]
        # Show sample reviews as clickable cards
        for text in sample_reviews:
            label_text = text[0] + ": " + text[1][:60] + "..."
            if st.button(label_text, key=f"review_sample_{text[0][:6]}_{round_num}", use_container_width=True):
                st.session_state.clarify_round = round_num
                st.session_state.last_clarify_response = text[1]
                st.rerun()

    else:
        st.caption("Quick demo shortcuts (click to simulate user responses):")
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            if st.button("Vague: '不太清楚'", key=f"vague_{round_num}", use_container_width=True):
                st.session_state.clarify_round = round_num
                st.session_state.last_clarify_response = "不太清楚"
                st.rerun()
        with sc2:
            if st.button("Vague: '不知道啊'", key=f"vague2_{round_num}", use_container_width=True):
                st.session_state.clarify_round = round_num + 1
                st.session_state.last_clarify_response = "不知道啊"
                st.rerun()
        with sc3:
            spec_label = f"Specific: '{placeholders[2]}'" if cur_lang == "zh" else f"Specific: '{placeholders[3]}'"
            spec_response = placeholders[2] if cur_lang == "zh" else placeholders[3]
            if st.button(spec_label, key=f"spec_{round_num}", use_container_width=True):
                st.session_state.clarify_round = round_num
                st.session_state.last_clarify_response = spec_response
                st.rerun()


def _render_full_result(response):
    """Render the full agent execution result: plan, tool trace, answer, metrics."""
    if response.plan and response.plan.steps:
        st.markdown("---")
        st.markdown(f"### {t('agent.plan_title')}")
        with st.expander(t("agent.plan_detail"), expanded=True):
            _render_plan(response.plan)

    if response.tool_calls_made:
        st.markdown("---")
        st.markdown(f"### {t('agent.tool_trace_title')} ({len(response.tool_calls_made)} {t('agent.exec_trace_calls')})")
        step_groups: dict[int, list] = {}
        for r in response.tool_calls_made:
            step_groups.setdefault(r.step_index, []).append(r)
        for step_idx, records in sorted(step_groups.items()):
            step = response.plan.steps[step_idx] if response.plan and step_idx < len(response.plan.steps) else None
            desc = step.description if step else f"Step {step_idx}"
            _render_step_execution(step_idx, desc, records)

    if response.answer:
        st.markdown("---")
        st.markdown(f"### {t('agent.final_answer')}")
        _render_answer(response)

    st.markdown("---")
    _render_metrics(response)
    st.session_state.clarify_round = 0  # Reset for next run


if __name__ == "__main__":
    run_demo()
