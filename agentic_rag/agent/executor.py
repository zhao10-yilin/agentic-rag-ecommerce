"""Parallel tool execution with degradation scheduling.

The Executor takes a validated :class:`AgentPlan` and runs each step's
tools concurrently via ``asyncio.gather``.  When a tool fails, the
degradation policy determines whether to fail, skip, retry, or fall
back to cache.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agentic_rag.models import (
    AgentPlan,
    AgentStep,
    DegradationPolicy,
    ToolCall,
    ToolCallRecord,
    ToolResult,
)
from agentic_rag.tools.base import BaseTool, ToolRegistry

logger = logging.getLogger(__name__)

# Retry config
MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.5  # seconds


class Executor:
    """Executes an :class:`AgentPlan` step-by-step.

    Parameters
    ----------
    tool_registry:
        The registry to look up tools by name.
    tool_timeout:
        Maximum seconds per tool execution.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        *,
        tool_timeout: float = 30.0,
        dedup_store: Any = None,  # CallDeduplicationStore
        semantic_guard: Any = None,  # SemanticGuard
    ) -> None:
        from agentic_rag.tools.dedup import CallDeduplicationStore
        from agentic_rag.agent.semantic_guard import SemanticGuard

        self._registry = tool_registry
        self._timeout = tool_timeout
        self._dedup = dedup_store or CallDeduplicationStore()
        self._semantic_guard = semantic_guard or SemanticGuard()

    async def execute(self, plan: AgentPlan) -> list[ToolCallRecord]:
        """Execute all steps in *plan* in order, with parallelism within each step.

        Returns a list of :class:`ToolCallRecord` for all completed calls.
        """
        all_records: list[ToolCallRecord] = []
        step_results: dict[int, list[ToolCallRecord]] = {}

        for step in plan.steps:
            # Check that all dependencies are satisfied
            for dep in step.depends_on:
                if dep not in step_results:
                    logger.warning(
                        "Step %d depends on step %d which has not been executed",
                        step.step_index,
                        dep,
                    )

            # ---- SemanticGuard: check before executing this step ----
            violation = self._semantic_guard.check(step, step_results)
            if violation is not None:
                from agentic_rag.agent.semantic_guard import PlanAssumptionViolated
                raise PlanAssumptionViolated(violation, step.step_index)

            logger.info(
                "Executing step %d/%d: %d action(s) — %s",
                step.step_index + 1,
                len(plan.steps),
                len(step.actions),
                step.description,
            )

            records = await self._execute_step(step, plan)
            step_results[step.step_index] = records
            all_records.extend(records)

        return all_records

    async def _execute_step(
        self, step: AgentStep, plan: AgentPlan
    ) -> list[ToolCallRecord]:
        """Run all actions in one step in parallel."""
        t0 = time.perf_counter()

        tasks = []
        for action in step.actions:
            call = ToolCall(
                tool_name=action.tool_name,
                input=action.input,
            )
            tasks.append(self._run_tool_with_policy(call, step.step_index))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        records: list[ToolCallRecord] = []
        for i, result in enumerate(results):
            call = ToolCall(
                tool_name=step.actions[i].tool_name,
                input=step.actions[i].input,
            )
            if isinstance(result, Exception):
                record = ToolCallRecord(
                    tool_name=call.tool_name,
                    input=call.input,
                    result=ToolResult(
                        tool_name=call.tool_name,
                        status="error",
                        summary=f"执行异常：{result}",
                        error=str(result),
                        error_code="EXECUTOR_EXCEPTION",
                    ),
                    idempotency_key=call.idempotency_key,
                    step_index=step.step_index,
                )
            else:
                record = ToolCallRecord(
                    tool_name=call.tool_name,
                    input=call.input,
                    result=result,
                    idempotency_key=call.idempotency_key,
                    step_index=step.step_index,
                )
            records.append(record)

        elapsed = time.perf_counter() - t0
        success_count = sum(1 for r in records if r.result.status in ("success", "degraded"))
        logger.info(
            "Step %d complete in %.2fs: %d/%d succeeded",
            step.step_index,
            elapsed,
            success_count,
            len(records),
        )
        return records

    async def _run_tool_with_policy(
        self, call: ToolCall, step_index: int
    ) -> ToolResult:
        """Execute a single tool with degradation handling.

        For SYNC tools: calls ``tool.execute()`` with a global timeout.
        For ASYNC_POLL tools: submits, then polls at the tool's configured
        interval until completion or the tool's own max_latency is reached.

        Write tools (mutation_kind != READ) check the dedup store before
        execution to prevent duplicate side-effects from retries or re-plans.
        """
        from agentic_rag.tools.base import ExecutionMode, MutationKind

        tool = self._registry.get(call.tool_name)
        if tool is None:
            return ToolResult(
                tool_name=call.tool_name,
                status="error",
                summary=f"未找到工具：{call.tool_name}",
                error=f"Tool '{call.tool_name}' not found",
                error_code="TOOL_NOT_FOUND",
            )

        profile = tool.execution_profile

        # ---- Client-side dedup for write operations ----
        if profile.mutation_kind != MutationKind.READ:
            cached = self._dedup.get(call.idempotency_key)
            if cached is not None:
                logger.info(
                    "Dedup hit for %s/%s — returning cached result",
                    call.tool_name, call.idempotency_key[:8],
                )
                return ToolResult(
                    tool_name=cached.tool_name,
                    status=cached.status,
                    summary=cached.summary,
                    structured_data=cached.structured_data,
                    error=cached.error,
                    error_code=cached.error_code,
                    cache_hit=True,
                    elapsed_ms=cached.elapsed_ms,
                )

        timeout = profile.max_latency_ms / 1000.0

        if profile.mode == ExecutionMode.ASYNC_POLL:
            result = await self._run_async_poll_tool(tool, call, timeout)
        else:
            result = await self._run_sync_tool(tool, call, timeout)

        # ---- Cache successful write results for future dedup ----
        if profile.mutation_kind != MutationKind.READ:
            self._dedup.put(call.idempotency_key, result)

        return result

    async def _run_sync_tool(
        self, tool: BaseTool, call: ToolCall, timeout: float
    ) -> ToolResult:
        try:
            result = await asyncio.wait_for(tool.execute(call), timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning("Tool '%s' timed out after %.1fs", call.tool_name, timeout)
            return await self._apply_degradation(
                tool, call, f"工具执行超时（{timeout}秒）", "TIMEOUT"
            )
        except Exception as exc:
            logger.exception("Tool '%s' failed", call.tool_name)
            return await self._apply_degradation(tool, call, str(exc), "TOOL_ERROR")

    async def _run_async_poll_tool(
        self, tool: BaseTool, call: ToolCall, timeout: float
    ) -> ToolResult:
        """Two-phase: submit → poll until done or timeout.

        The tool's ``execute()`` must return immediately with a task_id
        in ``structured_data``.  The Executor then polls using the tool's
        configured polling interval.
        """
        # Phase 1: Submit
        try:
            submit_result = await asyncio.wait_for(
                tool.execute(call),
                timeout=15.0,  # Submission itself should be fast
            )
        except Exception as exc:
            return await self._apply_degradation(tool, call, str(exc), "ASYNC_SUBMIT_ERROR")

        task_id = submit_result.structured_data.get("task_id")
        if not task_id:
            # The adapter returned a full result (blocking mode fallback)
            return submit_result

        # Phase 2: Poll
        profile = tool.execution_profile
        deadline = time.time() + timeout
        poll_count = 0

        while time.time() < deadline:
            await asyncio.sleep(profile.polling_interval_ms / 1000.0)
            poll_count += 1

            try:
                # Build a poll call — the tool knows how to check its own status
                poll_call = ToolCall(
                    tool_name=call.tool_name,
                    input={"action": "poll", "task_id": task_id},
                    idempotency_key=call.idempotency_key,
                )
                poll_result = await asyncio.wait_for(
                    tool.execute(poll_call),
                    timeout=10.0,
                )
                if poll_result.status == "success":
                    poll_result.elapsed_ms = (time.time() - (deadline - timeout)) * 1000
                    return poll_result
            except Exception:
                # Individual poll failed, but we keep trying until deadline
                logger.debug("Poll %d for task %s failed, retrying...", poll_count, task_id)
                continue

        # Timeout — return degraded
        return ToolResult(
            tool_name=call.tool_name,
            status="degraded",
            summary=f"异步任务 {task_id} 在 {timeout}秒内未完成（已轮询{poll_count}次）。结果可用后系统会通知您。",
            structured_data={"task_id": task_id, "status": "pending"},
            error=f"Async poll timeout after {poll_count} attempts",
            error_code="ASYNC_POLL_TIMEOUT",
        )

    async def _apply_degradation(
        self,
        tool: BaseTool,
        call: ToolCall,
        error_msg: str,
        error_code: str,
    ) -> ToolResult:
        """Apply the tool's degradation policy."""

        if tool.degradation == DegradationPolicy.FAIL_FAST:
            return ToolResult(
                tool_name=call.tool_name,
                status="error",
                summary=f"工具 '{call.tool_name}' 执行失败：{error_msg}",
                error=error_msg,
                error_code=error_code,
            )

        if tool.degradation == DegradationPolicy.RETRY_WITH_BACKOFF:
            for attempt in range(1, MAX_RETRIES + 1):
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.info(
                    "Retrying '%s' (attempt %d/%d) after %.1fs",
                    call.tool_name,
                    attempt,
                    MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                try:
                    return await asyncio.wait_for(
                        tool.execute(call),
                        timeout=self._timeout,
                    )
                except Exception:
                    if attempt == MAX_RETRIES:
                        break

            return ToolResult(
                tool_name=call.tool_name,
                status="error",
                summary=f"工具 '{call.tool_name}' 重试 {MAX_RETRIES} 次后仍失败：{error_msg}",
                error=error_msg,
                error_code=f"{error_code}_RETRIES_EXHAUSTED",
            )

        if tool.degradation == DegradationPolicy.RETURN_CACHED:
            return ToolResult(
                tool_name=call.tool_name,
                status="degraded",
                summary=f"[降级] 工具 '{call.tool_name}' 当前不可用。使用缓存数据。",
                error=error_msg,
                error_code=error_code,
            )

        if tool.degradation == DegradationPolicy.SKIP:
            return ToolResult(
                tool_name=call.tool_name,
                status="degraded",
                summary=f"[已跳过] 工具 '{call.tool_name}' 当前不可用。",
                error=error_msg,
                error_code=error_code,
            )

        if tool.degradation == DegradationPolicy.INFORM_USER:
            return ToolResult(
                tool_name=call.tool_name,
                status="degraded",
                summary=f"抱歉，'{call.tool_name}' 服务暂时不可用。{error_msg}",
                error=error_msg,
                error_code=error_code,
            )

        # Fallback
        return ToolResult(
            tool_name=call.tool_name,
            status="error",
            summary=f"工具执行失败：{error_msg}",
            error=error_msg,
            error_code=error_code,
        )
