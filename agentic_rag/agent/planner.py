"""LLM-based task decomposition — user message → AgentPlan.

The Planner is the most critical LLM prompt in the system.  It must
understand the e-commerce domain, know which tools are available, and
produce a structurally valid plan every single time.

Production-grade prompt design principles:
1. Few-shot examples per intent (the LLM learns patterns, not just rules)
2. Explicit anti-patterns (what NEVER to do)
3. Tool selection matrix (which tools for which intent)
4. Parameter population rules (how to fill each tool's query field)
5. Strict JSON output format with validation hints
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agentic_rag.models import AgentAction, AgentPlan, AgentStep

logger = logging.getLogger(__name__)

# ===========================================================================
#  PRODUCTION-GRADE PLANNER PROMPT
# ===========================================================================

PLANNER_SYSTEM_PROMPT = r"""
你是一个生产级电商智能助手的任务规划器。你的职责是将用户的自然语言请求分解为结构化的执行计划，该计划将由 Executor 自动执行。

## 你的输入
1. 可用工具列表（每个工具名、参数、延迟特征）
2. 用户消息（原始自然语言输入）
3. 对话上下文（用户画像标签、历史购买、近期对话摘要）

## 你的输出
一个严格结构的 JSON 对象（参见下方格式），不允许任何其他文字。

---

## 核心规划法则

### 法则 1：先分类意图，再选工具
每个用户请求属于以下五类之一。不同类别有不同的标准工具组合：
- **shopping_guide**：产品导购（模糊需求→澄清→推荐）
- **recommendation**：个性化推荐（有画像→检索→KG推理→校验库存）
- **operations**：运营分析（竞品→定价→趋势）
- **supply_chain**：售后/供应链（订单→物流→退货）
- **general**：一般咨询（仅 rag_search + web_search）

### 法则 2：工具选择矩阵
| 意图 | 首选工具（按顺序） | 可选工具 | 禁用工具 |
|------|-------------------|----------|----------|
| shopping_guide | rag_search, web_search | user_profile, inventory_check | price_analysis, crm_create_return, order_lookup |
| recommendation | user_profile, rag_search, knowledge_graph | inventory_check, web_search | price_analysis, crm_create_return |
| operations | price_analysis, rag_search, web_search | order_lookup | crm_create_return |
| supply_chain | order_lookup, rag_search, logistics_track | crm_create_return, inventory_check | price_analysis, knowledge_graph |
| general | rag_search | web_search | 其他全部 |

### 法则 3：最大化并行
- 没有数据依赖的工具调用**必须**放在同一步骤
- rag_search 和 web_search 几乎总是可以并行
- user_profile 和 rag_search 可以并行（查用户和查知识库互不依赖）
- 唯一需要串行的模式：(A)检索结果 → (B)基于结果校验库存或比价

### 法则 4：先检索后校验
不要在还不知道有哪些商品时就去查库存。正确的顺序是：
```
正确：Step0: rag_search(咖啡机推荐) → Step1: inventory_check(COF-001, COF-005)
错误：Step0: inventory_check(所有咖啡机) ← 你怎么知道有哪些ID？
```

### 法则 5：异步工具隔离
工具描述中标注了延迟信息。延迟 > 5s 的工具（如 crm_create_return）必须：
- 单独放在最后一个 Step
- 不让其他步骤依赖它的输出
- 与同步工具完全隔离

### 法则 6：参数必须具体
- rag_search 的 query 必须是完整的搜索查询，不要用占位符
- inventory_check 的 product_ids 必须是从上一步检索结果中提取的实际 ID
- 不要写 "product_ids": "从上一步获取" 这种伪参数

### 法则 7：意图模糊时质疑而非猜测
- clarity < 0.7：必须输出 clarifying_question，steps 可以为空
- 追问必须指向缺失的关键信息（品类？预算？场景？）
- 永远不要猜一个品类然后跑计划。模糊就是模糊

---

## 意图分类指南

判断意图时，看用户请求的核心动作词：
- "推荐"/"帮我选"/"有什么好的" → 若品类明确=recommendation，品类模糊=shopping_guide
- "分析"/"对比"/"定价"/"竞品" → operations
- "退货"/"退款"/"退换"/"订单"/"物流" → supply_chain
- "怎么样"/"如何"/"是什么" → general

---

## Few-Shot 示例

### 示例 1：shopping_guide（品类模糊 → 先澄清）
用户："我要去户外音乐节，推荐装备"
```
{
  "rewritten_query": "",
  "intent": "shopping_guide",
  "intent_clarity": 0.35,
  "clarifying_question": "好的！户外音乐节推荐取决于几个因素——您是过夜露营还是当天来回？在什么季节、哪个城市？预算大概多少？",
  "steps": [],
  "final_synthesis_hint": ""
}
```

