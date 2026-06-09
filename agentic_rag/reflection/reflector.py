"""LLM-based reflection — critiques results pre-synthesis.

Only runs at key checkpoints (pre-synthesis), not after every tool
execution.  This keeps latency down while still catching hallucinations,
missing information, and contradictory results.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agentic_rag.models import AgentPlan, ToolCallRecord

logger = logging.getLogger(__name__)

REFLECTION_SYSTEM_PROMPT = """\
你是一个严格的质量检查员。你的任务是审查一个电商助手的执行结果，判断是否需要修正。

## 检查项
1. **幻觉检测**：工具返回的事实是否被正确使用？有没有编造的信息？
2. **信息完整性**：是否回答了用户的所有核心问题？有没有遗漏？
3. **一致性**：不同工具的结果之间有没有矛盾？
4. **时效性**：是否使用了过时的信息（如已经下架的商品）？
5. **可操作性**：回答是否给出了用户能执行的下一步？

## 输出格式
你必须只输出一个 JSON 对象：

```json
{
  "needs_replan": false,
  "overall_quality": 0.0-1.0,
  "issues": ["问题1", "问题2"],
  "notes": "详细的评审意见"
}
```

设置 ``needs_replan: true`` 仅在质量 < 0.4 或存在严重事实错误时。"""


class Reflector:
    """LLM-based reflection for quality assurance.

    Parameters
    ----------
    llm_gateway:
        The existing ``LLMGateway``.
    quality_threshold:
        Minimum quality score before requiring a re-plan (default 0.4).
    """

    def __init__(
        self,
        llm_gateway: Any,
        *,
        quality_threshold: float = 0.4,
    ) -> None:
        self._llm = llm_gateway
        self._threshold = quality_threshold

    async def critique(
        self,
        plan: AgentPlan,
        records: list[ToolCallRecord],
    ) -> dict[str, Any]:
        """Critique the execution results and decide whether re-planning is needed.

        Returns a dict with keys: ``needs_replan``, ``overall_quality``,
        ``issues``, ``notes``.
        """
        # Build the user prompt
        plan_summary = f"意图: {plan.intent}\n改写查询: {plan.rewritten_query}\n步骤数: {len(plan.steps)}"

        tool_summary_parts: list[str] = []
        for r in records:
            tool_summary_parts.append(
                f"- {r.tool_name}: {r.result.status} — {r.result.summary[:200]}"
            )
        tool_summary = "\n".join(tool_summary_parts)

        user_prompt = f"""\
## 执行计划
{plan_summary}

## 工具执行结果
{tool_summary}

请审查以上结果。"""

        client = self._llm._get_async_client()

        try:
            response = await client.chat.completions.create(
                model=self._llm._light_model,
                messages=[
                    {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=1024,
            )
            raw = response.choices[0].message.content or "{}"
            return self._parse_reflection(raw)
        except Exception:
            logger.exception("Reflection LLM call failed")
            return {
                "needs_replan": False,
                "overall_quality": 0.8,
                "issues": [],
                "notes": "Reflection skipped due to LLM error.",
            }

    @staticmethod
    def _parse_reflection(raw: str) -> dict[str, Any]:
        raw = raw.strip()
        if raw.startswith("```"):
            import re
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse reflection JSON: %s", raw[:200])
            return {
                "needs_replan": False,
                "overall_quality": 0.5,
                "issues": [],
                "notes": f"Failed to parse reflection: {raw[:300]}",
            }
