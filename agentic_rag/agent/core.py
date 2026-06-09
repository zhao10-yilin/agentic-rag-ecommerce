"""Plan-and-Execute Agent — the central orchestrator.

This is the top-level entry point.  It wires together the Planner,
PlanValidator, Clarifier, Executor, Reflector, and Memory into a
coherent agent loop.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agentic_rag.agent.clarifier import Clarifier
from agentic_rag.agent.executor import Executor
from agentic_rag.agent.input_sanitizer import InputSanitizer, Severity
from agentic_rag.agent.plan_validator import PlanValidationError, PlanValidator
from agentic_rag.agent.planner import Planner
from agentic_rag.agent.semantic_guard import PlanAssumptionViolated
from agentic_rag.agent.state import can_transition, is_terminal
from agentic_rag.config import get_settings
from agentic_rag.models import (
    AgentMemoryState,
    AgentPlan,
    AgentResponse,
    AgentState,
    ToolCallRecord,
)
from agentic_rag.tools.base import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Synthesis prompt
# ---------------------------------------------------------------------------

SYNTHESIS_SYSTEM_PROMPT = """\
你是一个专业的电商智能助手。现在你需要根据多个工具的执行结果，为用户生成最终的回答。

## 回答要求
1. **结构清晰**：根据信息的重要程度组织内容，使用适当的分段。
2. **引用来源**：如果你的回答基于知识库检索结果，注明信息来源。
3. **诚实透明**：
   - 明确区分从工具获得的"实际数据"和你的"推理建议"。
   - 如果某个工具返回了降级/错误状态，委婉告知用户该部分信息可能不完整。
4. **可操作**：如果用户需要下一步行动（如下单、查看详情），提供明确指引。
5. **个性化**：如果记忆中有用户偏好，在推荐时体现出来。
6. **中文回复**：始终使用流畅、专业的中文。