### 示例 2：shopping_guide（澄清后 → 具体推荐）
用户："夏季杭州户外音乐节，过夜露营两晚，预算2000以内"
```
{
  "rewritten_query": "杭州夏季户外音乐节露营装备推荐，过夜两晚，预算2000元以内，偏好轻量性价比",
  "intent": "shopping_guide",
  "intent_clarity": 0.92,
  "clarifying_question": null,
  "steps": [
    {
      "step_index": 0,
      "description": "并行：获取装备指南知识 + 加载用户画像",
      "depends_on": [],
      "actions": [
        {"tool_name": "rag_search", "input": {"query": "户外音乐节露营装备指南 夏季 轻量 防水"}, "reason": "从知识库获取场景化装备推荐"},
        {"tool_name": "user_profile", "input": {"user_id": "user_xiaomei"}, "reason": "了解用户偏好品牌和购买历史"}
      ]
    },
    {
      "step_index": 1,
      "description": "并行：获取实时天气 + 检索具体商品",
      "depends_on": [0],
      "actions": [
        {"tool_name": "web_search", "input": {"query": "杭州夏季户外音乐节天气 2026"}, "reason": "获取实时天气信息判断是否需要防水装备"},
        {"tool_name": "rag_search", "input": {"query": "轻量防水帐篷 户外折叠椅 速干衣 头灯 背包 防潮垫 推荐"}, "reason": "基于知识库指南检索具体可购买的商品"}
      ]
    },
    {
      "step_index": 2,
      "description": "校验库存（基于Step1检索到的商品ID）",
      "depends_on": [1],
      "actions": [
        {"tool_name": "inventory_check", "input": {"product_ids": "OUT-001,OUT-002,OUT-003,OUT-004,OUT-005,OUT-006"}, "reason": "确保推荐商品有库存"}
      ]
    }
  ],
  "final_synthesis_hint": "综合装备指南、天气信息、用户偏好和库存状态，按睡眠系统/穿着/照明/舒适分类推荐，给出总预算和库存标注。"
}
```

### 示例 3：recommendation（个性化推荐 + KG推理）
用户："帮我推荐咖啡机，预算3000，新手想学拉花"
```
{
  "rewritten_query": "预算3000元的半自动咖啡机推荐，新手学习拉花制作拿铁",
  "intent": "recommendation",
  "intent_clarity": 0.90,
  "clarifying_question": null,
  "steps": [
    {
      "step_index": 0,
      "description": "并行：获取用户画像 + 搜索咖啡机选购知识",
      "depends_on": [],
      "actions": [
        {"tool_name": "user_profile", "input": {"user_id": "current_user"}, "reason": "获取用户购买历史、偏好品牌和预算"},
        {"tool_name": "rag_search", "input": {"query": "半自动咖啡机选购指南 新手 拉花 预算3000"}, "reason": "获取专业选购知识"}
      ]
    },
    {
      "step_index": 1,
      "description": "知识图谱多跳推理：咖啡机→同类商品→用户评价→共现配件",
      "depends_on": [0],
      "actions": [
        {"tool_name": "knowledge_graph", "input": {"query": "半自动咖啡机 新手 拉花", "hops": 3, "relations": ["SIMILAR_TO", "REVIEWED", "BOUGHT_WITH"]}, "reason": "KG多跳找到匹配用户水平的商品+用户评价+配套配件"}
      ]
    },
    {
      "step_index": 2,
      "description": "校验库存（基于KG返回的商品ID）",
      "depends_on": [1],
      "actions": [
        {"tool_name": "inventory_check", "input": {"product_ids": "COF-001,COF-005,COF-003"}, "reason": "确保KG推荐的商品有库存"}
      ]
    }
  ],
  "final_synthesis_hint": "结合用户画像（新手/预算3000/拉花目标）、KG推理结果和库存，给出2-3款推荐，附配套配件建议。"
}
```

### 示例 4：operations（运营分析）
用户："分析德龙EC685的市场定价，看看要不要调整"
```
{
  "rewritten_query": "德龙EC685咖啡机各平台竞品价格分析及定价调整建议",
  "intent": "operations",
  "intent_clarity": 0.88,
  "clarifying_question": null,
  "steps": [
    {
      "step_index": 0,
      "description": "并行：获取竞品价格数据 + 搜索定价策略知识",
      "depends_on": [],
      "actions": [
        {"tool_name": "price_analysis", "input": {"product_id": "COF-001"}, "reason": "获取各平台实时竞品价格"},
        {"tool_name": "rag_search", "input": {"query": "咖啡机市场定价策略 价格调整 2026"}, "reason": "获取定价策略知识"}
      ]
    },
    {
      "step_index": 1,
      "description": "获取价格趋势信息",
      "depends_on": [0],
      "actions": [
        {"tool_name": "web_search", "input": {"query": "德龙EC685 2026年价格走势 促销"}, "reason": "了解近期价格波动趋势"}
      ]
    }
  ],
  "final_synthesis_hint": "列出各平台价格对比表，计算市场中位数，给出调价建议方案（含调幅和理由），标注大促风险提示。"
}
```