## 回复格式
直接输出回答文本，不需要 JSON 包装。"""


# ---------------------------------------------------------------------------
# PlanAndExecuteAgent
# ---------------------------------------------------------------------------


class PlanAndExecuteAgent:
    """Plan-and-Execute agent for e-commerce scenarios.

    Parameters
    ----------
    llm_gateway:
        Existing ``LLMGateway`` for planning, clarification, and synthesis.
    tool_registry:
        Registry of all available tools.
    memory_manager:
        Long-term memory manager (or ``None`` to skip memory).
    reflector:
        LLM reflector for pre-synthesis critique (or ``None`` to skip reflection).
    """

    def __init__(
        self,
        llm_gateway: Any,
        tool_registry: ToolRegistry,
        *,
        memory_manager: Any = None,
        reflector: Any = None,
        experiment_name: str = "agentic_rag_v1",
        experiment_groups: list[str] | None = None,
        rollout_config: Any = None,  # RolloutConfig — enables canary A/B
    ) -> None:
        from agentic_rag.evaluation.attribution import ExperimentBucketer

        self._llm = llm_gateway
        self._registry = tool_registry
        self._memory = memory_manager
        self._bucketer = ExperimentBucketer(
            experiment_name,
            experiment_groups or ["control", "treatment"],
        )
        self._reflector = reflector

        # If a rollout config is active, route requests to variants
        self._variant_router = None
        if rollout_config is not None:
            from agentic_rag.evaluation.rollout import VariantRouter
            self._variant_router = VariantRouter(rollout_config, self._bucketer)
            self._rollout_config = rollout_config
            logger.info(
                "Canary rollout active: experiment=%s canary_pct=%.0f%%",
                rollout_config.experiment_name, rollout_config.canary_pct,
            )

        self._planner = Planner(llm_gateway, tool_registry)
        self._validator = PlanValidator(tool_registry)
        self._clarifier = Clarifier(llm_gateway)
        self._sanitizer = InputSanitizer()
        self._executor = Executor(
            tool_registry,
            tool_timeout=get_settings().tool_timeout_seconds,
        )

        self._settings = get_settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        user_message: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        _clarification_round: int = 0,
    ) -> AgentResponse:
        """Execute the full agent loop.

        Returns an :class:`AgentResponse` — if ``clarifying_question`` is
        set, the caller should display it to the user and call
        :meth:`continue_with_clarification`.

        Parameters
        ----------
        _clarification_round:
            Internal — tracks how many clarification rounds have occurred.
        """
        t0 = time.perf_counter()
        state = AgentState.PLANNING
        trace_id = uuid.uuid4().hex[:16]
        experiment_group = self._bucketer.assign(user_id or "anonymous")

        # ---- 0. Input sanitization ------------------------------------
        sanitization = self._sanitizer.sanitize(user_message)
        if sanitization.should_block:
            logger.warning(
                "Blocked potentially malicious input: flags=%s input_preview=%s",
                sanitization.flags,
                user_message[:120],
            )
            elapsed = time.perf_counter() - t0
            return self._build_response(
                answer="抱歉，您的请求包含了系统无法处理的内容。请重新描述您的需求。",
                state=AgentState.ERROR,
                elapsed=round(elapsed, 3),
                degradation_notes=[f"输入已被安全过滤器拦截: {sanitization.flags}"],
                trace_id=trace_id,
                experiment_group=experiment_group,
            )

        safe_message = sanitization.sanitized

        # ---- 1. Load memory (tiered, query-aware) -----------------------
        memory_state = await self._load_memory(user_id)

        # ---- 2. Plan ---------------------------------------------------
        # Tier 1 identity is always included. Tier 2 is included only if
        # the current query matched relevant purchase/conversation history.
        memory_block = self._format_memory_for_planning(
            memory_state, query=safe_message
        )
        plan = await self._planner.plan(
            safe_message,
            memory_context=memory_block,
        )

        # ---- 3. Clarify? -----------------------------------------------
        if self._clarifier.needs_clarification(plan):
            # Enforce max rounds: if we've already clarified twice, stop asking
            if _clarification_round >= self._settings.max_clarification_rounds:
                logger.info(
                    "Clarification round %d reached max %d — forcing Best-Effort Plan",
                    _clarification_round, self._settings.max_clarification_rounds,
                )
                plan = self._force_best_effort_plan(user_message, plan)
            else:
                question = await self._clarifier.generate_question(user_message, plan)
                elapsed = time.perf_counter() - t0
                return self._build_response(
                    answer="",
                    clarifying_question=question,
                    state=AgentState.CLARIFYING,
                    plan=plan,
                    elapsed=round(elapsed, 3),
                    trace_id=trace_id,
                    experiment_group=experiment_group,
                    clarification_round=_clarification_round,
                )

        # ---- 4. Validate plan ------------------------------------------
        validation_errors = self._validator.validate(plan)
        validation_attempts = 0
        while validation_errors and validation_attempts < 2:
            logger.warning("Plan validation failed, re-planning: %s", validation_errors[:3])
            plan = await self._planner.replan(
                plan,
                reflection_notes=f"计划校验失败：{'; '.join(validation_errors[:5])}",
            )
            validation_errors = self._validator.validate(plan)
            validation_attempts += 1

        if validation_errors:
            logger.error("Plan validation failed after %d attempts: %s", validation_attempts, validation_errors)
            elapsed = time.perf_counter() - t0
            return self._build_response(
                answer="抱歉，我暂时无法处理您的请求。请稍后再试或联系人工客服。",
                state=AgentState.ERROR,
                plan=plan,
                degradation_notes=[f"计划校验失败: {e}" for e in validation_errors[:3]],
                elapsed=round(elapsed, 3),
                trace_id=trace_id,
                experiment_group=experiment_group,
            )

        # ---- 5. Execute ------------------------------------------------
        try:
            all_records = await self._executor.execute(plan)
        except PlanAssumptionViolated as violation:
            logger.warning(
                "SemanticGuard triggered re-plan at step %d: %s",
                violation.violating_step, violation.message,
            )
            # Re-plan with the violation as reflection feedback
            plan = await self._planner.replan(
                plan,
                reflection_notes=(
                    f"步骤 {violation.violating_step} 执行前被语义护栏拦截: "
                    f"{violation.message}\n"
                    "请移除不合理或冲突的工具调用，调整计划的步骤顺序。"
                ),
            )
            all_records = await self._executor.execute(plan)

        # ---- 6. Reflect ------------------------------------------------
        reflection_rounds = 0
        while reflection_rounds < self._settings.max_reflection_rounds:
            if self._reflector is None:
                break
            issues = await self._reflector.critique(plan, all_records)
            if not issues.get("needs_replan", False):
                break
            logger.info("Reflection round %d: re-planning needed", reflection_rounds + 1)
            plan = await self._planner.replan(
                plan,
                reflection_notes=issues.get("notes", ""),
                partial_results=self._format_records(all_records),
            )
            new_records = await self._executor.execute(plan)
            all_records.extend(new_records)
            reflection_rounds += 1

        # ---- 7. Synthesize ---------------------------------------------
        answer = await self._synthesize(user_message, plan, all_records)

        # ---- 8. Save memory --------------------------------------------
        if self._memory:
            await self._save_memory(user_id, user_message, answer, all_records)

        elapsed = time.perf_counter() - t0
        degradation_notes = [
            r.result.error
            for r in all_records
            if r.result.status in ("degraded", "error") and r.result.error
        ]

        logger.info(
            "Agent run complete in %.2fs: state=%s steps=%d tools=%d reflections=%d",
            elapsed,
            AgentState.DONE.value,
            len(plan.steps),
            len(all_records),
            reflection_rounds,
        )

        return self._build_response(
            answer=answer,
            state=AgentState.DONE,
            sources=self._extract_sources(all_records),
            tool_calls_made=all_records,
            plan=plan,
            elapsed=round(elapsed, 3),
            degradation_notes=degradation_notes,
            trace_id=trace_id,
            experiment_group=experiment_group,
        )

    async def continue_with_clarification(
        self,
        user_response: str,
        previous_response: AgentResponse,
        *,
        user_id: str | None = None,
    ) -> AgentResponse:
        """Continue the agent loop after the user responds to a clarifying question.

        The ``previous_response`` should be the one that had ``state == CLARIFYING``.
        Tracks the number of clarification rounds; after max_clarification_rounds
        is reached, forces a Best-Effort Plan instead of asking again.
        """
        if previous_response.plan is None:
            return self._build_response(
                answer="抱歉，内部状态丢失，请重新描述您的需求。",
                state=AgentState.ERROR,
                trace_id=uuid.uuid4().hex[:16],
                experiment_group=self._bucketer.assign(user_id or "anonymous"),
            )

        # Track clarification rounds from the previous response
        current_round = previous_response.clarification_round + 1

        enriched_query = await self._clarifier.enrich_with_clarification(
            previous_response.plan, user_response
        )
        return await self.run(
            enriched_query,
            user_id=user_id,
            _clarification_round=current_round,
        )

    # ------------------------------------------------------------------
    # Internal — memory
    # ------------------------------------------------------------------

    async def _load_memory(self, user_id: str | None) -> AgentMemoryState:
        if self._memory is None or user_id is None:
            return AgentMemoryState()

        try:
            profile = await self._memory.load_user_profile(user_id)
            conversations = await self._memory.search_past_conversations(
                "", user_id, k=3
            )
            return AgentMemoryState(
                user_id=user_id,
                user_profile=profile or {},
                recent_conversations=conversations or [],
            )
        except Exception:
            logger.exception("Failed to load memory for user %s", user_id)
            return AgentMemoryState(user_id=user_id)

    async def _save_memory(
        self,
        user_id: str | None,
        query: str,
        answer: str,
        records: list[ToolCallRecord],
    ) -> None:
        if self._memory is None or user_id is None:
            return
        try:
            await self._memory.save_conversation(user_id, query, answer, records)
        except Exception:
            logger.exception("Failed to save memory for user %s", user_id)

    @staticmethod
    def _format_memory_context(state: AgentMemoryState) -> str:
        """Legacy format — kept for backward compatibility in tests.
        Prefer _format_memory_for_planning() which uses tiered loading.
        """
        return PlanAndExecuteAgent._format_memory_for_planning(state, query="")

    @staticmethod
    def _format_memory_for_planning(
        state: AgentMemoryState, *, query: str = "", intent: str = ""
    ) -> str:
        """Build tiered memory block for Planner prompt.

        Tier 1 (identity signals) is always included.  Tier 2
        (query-relevant purchases + conversations) is included only
        when keywords from the query overlap with stored history.
        """
        from agentic_rag.memory.compressor import MemoryCompressor

        compressor = MemoryCompressor()
        tiered = compressor.compress(
            user_profile=state.user_profile if state.user_profile else None,
            purchase_history=state.user_profile.get("purchase_history", [])
            if state.user_profile else [],
            past_conversations=state.recent_conversations,
            current_query=query,
            current_intent=intent,
        )
        return tiered.to_prompt_block(include_tier2=bool(query))

    # ------------------------------------------------------------------
    # Internal — synthesis
    # ------------------------------------------------------------------

    async def _synthesize(
        self,
        user_message: str,
        plan: AgentPlan,
        records: list[ToolCallRecord],
    ) -> str:
        """Synthesize final answer from all tool results."""
        tool_outputs = self._format_records(records)
        synthesis_hint = plan.final_synthesis_hint or ""

        user_prompt = f"""\
## 用户原始请求
{user_message}

## 改写后的查询
{plan.rewritten_query or user_message}

## 执行计划意图
{plan.intent}

## 合成提示
{synthesis_hint or "综合所有工具结果给出最佳回答"}

## 工具执行结果
{tool_outputs}

请基于以上信息生成最终回答。"""

        client = self._llm._get_async_client()

        try:
            response = await client.chat.completions.create(
                model=self._llm._model,
                messages=[
                    {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2048,
            )
            return response.choices[0].message.content or "抱歉，我无法生成回答。"
        except Exception as exc:
            logger.exception("Synthesis failed")
            return f"抱歉，回答生成失败：{exc}。以下是收集到的原始信息：\n\n{tool_outputs}"

    # ------------------------------------------------------------------
    # Internal — helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _force_best_effort_plan(
        user_message: str, original_plan: AgentPlan
    ) -> AgentPlan:
        """After max_clarification_rounds, force a limited-scope plan.

        Does NOT fabricate understanding.  Runs a generic RAG search and
        explicitly tells the synthesis step to admit uncertainty and offer
        structured options.
        """
        logger.info("Forcing Best-Effort Plan for: %s", user_message[:80])
        return AgentPlan(
            original_query=user_message,
            rewritten_query=user_message,
            intent=original_plan.intent or "general",
            intent_clarity=original_plan.intent_clarity,  # Keep the real score
            steps=[
                AgentStep(
                    step_index=0,
                    description="模糊意图通用检索",
                    actions=[
                        AgentAction(
                            tool_name="rag_search",
                            input={"query": user_message},
                            reason="意图不明确，尝试通用知识库检索",
                        ),
                    ],
                ),
            ],
            final_synthesis_hint=(
                "用户意图经多轮澄清仍不明确（clarity={:.0%}）。"
                "不要假装理解了。基于检索结果给出通用建议，明确提出信息局限性，"
                "主动列出3-5个可能的细分方向，每个带简短描述，邀请用户选择。"
                "使用以下格式：\n"
                "> 为了让推荐更精准，你可以从下面几个方向帮我缩小范围。"
            ).format(original_plan.intent_clarity),
        )

    @staticmethod
    def _format_records(records: list[ToolCallRecord]) -> str:
        parts: list[str] = []
        for r in records:
            status_icon = {"success": "✅", "degraded": "⚠️", "error": "❌", "timeout": "⏱️"}.get(
                r.result.status, "❓"
            )
            parts.append(
                f"### {status_icon} {r.tool_name}\n"
                f"状态: {r.result.status}\n"
                f"结果: {r.result.summary}\n"
            )
            if r.result.structured_data:
                # Include keys but keep it concise
                keys = list(r.result.structured_data.keys())
                parts.append(f"数据字段: {', '.join(keys)}")
            parts.append("")
        return "\n".join(parts) if parts else "(没有工具执行结果)"

    @staticmethod
    def _extract_sources(records: list[ToolCallRecord]) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for r in records:
            if r.result.status in ("success", "degraded"):
                data = r.result.structured_data
                if "sources" in data:
                    sources.extend(data["sources"])
        return sources

    # ------------------------------------------------------------------
    # Attribution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_response(
        *,
        answer: str = "",
        clarifying_question: str | None = None,
        state: AgentState = AgentState.DONE,
        sources: list[dict[str, Any]] | None = None,
        tool_calls_made: list[ToolCallRecord] | None = None,
        plan: AgentPlan | None = None,
        elapsed: float = 0.0,
        degradation_notes: list[str] | None = None,
        trace_id: str = "",
        experiment_group: str = "control",
        clarification_round: int = 0,
    ) -> AgentResponse:
        """Construct an AgentResponse with attribution fields injected."""
        records = tool_calls_made or []
        return AgentResponse(
            answer=answer,
            clarifying_question=clarifying_question,
            state=state,
            sources=sources or [],
            tool_calls_made=records,
            plan=plan,
            elapsed_seconds=elapsed,
            degradation_notes=degradation_notes or [],
            trace_id=trace_id,
            experiment_group=experiment_group,
            clarification_round=clarification_round,
            recommended_product_ids=PlanAndExecuteAgent._extract_product_ids(records),
        )

    @staticmethod
    def _extract_product_ids(records: list[ToolCallRecord]) -> list[str]:
        """Extract product IDs mentioned in tool results for attribution join."""
        ids: set[str] = set()
        for r in records:
            data = r.result.structured_data
            # From inventory: {"OUT-001": 45, "OUT-002": 120}
            for key, val in data.items():
                if isinstance(key, str) and "-" in key and isinstance(val, (int, float)):
                    ids.add(key)
            # From KG: entities have "id" field
            for entity in data.get("entities", []):
                if isinstance(entity, dict) and "id" in entity:
                    ids.add(entity["id"])
        return sorted(ids)