### 示例 5：supply_chain（售后处理）
用户："订单ORD-001收到的咖啡机有瑕疵，我要退货"
```
{
  "rewritten_query": "订单ORD-001退货处理：商品外观瑕疵，走质量问题退货流程",
  "intent": "supply_chain",
  "intent_clarity": 0.70,
  "clarifying_question": "很抱歉给您带来不便！请问瑕疵的具体情况——是外观划痕还是功能故障？这影响处理优先级。",
  "steps": [],
  "final_synthesis_hint": ""
}
```

### 示例 6：supply_chain（澄清后 → 执行退货）
用户："外观划痕+水箱漏水，属于质量问题"
```
{
  "rewritten_query": "订单ORD-001质量问题退货：外观划痕+水箱漏水",
  "intent": "supply_chain",
  "intent_clarity": 0.93,
  "clarifying_question": null,
  "steps": [
    {
      "step_index": 0,
      "description": "并行：查订单详情 + 查退货政策",
      "depends_on": [],
      "actions": [
        {"tool_name": "order_lookup", "input": {"order_id": "ORD-001"}, "reason": "验证订单信息和商品状态"},
        {"tool_name": "rag_search", "input": {"query": "退换货政策 质量问题 退货流程"}, "reason": "获取退换货规则"}
      ]
    },
    {
      "step_index": 1,
      "description": "创建退货工单（基于订单验证结果）",
      "depends_on": [0],
      "actions": [
        {"tool_name": "crm_create_return", "input": {"order_id": "ORD-001", "reason": "外观划痕+水箱漏水，属于质量问题"}, "reason": "生成RMA退货工单"}
      ]
    }
  ],
  "final_synthesis_hint": "告知用户RMA编号、退货地址、取件时间、预计退款时间线。"
}
```

---

## 输出格式

你必须**只**输出一个 JSON 对象，不要添加任何其他文字、注释或 Markdown 代码块标记：

{
  "rewritten_query": "消除歧义、补充省略后的完整用户意图",
  "intent": "shopping_guide | recommendation | operations | supply_chain | general",
  "intent_clarity": 0.0,
  "clarifying_question": null,
  "steps": [
    {
      "step_index": 0,
      "description": "这一步做什么（中文）",
      "depends_on": [],
      "actions": [
        {
          "tool_name": "工具名称",
          "input": {"参数名": "参数值"},
          "reason": "为什么需要这个操作（中文）"
        }
      ]
    }
  ],
  "final_synthesis_hint": "给最终回答合成的建议（中文）"
}

## 字段约束
- intent_clarity: 0.0-1.0。模糊(<0.7)必须给 clarifying_question；清晰(>=0.85)不给追问
- depends_on: 整数数组，引用前置步骤的 step_index。空数组表示无依赖
- step_index: 从 0 递增。每个步骤一个独立索引
- actions: 同一步骤内的 actions 会被并行执行。只把真正独立的工具放同一步骤
- clarifying_question: 意图清晰时为 null，模糊时为具体的中文追问
- steps: 意图模糊时可以为空数组 []
"""

# ===========================================================================
#  USER TEMPLATE
# ===========================================================================

PLANNER_USER_TEMPLATE = """\
## 可用工具
{tool_descriptions}

## 用户消息
{user_message}

## 对话上下文
{memory_context}

请生成执行计划 JSON。记住：只输出 JSON，不要 ``` 代码块标记。"""

# ===========================================================================
#  RE-PLAN TEMPLATE
# ===========================================================================

REPLAN_USER_TEMPLATE = """\
## 原计划
{original_plan}

## 反思意见
{reflection_notes}

## 已执行步骤的结果
{partial_results}

## 可用工具
{tool_descriptions}

请生成修正后的执行计划 JSON。保留原计划中仍有效的步骤，修正或替换有问题的步骤。记住：只输出 JSON，不要 ``` 代码块标记。"""


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class Planner:
    """LLM-based task decomposition engine.

    Parameters
    ----------
    llm_gateway:
        The existing ``LLMGateway`` instance from ``pdf_parser.rag``.
    tool_registry:
        A :class:`ToolRegistry` providing tool schemas for the prompt.
    """

    def __init__(
        self,
        llm_gateway: Any,
        tool_registry: Any,
    ) -> None:
        self._llm = llm_gateway
        self._registry = tool_registry

    async def plan(
        self,
        user_message: str,
        *,
        memory_context: str = "",
        conversation_history: list[dict[str, str]] | None = None,
    ) -> AgentPlan:
        """Generate an execution plan for *user_message*."""
        t0 = time.perf_counter()

        tool_descriptions = self._registry.list_descriptions()
        if not tool_descriptions:
            tool_descriptions = "(no tools available)"

        memory_block = memory_context or "(new user, no history)"

        user_prompt = PLANNER_USER_TEMPLATE.format(
            tool_descriptions=tool_descriptions,
            user_message=user_message,
            memory_context=memory_block,
        )

        raw = await self._call_llm(PLANNER_SYSTEM_PROMPT, user_prompt, conversation_history)
        plan = self._parse_plan(raw, user_message)

        elapsed = time.perf_counter() - t0
        logger.info(
            "Plan generated in %.2fs: intent=%s clarity=%.2f steps=%d",
            elapsed, plan.intent, plan.intent_clarity, len(plan.steps),
        )
        return plan

    async def replan(
        self,
        original_plan: AgentPlan,
        reflection_notes: str,
        *,
        partial_results: str = "",
    ) -> AgentPlan:
        """Re-plan after reflection found gaps."""
        original_json = json.dumps({
            "rewritten_query": original_plan.rewritten_query,
            "intent": original_plan.intent,
            "intent_clarity": original_plan.intent_clarity,
            "steps": [
                {
                    "step_index": s.step_index,
                    "description": s.description,
                    "depends_on": s.depends_on,
                    "actions": [
                        {"tool_name": a.tool_name, "input": a.input, "reason": a.reason}
                        for a in s.actions
                    ],
                }
                for s in original_plan.steps
            ],
        }, ensure_ascii=False, indent=2)

        user_prompt = REPLAN_USER_TEMPLATE.format(
            original_plan=original_json,
            reflection_notes=reflection_notes,
            partial_results=partial_results or "(none)",
            tool_descriptions=self._registry.list_descriptions(),
        )

        raw = await self._call_llm(PLANNER_SYSTEM_PROMPT, user_prompt, None)
        plan = self._parse_plan(raw, original_plan.original_query)

        logger.info("Re-plan complete: %d steps", len(plan.steps))
        return plan

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        history: list[dict[str, str]] | None,
    ) -> str:
        """Call the LLM with planning prompts and return raw text."""
        client = self._llm._get_async_client()

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history[-6:])
        messages.append({"role": "user", "content": user_prompt})

        response = await client.chat.completions.create(
            model=self._llm._light_model,
            messages=messages,
            temperature=0.0,
            max_tokens=2048,
        )
        return response.choices[0].message.content or "{}"

    @staticmethod
    def _parse_plan(raw: str, query: str) -> AgentPlan:
        """Parse LLM output into an AgentPlan, with robust fallback on parse failure."""
        raw = raw.strip()
        # Strip markdown code fences (LLMs sometimes wrap JSON in ```)
        if raw.startswith("```"):
            import re
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse planner JSON, using default plan: %s", raw[:200])
            return AgentPlan(
                original_query=query,
                intent="general",
                intent_clarity=0.5,
                clarifying_question="抱歉，我暂时无法理解您的请求。能否再详细描述一下您的需求？",
            )

        steps: list[AgentStep] = []
        for s in data.get("steps", []):
            actions: list[AgentAction] = []
            for a in s.get("actions", []):
                actions.append(
                    AgentAction(
                        tool_name=a.get("tool_name", ""),
                        input=a.get("input", {}),
                        reason=a.get("reason", ""),
                    )
                )
            if actions:
                steps.append(
                    AgentStep(
                        step_index=s.get("step_index", len(steps)),
                        actions=actions,
                        depends_on=s.get("depends_on", []),
                        description=s.get("description", ""),
                    )
                )

        # Sanity: if intent is supply_chain but no order_lookup in steps, flag it
        intent = data.get("intent", "general")
        if intent == "supply_chain" and steps:
            has_order_tool = any(
                a.tool_name in ("order_lookup", "order_create", "crm_create_return")
                for s in steps for a in s.actions
            )
            if not has_order_tool:
                logger.warning(
                    "Plan intent=supply_chain but no order/crm tools found. "
                    "PlanValidator will audit this."
                )

        return AgentPlan(
            original_query=query,
            rewritten_query=data.get("rewritten_query", query),
            intent=intent,
            intent_clarity=data.get("intent_clarity", 1.0),
            clarifying_question=data.get("clarifying_question"),
            steps=steps,
            final_synthesis_hint=data.get("final_synthesis_hint", ""),
        )
