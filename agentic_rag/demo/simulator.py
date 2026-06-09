"""Demo simulation engine — produces realistic agent traces without requiring a live LLM.

For interview demos, the simulator uses pre-scripted (but realistic) agent plans,
tool results, and final answers.  This guarantees a smooth, predictable demo
that works offline and never fails mid-presentation.

Set ``AGENTIC_RAG_DEMO_MODE=true`` to use simulated responses instead of live LLM.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agentic_rag.models import (
    AgentAction,
    AgentPlan,
    AgentResponse,
    AgentState,
    AgentStep,
    DegradationPolicy,
    ToolCall,
    ToolCallRecord,
    ToolResult,
)
from agentic_rag.demo.mock_data import (
    ALL_PRODUCTS,
    WOMENSWEAR_PRODUCTS,
    COMPETITOR_PRICES,
    KB_ARTICLES,
    OUTDOOR_PRODUCTS,
    MOCK_USERS,
)

logger = logging.getLogger(__name__)


class DemoSimulator:
    """Produces realistic simulated agent traces for interview demos."""

    def __init__(self) -> None:
        self._step_delay = 0.3  # Simulated execution delay per step (seconds)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_still_vague(user_response: str) -> bool:
        """Detect if the user's clarification response is still too vague."""
        vague_markers = [
            "不知道", "不清楚", "不太清楚", "不太确定", "不确定", "我也不确定",
            "随便", "都行", "都可以", "无所谓", "不太了解",
            "不知道啊", "没想好", "再看看", "不知道呢", "不懂", "不明白",
            "idk", "not sure", "dont know",
        ]
        response_lower = user_response.lower().strip()
        # Very short responses are likely vague
        if len(response_lower) <= 3:
            return True
        for marker in vague_markers:
            if marker in response_lower:
                return True
        return False

    @staticmethod
    def _build_best_effort_festival(t0: float, user_msg: str) -> "AgentResponse":
        """Build a Best-Effort Plan after max clarification rounds."""
        plan = AgentPlan(
            original_query=user_msg,
            rewritten_query="户外音乐节装备通用推荐",
            intent="shopping_guide",
            intent_clarity=0.3,
            steps=[
                AgentStep(
                    step_index=0,
                    description="意图不明确，进行通用检索",
                    actions=[
                        AgentAction(
                            tool_name="rag_search",
                            input={"query": "户外音乐节装备通用推荐"},
                            reason="通用知识库检索，不做个性化推荐",
                        ),
                    ],
                ),
            ],
            final_synthesis_hint=(
                "用户意图经多轮澄清仍不明确。基于检索结果给出通用建议，明确提出信息局限性，"
                "主动列出3-5个可能的细分方向邀请用户选择。"
            ),
        )
        return AgentResponse(
            answer="""## 户外音乐节装备通用推荐

由于具体需求暂不明确，以下是适用于大多数户外音乐节场景的通用装备清单：

### 基础装备
- **帐篷**：轻量防水款（300-500元），推荐 NatureHike 2人款
- **防潮垫**：充气款（150-200元），R值3.0以上
- **折叠椅**：便携款（100-200元），1kg以内

### 穿着建议
- 速干衣 + 防晒外套（视季节选择厚度）
- 舒适徒步鞋

### 照明与背包
- 头灯（200-300元）
- 20-30L 日用背包

### 推荐配件包
| 配件 | 价格 | 用途 |
|------|------|------|
| 户外急救包 | ¥79 | 基础应急 |
| 防水袋套装 | ¥59 | 保护电子设备 |
| 便携水袋 2L | ¥49 | 夏季补水 |

> 为了让推荐更精准，你可以告诉我以下任一方向：
> - **按场景**：沙漠音乐节 / 草地音乐节 / 城市音乐节
> - **按季节**：夏季防暑 / 春秋保暖
> - **按预算**：经济型(800-1200) / 舒适型(1500-2500) / 专业型(3000+)
> - **按天数**：当天来回 / 过夜一晚 / 露营多日""",
            state=AgentState.DONE,
            plan=plan,
            elapsed_seconds=round(time.perf_counter() - t0, 3),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def simulate_festival_gear(
        self,
        user_msg: str,
        clarifying_response: str | None = None,
        _clarification_round: int = 0,
    ) -> AgentResponse:
        """Simulate Scenario 1 — Outdoor music festival gear recommendation."""
        t0 = time.perf_counter()

        if clarifying_response is None:
            # First turn: agent detects ambiguity and asks for clarification
            plan = AgentPlan(
                original_query=user_msg,
                rewritten_query="",
                intent="shopping_guide",
                intent_clarity=0.4,
                clarifying_question="好的！户外音乐节需要不少准备呢～您是打算过夜露营，还是当天来回？另外，音乐节在什么季节、在哪个城市？这些会决定推荐什么装备哦。",
            )
            return AgentResponse(
                answer="",
                clarifying_question=plan.clarifying_question,
                state=AgentState.CLARIFYING,
                plan=plan,
                elapsed_seconds=round(time.perf_counter() - t0, 3),
            )

        # After 2 rounds of vague responses -> Best-Effort
        if _clarification_round >= 2:
            return self._build_best_effort_festival(t0, user_msg)

        # Check if user response is STILL vague
        if self._is_still_vague(clarifying_response):
            return AgentResponse(
                answer="",
                clarifying_question=(
                    "抱歉，我还是不太确定您的具体需求。没关系，"
                    "我先根据您之前的描述给您一个通用推荐方案，"
                    "您可以从中挑选感兴趣的方向～"
                ),
                state=AgentState.CLARIFYING,
                plan=AgentPlan(
                    original_query=user_msg,
                    intent="shopping_guide",
                    intent_clarity=0.35,
                    clarifying_question="...",
                ),
                elapsed_seconds=round(time.perf_counter() - t0, 3),
            )

        # Second turn: full plan with tools
        enriched_query = f"夏季杭州户外音乐节，过夜露营两晚，需要全套户外装备推荐。用户偏好：轻量、性价比高。"

        plan = AgentPlan(
            original_query=enriched_query,
            rewritten_query="杭州夏季户外音乐节露营装备推荐，过夜两晚，偏好轻量和性价比",
            intent="shopping_guide",
            intent_clarity=0.95,
            steps=[
                AgentStep(
                    step_index=0,
                    description="搜索知识库中的户外音乐节装备指南",
                    actions=[
                        AgentAction(
                            tool_name="rag_search",
                            input={"query": "户外音乐节装备完全指南 夏季露营"},
                            reason="获取场景化装备推荐知识",
                        ),
                    ],
                ),
                AgentStep(
                    step_index=1,
                    description="并行获取外部信息 + 用户画像",
                    actions=[
                        AgentAction(
                            tool_name="web_search",
                            input={"query": "杭州夏季户外音乐节攻略"},
                            reason="获取实时天气和场地信息",
                        ),
                        AgentAction(
                            tool_name="user_profile",
                            input={"user_id": "user_xiaomei"},
                            reason="获取用户偏好和历史购买",
                        ),
                    ],
                ),
                AgentStep(
                    step_index=2,
                    description="查询可购买的具体商品",
                    depends_on=[0, 1],
                    actions=[
                        AgentAction(
                            tool_name="rag_search",
                            input={"query": "轻量帐篷 折叠椅 速干衣 头灯 户外装备推荐"},
                            reason="检索具体产品推荐",
                        ),
                        AgentAction(
                            tool_name="inventory_check",
                            input={"product_ids": "OUT-001,OUT-002,OUT-003,OUT-004,OUT-005,OUT-006"},
                            reason="批量检查库存状态",
                        ),
                    ],
                ),
            ],
            final_synthesis_hint="综合装备指南、天气信息、用户偏好和库存情况，给出分场景（睡眠、穿着、照明、舒适）的推荐清单，并标注预算。",
        )

        # Simulate tool execution results
        await asyncio.sleep(self._step_delay)
        records = self._build_festival_gear_records()

        # Synthesized answer
        answer = self._build_festival_gear_answer()

        elapsed = time.perf_counter() - t0
        return AgentResponse(
            answer=answer,
            state=AgentState.DONE,
            sources=self._extract_festival_sources(),
            tool_calls_made=records,
            plan=plan,
            elapsed_seconds=round(elapsed, 3),
        )

    async def simulate_womenswear(
        self,
        user_msg: str,
        clarifying_response: str | None = None,
        _clarification_round: int = 0,
    ) -> AgentResponse:
        """Simulate Scenario 2 — Personalized womenswear recommendation."""
        t0 = time.perf_counter()

        if clarifying_response is None:
            plan = AgentPlan(
                original_query=user_msg,
                rewritten_query="",
                intent="recommendation",
                intent_clarity=0.45,
                clarifying_question="职场穿搭选对一件好外套太重要了！为了精准推荐，想了解一下：您偏好什么风格（简约/法式/韩系）？另外大概预算和尺码方便说一下吗？",
            )
            return AgentResponse(
                answer="",
                clarifying_question=plan.clarifying_question,
                state=AgentState.CLARIFYING,
                plan=plan,
                elapsed_seconds=round(time.perf_counter() - t0, 3),
            )

        enriched_query = "预算3000左右，简约风格，职场通勤穿，平时穿M码，想要一件百搭的外套。"

        plan = AgentPlan(
            original_query=enriched_query,
            rewritten_query="简约风格职场女装外套推荐，预算3000元，M码，百搭通勤",
            intent="recommendation",
            intent_clarity=0.92,
            steps=[
                AgentStep(
                    step_index=0,
                    description="加载用户画像 + 搜索女装穿搭指南",
                    actions=[
                        AgentAction(
                            tool_name="user_profile",
                            input={"user_id": "user_xiaoyu"},
                            reason="获取购买历史、偏好品牌和预算",
                        ),
                        AgentAction(
                            tool_name="rag_search",
                            input={"query": "职场女装穿搭指南 简约风 外套 百搭"},
                            reason="获取专业知识",
                        ),
                    ],
                ),
                AgentStep(
                    step_index=1,
                    description="知识图谱多跳检索：女装外套→同类商品→评价",
                    depends_on=[0],
                    actions=[
                        AgentAction(
                            tool_name="knowledge_graph",
                            input={
                                "query": "女士西装外套 简约风",
                                "hops": 3,
                                "relations": ["SIMILAR_TO", "REVIEWED", "PURCHASED"],
                            },
                            reason="KG多跳：找到外套→同类商品→用户评价链条",
                        ),
                    ],
                ),
                AgentStep(
                    step_index=2,
                    description="检查库存 + 搜真实评价",
                    depends_on=[1],
                    actions=[
                        AgentAction(
                            tool_name="inventory_check",
                            input={"product_ids": "WOM-001,WOM-003,WOM-004"},
                            reason="确保推荐商品有货",
                        ),
                        AgentAction(
                            tool_name="web_search",
                            input={"query": "Theory西装 Maje西裤 COS连衣裙 通勤女装 真实评价 2026"},
                            reason="获取真实用户的购买体验",
                        ),
                    ],
                ),
            ],
            final_synthesis_hint="结合用户画像（简约风、预算3000、M码、职场通勤）、KG推理结果和库存，给出3件核心单品+配套推荐，说明每款的搭配思路。",
        )

        await asyncio.sleep(self._step_delay)
        records = self._build_womenswear_records()

        answer = self._build_womenswear_answer()

        elapsed = time.perf_counter() - t0
        return AgentResponse(
            answer=answer,
            state=AgentState.DONE,
            sources=self._extract_womenswear_sources(),
            tool_calls_made=records,
            plan=plan,
            elapsed_seconds=round(elapsed, 3),
        )

    async def simulate_competitor_analysis(self, user_msg: str) -> AgentResponse:
        """Simulate Scenario 3 — Competitor pricing analysis for women's clothing."""
        t0 = time.perf_counter()

        plan = AgentPlan(
            original_query=user_msg,
            rewritten_query="分析Theory羊毛混纺西装外套在主要电商平台的竞品定价，给出调价建议",
            intent="operations",
            intent_clarity=0.90,
            steps=[
                AgentStep(
                    step_index=0,
                    description="获取内部定价 + 竞品数据",
                    actions=[
                        AgentAction(
                            tool_name="price_analysis",
                            input={"product_id": "WOM-001", "platforms": ["天猫", "京东", "抖音", "拼多多"]},
                            reason="获取各平台实时竞品价格",
                        ),
                        AgentAction(
                            tool_name="rag_search",
                            input={"query": "女装外套市场定价策略 2026"},
                            reason="获取定价策略知识",
                        ),
                    ],
                ),
                AgentStep(
                    step_index=1,
                    description="综合分析并生成建议",
                    depends_on=[0],
                    actions=[
                        AgentAction(
                            tool_name="web_search",
                            input={"query": "Theory西装外套 2026年价格走势 女装"},
                            reason="了解价格趋势",
                        ),
                    ],
                ),
            ],
            final_synthesis_hint="列出各平台价格对比表，计算市场中位数，给出调价建议（含调整幅度和理由）。",
        )

        await asyncio.sleep(self._step_delay)
        records = self._build_competitor_records()

        answer = self._build_competitor_answer()
        elapsed = time.perf_counter() - t0

        return AgentResponse(
            answer=answer,
            state=AgentState.DONE,
            sources=[],
            tool_calls_made=records,
            plan=plan,
            elapsed_seconds=round(elapsed, 3),
        )

    async def simulate_return_order(self, user_msg: str, clarifying_response: str | None = None, _clarification_round: int = 0) -> AgentResponse:
        """Simulate Scenario 4 — Return/refund processing with Feishu integration."""
        t0 = time.perf_counter()

        if clarifying_response is None:
            plan = AgentPlan(
                original_query=user_msg,
                rewritten_query="",
                intent="supply_chain",
                intent_clarity=0.55,
                clarifying_question=(
                    "很抱歉给您带来不便！在为您处理退货之前，请先选择遇到的问题类型，"
                    "这将帮助我们更快地为您解决："
                ),
            )
            return AgentResponse(
                answer="",
                clarifying_question=plan.clarifying_question,
                state=AgentState.CLARIFYING,
                plan=plan,
                elapsed_seconds=round(time.perf_counter() - t0, 3),
            )

        # Parse the user's selected reason
        reason = clarifying_response
        clothing_type = "女装外套"

        enriched_query = f"订单#ORD-20260501-001，{clothing_type}，原因：{reason}，要求退货退款"

        plan = AgentPlan(
            original_query=enriched_query,
            rewritten_query=f"订单ORD-20260501-001退货处理：{clothing_type}，{reason}",
            intent="supply_chain",
            intent_clarity=0.93,
            steps=[
                AgentStep(
                    step_index=0,
                    description="查询订单详情 + 退货政策",
                    actions=[
                        AgentAction(
                            tool_name="order_lookup",
                            input={"order_id": "ORD-20260501-001"},
                            reason="验证订单信息和商品状态",
                        ),
                        AgentAction(
                            tool_name="rag_search",
                            input={"query": "退换货政策 质量问题 退货流程"},
                            reason="获取退换货规则",
                        ),
                    ],
                ),
                AgentStep(
                    step_index=1,
                    description="创建退货工单 + 触发飞书通知",
                    depends_on=[0],
                    actions=[
                        AgentAction(
                            tool_name="crm_create_return",
                            input={
                                "order_id": "ORD-20260501-001",
                                "reason": reason,
                                "item_ids": ["WOM-001"],
                            },
                            reason="创建退货RMA工单",
                        ),
                    ],
                ),
            ],
            final_synthesis_hint="告知用户退货流程进展：RMA编号、退货地址、预计退款时间线。同时已通过飞书群通知客服团队处理。",
        )

        await asyncio.sleep(self._step_delay)
        records = self._build_return_records_with_reason(reason)

        # ---- Feishu integration ----
        rma_id = f"RMA-{int(time.time())}"
        feishu_result = await self._push_to_feishu(
            clothing_type=clothing_type,
            reason=reason,
            order_id="ORD-20260501-001",
            rma_id=rma_id,
        )

        answer = self._build_return_answer_with_reason(reason, rma_id, feishu_result)

        elapsed = time.perf_counter() - t0
        return AgentResponse(
            answer=answer,
            state=AgentState.DONE,
            sources=[],
            tool_calls_made=records,
            plan=plan,
            elapsed_seconds=round(elapsed, 3),
        )

    @staticmethod
    async def _push_to_feishu(
        clothing_type: str,
        reason: str,
        order_id: str,
        rma_id: str,
    ) -> dict[str, Any]:
        """Push notification to Feishu group + add Bitable record."""
        from agentic_rag.config import get_settings
        from agentic_rag.integrations.feishu import get_bot_client, get_bitable_client

        settings = get_settings()
        bot = get_bot_client(webhook_url=settings.feishu_webhook_url)
        bitable = get_bitable_client(app_token=settings.feishu_bitable_app_token)

        # 1. Send card notification to Feishu group
        await bot.send_return_notification(
            clothing_type=clothing_type,
            return_reason=reason,
            order_id=order_id,
            rma_id=rma_id,
            customer_note=f"用户反馈：{reason}",
        )

        # 2. Add row to Bitable (多维表格)
        record = await bitable.add_return_record(
            clothing_type=clothing_type,
            return_reason=reason,
            order_id=order_id,
            rma_id=rma_id,
            customer_note=f"用户反馈：{reason}",
        )

        pending = bitable.get_pending_count()

        return {
            "bot_sent": True,
            "bitable_record_id": record["record_id"],
            "pending_returns": pending,
            "rma_id": rma_id,
        }

    @staticmethod
    def _build_return_records_with_reason(reason: str) -> list[ToolCallRecord]:
        return [
            ToolCallRecord(
                tool_name="order_lookup", input={"order_id": "ORD-20260501-001"},
                result=ToolResult(tool_name="order_lookup", status="success",
                    summary="订单#ORD-20260501-001：Theory西装外套×1，¥2899，2026-04-15下单，已签收。状态：已完成。",
                    structured_data={"order_id": "ORD-20260501-001", "product": "Theory羊毛西装外套", "price": 2899, "status": "已签收", "date": "2026-04-15"},
                    elapsed_ms=140.0),
                step_index=0, idempotency_key="sim_r_step0_order"),
            ToolCallRecord(
                tool_name="rag_search", input={"query": "退换货政策"},
                result=ToolResult(tool_name="rag_search", status="success",
                    summary=KB_ARTICLES["return_policy"][:300],
                    structured_data={"answer": KB_ARTICLES["return_policy"]},
                    elapsed_ms=200.0),
                step_index=0, idempotency_key="sim_r_step0_rag"),
            ToolCallRecord(
                tool_name="crm_create_return", input={"order_id": "ORD-20260501-001", "reason": reason},
                result=ToolResult(tool_name="crm_create_return", status="success",
                    summary=f"RMA退货工单已创建。退货原因：{reason}。退货地址：杭州市余杭区仓前物流园3号仓。预计退款：3-5个工作日到账。",
                    structured_data={"rma_id": f"RMA-{int(time.time())}", "status": "approved", "return_address": "杭州市余杭区仓前物流园3号仓", "refund_estimate": "3-5个工作日"},
                    elapsed_ms=380.0),
                step_index=1, idempotency_key="sim_r_step1_crm"),
        ]

    @staticmethod
    def _build_return_answer_with_reason(reason: str, rma_id: str, feishu: dict[str, Any]) -> str:
        reason_labels = {
            "质量问题": "线头/起球/脱线/拉链故障等",
            "版型问题": "肩宽/袖长/衣长不合适",
            "色差问题": "实物颜色与商品图差异较大",
            "面料问题": "手感粗糙/起静电/不透气",
            "尺码不符": "偏大或偏小，M码与标注S-XL范围不符",
            "其他问题": "其他原因",
        }
        detail = reason_labels.get(reason, reason)

        pending = feishu.get("pending_returns", 0)

        return f"""## 退货处理已受理

### 退货信息
| 项目 | 详情 |
|------|------|
| 订单号 | ORD-20260501-001 |
| 商品 | Theory 羊毛混纺西装外套 (¥2,899) |
| 反馈原因 | **{reason}** — {detail} |
| RMA编号 | **{rma_id}** |
| 退货地址 | 杭州市余杭区仓前物流园3号仓 |
| 预计退款 | 3-5 个工作日到账 |
| 处理状态 | 待处理 |

### 后续步骤
1. **明天上午**将商品原包装准备好（含吊牌和赠品）
2. 等待顺丰快递员上门取件（取件码将短信通知）
3. 仓库验收通过后，退款自动退回原支付方式

---

### 飞书通知
- 已推送退货通知到**客服群聊**
- 已添加记录到**飞书多维表格**（当前待处理退货：{pending} 件）
- 客服团队将在 2 小时内跟进处理

> 有任何问题可随时回复 **{rma_id}** 查询进度！"""

    # ==================================================================
    # Scenario: Negative Review Monitoring (差评监控)
    # ==================================================================

    REVIEW_REASONS: dict[str, str] = {
        "质量问题": "线头/脱线/起球/拉链故障/扣子脱落",
        "版型问题": "肩宽不合适/袖长偏短/衣长偏长/腰身不贴合",
        "色差问题": "实物颜色与商品图差异较大/偏深/偏浅",
        "面料问题": "手感粗糙/不透气/易起皱/起静电/面料偏薄",
        "尺码问题": "偏大/偏小/与标注尺码表不符",
        "物流问题": "包装破损/物流太慢/少发漏发",
        "客服问题": "回复不及时/态度差/处理方案不满意",
        "其他问题": "其他原因/未明确说明",
    }

    async def simulate_negative_review(
        self,
        review_text: str | None = None,
        selected_reason: str | None = None,
    ) -> AgentResponse:
        """Simulate a negative review monitoring scenario.

        Flow:
        1. Customer leaves a review (≤3 stars = negative review)
        2. System classifies the complaint reason
        3. Pushes alert to Feishu group
        4. Adds record to Feishu Bitable (多维表格)
        5. Returns resolution plan
        """
        t0 = time.perf_counter()

        if review_text is None:
            plan = AgentPlan(
                original_query="差评监控",
                rewritten_query="",
                intent="operations",
                intent_clarity=0.5,
                clarifying_question="请选择一条需要处理的差评样例，或输入自定义评价内容：",
            )
            return AgentResponse(
                answer="",
                clarifying_question=plan.clarifying_question,
                state=AgentState.CLARIFYING,
                plan=plan,
                elapsed_seconds=round(time.perf_counter() - t0, 3),
            )

        if selected_reason is None:
            selected_reason = self._classify_review_reason(review_text)

        stars = 2
        if "1星" in review_text or "一星" in review_text:
            stars = 1
        elif "3星" in review_text or "三星" in review_text:
            stars = 3

        plan = AgentPlan(
            original_query=review_text,
            rewritten_query=f"差评监控：{stars}星差评，原因={selected_reason}",
            intent="operations",
            intent_clarity=0.90,
            steps=[
                AgentStep(step_index=0, description="解析评价 + 分类差评原因",
                    actions=[AgentAction(tool_name="rag_search", input={"query": f"差评处理 {selected_reason}"}, reason="获取处理SOP")]),
                AgentStep(step_index=1, description="推送飞书 + 多维表格",
                    depends_on=[0],
                    actions=[AgentAction(tool_name="crm_create_return", input={"review": review_text[:100], "reason": selected_reason}, reason="创建差评工单")]),
            ],
            final_synthesis_hint=f"{stars}星差评，归类'{selected_reason}'。已推送飞书通知客服跟进。",
        )

        await asyncio.sleep(self._step_delay)
        records = self._build_review_records(review_text, selected_reason, stars)
        feishu_result = await self._push_review_to_feishu(review_text=review_text, reason=selected_reason, stars=stars)
        answer = self._build_review_answer(review_text=review_text, reason=selected_reason, stars=stars, feishu_result=feishu_result)

        elapsed = time.perf_counter() - t0
        return AgentResponse(
            answer=answer, state=AgentState.DONE, sources=[],
            tool_calls_made=records, plan=plan,
            elapsed_seconds=round(elapsed, 3),
        )

    @staticmethod
    def _classify_review_reason(text: str) -> str:
        keyword_map = {
            "质量问题": ["线头", "脱线", "起球", "拉链", "扣子", "掉色", "褪色", "破损", "烂", "坏了", "瑕疵"],
            "版型问题": ["肩宽", "袖长", "衣长", "腰身", "版型", "不合身", "偏紧", "偏松", "袖子"],
            "色差问题": ["色差", "颜色", "偏深", "偏浅", "图片", "照片"],
            "面料问题": ["面料", "手感", "粗糙", "透气", "起皱", "静电", "薄", "厚", "扎人", "刺"],
            "尺码问题": ["尺码", "偏大", "偏小", "M码", "L码", "XL", "大小"],
            "物流问题": ["物流", "快递", "包装", "慢", "破损", "少发", "漏发", "发错"],
            "客服问题": ["客服", "态度", "回复", "处理"],
        }
        for reason, keywords in keyword_map.items():
            for kw in keywords:
                if kw in text:
                    return reason
        return "其他问题"

    @staticmethod
    def _build_review_records(text: str, reason: str, stars: int) -> list[ToolCallRecord]:
        return [
            ToolCallRecord(tool_name="rag_search", input={"query": f"差评处理 {reason}"},
                result=ToolResult(tool_name="rag_search", status="success",
                    summary=f"差评SOP：{reason}→核实→联系客户→补偿→复盘→回复。", structured_data={}, elapsed_ms=180.0),
                step_index=0, idempotency_key="sim_rev_rag"),
            ToolCallRecord(tool_name="crm_create_return", input={"review": text[:80], "reason": reason},
                result=ToolResult(tool_name="crm_create_return", status="success",
                    summary=f"差评工单：{stars}星，{reason}。已分配客服一组。",
                    structured_data={"task_id": f"REV-{int(time.time())}", "assigned": "客服一组", "priority": "高" if stars <= 2 else "中"}, elapsed_ms=250.0),
                step_index=1, idempotency_key="sim_rev_crm"),
        ]

    @staticmethod
    async def _push_review_to_feishu(review_text: str, reason: str, stars: int) -> dict[str, Any]:
        from agentic_rag.config import get_settings
        from agentic_rag.integrations.feishu import get_bot_client, get_bitable_client
        settings = get_settings()
        bot = get_bot_client(webhook_url=settings.feishu_webhook_url)
        bitable = get_bitable_client(app_token=settings.feishu_bitable_app_token)
        clothing_type = "女装外套"
        if "衬衫" in review_text: clothing_type = "女装衬衫"
        elif "裤" in review_text: clothing_type = "女装西裤"
        elif "裙" in review_text: clothing_type = "连衣裙"
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        await bot.send_return_notification(
            clothing_type=clothing_type, return_reason=reason,
            order_id=f"REV-{stars}星差评", rma_id=f"REV-{int(time.time())}",
            customer_note=f"[{stars}星差评] {review_text[:100]}",
            stars=stars, review_time=now,
        )
        record = await bitable.add_return_record(
            clothing_type=clothing_type, return_reason=reason,
            order_id=f"REV-{stars}星差评", rma_id=f"REV-{int(time.time())}",
            customer_note=f"[{stars}星差评] {review_text[:150]}",
            stars=stars, review_time=now,
        )
        return {"bot_sent": True, "bitable_record_id": record["record_id"], "pending_reviews": bitable.get_pending_count()}

    @staticmethod
    def _build_review_answer(review_text: str, reason: str, stars: int, feishu_result: dict[str, Any]) -> str:
        star_display = {1: "★☆☆☆☆", 2: "★★☆☆☆", 3: "★★★☆☆"}
        detail = DemoSimulator.REVIEW_REASONS.get(reason, reason)
        pending = feishu_result.get("pending_reviews", 0)
        priority = "高优先级" if stars <= 2 else "中优先级"
        return f"""## 差评监控 — 自动处理完成

### 差评信息
| 项目 | 详情 |
|------|------|
| 评分 | {star_display.get(stars, '★★★☆☆')} ({stars}星)
| 商品 | Theory 羊毛混纺西装外套 |
| 差评原因 | **{reason}** — {detail} |
| 优先级 | {priority} |
| 处理状态 | 待处理 |

### 差评原文
> {review_text}

---

### 飞书通知
- 已推送差评提醒到**客服群聊**（2小时内跟进）
- 已添加记录到**飞书多维表格**（当前待处理差评：{pending} 条）

### 建议处理方案
1. 客服在 2 小时内联系客户致歉 + 了解详情
2. 根据 {reason} 类型执行对应补偿方案
3. 内部复盘：检查同批次商品是否存在类似问题
4. 处理完成后更新多维表格状态为「已处理」

> 差评处理完成后，建议在商品评价下做出公开回复。"""

    # ------------------------------------------------------------------
    # Generic conversation handler (custom input, multi-turn, with memory)
    # ------------------------------------------------------------------

    async def simulate_conversation(
        self,
        user_msg: str,
        *,
        history: list[dict[str, str]] | None = None,
        round_num: int = 0,
    ) -> AgentResponse:
        """Handle generic e-commerce conversation with multi-turn memory.

        Flow:
        1. If the message is clearly about a product category -> try to recommend
        2. If vague -> ask clarifying question
        3. After 2-3 rounds of vagueness -> Best-Effort generic answer
        4. If specific enough -> simulate a product search + recommendation
        """
        t0 = time.perf_counter()
        h = history or []
        turn_count = len([m for m in h if m["role"] == "user"]) + 1

        # Build conversation context summary
        context = ""
        if h:
            recent = h[-6:]  # Last 3 exchanges
            context = " | ".join(
                f"{'U' if m['role']=='user' else 'A'}: {m['content'][:60]}"
                for m in recent
            )

        # Detect product category from message + history
        category = self._detect_category(user_msg, context)

        # --- Case 1: Clear product intent -> simulate recommendation ---
        if category and round_num < 2 and not self._is_still_vague(user_msg):
            return self._build_category_recommendation(t0, user_msg, category, turn_count)

        # --- Case 2: Vague, within clarification budget -> ask ---
        if round_num < 2 and (self._is_still_vague(user_msg) or len(user_msg.strip()) < 5):
            return self._build_clarifying_question(t0, user_msg, round_num, turn_count)

        # --- Case 3: Vague, exhausted rounds -> Best-Effort ---
        if round_num >= 2:
            return self._build_conversation_best_effort(t0, user_msg, turn_count)

        # --- Case 4: Default -> generic response ---
        return self._build_category_recommendation(t0, user_msg, category or "general", turn_count)

    @staticmethod
    def _detect_category(msg: str, context: str = "") -> str | None:
        """Detect product category from the user's message + conversation context."""
        combined = (msg + " " + context).lower()
        categories = {
            "coffee": ["咖啡", "咖啡机", "拿铁", "拉花", "espresso", "手冲", "半自动", "全自动", "coffee", "德龙", "铂富", "breville"],
            "outdoor": ["户外", "音乐节", "露营", "徒步", "帐篷", "登山", "骑行", "跑步", "outdoor", "camping", "festival"],
            "electronics": ["手机", "电脑", "耳机", "相机", "平板", "电视", "音箱", "laptop", "phone"],
            "home": ["家具", "厨具", "灯具", "收纳", "装饰", "冰箱", "洗衣机", "furniture"],
            "fashion": ["衣服", "女装", "裙子", "连衣裙", "西装", "外套", "衬衫", "西裤", "通勤", "职场", "穿搭", "鞋", "包", "手表", "眼镜", "首饰", "运动", "跑步", "shoes", "bag", "watch", "cloth", "dress"],
            "skincare": ["护肤", "化妆", "面膜", "精华", "口红", "防晒", "skincare", "makeup"],
            "return": ["退货", "退款", "退换", "return", "refund", "订单", "order"],
            "pricing": ["竞品", "定价", "价格", "比价", "price", "competitor", "降价", "促销"],
            "food": ["零食", "饮料", "咖啡豆", "茶叶", "牛奶", "巧克力", "food", "snack"],
            "baby": ["母婴", "奶粉", "尿布", "玩具", "童装", "baby", "kids"],
        }
        for cat, keywords in categories.items():
            for kw in keywords:
                if kw in combined:
                    return cat
        if len(msg.strip()) >= 8:
            return "general"
        return None

    @staticmethod
    def _build_clarifying_question(t0: float, user_msg: str, round_num: int, turn: int) -> AgentResponse:
        """Build a clarifying question based on conversation round."""
        questions = [
            "能再详细说说您的需求吗？比如您对品牌、预算、使用场景有什么想法？",
            "我大概理解了方向，但还需要一点信息～您最看重的是性价比、品牌、还是功能体验？另外预算方面有什么考虑吗？",
            "好的，让我根据目前的信息给您一些通用建议。",
        ]
        q = questions[min(round_num, len(questions) - 1)]

        plan = AgentPlan(
            original_query=user_msg,
            intent="general",
            intent_clarity=0.3 + round_num * 0.05,
            clarifying_question=q,
        )
        return AgentResponse(
            answer="",
            clarifying_question=q,
            state=AgentState.CLARIFYING,
            plan=plan,
            clarification_round=round_num,
            elapsed_seconds=round(time.perf_counter() - t0, 3),
        )

    @staticmethod
    def _build_category_recommendation(t0: float, user_msg: str, category: str, turn: int) -> AgentResponse:
        """Build a simulated product recommendation with topic branches and complementary items."""
        catalog = {
            "coffee": {
                "products": [
                    ("德龙 EC685 半自动咖啡机", "¥1,299", "15Bar泵压，手动蒸汽棒，学习拉花入门首选"),
                    ("Breville BES870 半自动咖啡机", "¥3,299", "一体式磨豆+萃取，专业蒸汽棒，进阶拉花必备"),
                    ("百胜图 Mini 半自动咖啡机", "¥799", "20Bar泵压，入门性价比之选"),
                ],
                "complementary": [
                    ("意式拼配咖啡豆 1kg", "¥89", "中深烘焙，油脂丰富，适合拿铁"),
                    ("不锈钢奶缸 600ml", "¥49", "拉花练习必备，尖嘴设计"),
                    ("咖啡机清洁套装", "¥69", "除垢剂+清洁刷+ microfiber布"),
                    ("精密压粉器 58mm", "¥129", "恒压设计，萃取更均匀"),
                ],
                "branches": ["入门 vs 进阶怎么选", "半自动 vs 全自动对比", "拉花新手必备配件清单", "￥1000以内高性价比推荐"],
            },
            "outdoor": {
                "products": [
                    ("NatureHike 轻量防水帐篷 2人款", "¥399", "1.8kg超轻，3000mm防水，音乐节露营必备"),
                    ("探路者 户外折叠椅 便携款", "¥159", "0.9kg便携，承重120kg"),
                    ("Black Diamond 头灯 Spot 400", "¥298", "400流明，IPX8防水"),
                ],
                "complementary": [
                    ("迪卡侬 UPF50+ 速干衣", "¥129", "夏季户外防晒必备，透气快干"),
                    ("挪客 充气防潮垫 单人款", "¥189", "R值3.5，6.5cm厚，露营舒适保障"),
                    ("Osprey Daylite Plus 20L 背包", "¥499", "多隔层设计，水袋仓"),
                    ("户外急救包 基础版", "¥79", "创可贴/消毒/绷带/应急毯"),
                ],
                "branches": ["露营过夜 vs 当天来回怎么选", "夏季防晒装备清单", "轻量化 vs 舒适型装备对比", "￥1500以内全套露营方案"],
            },
            "electronics": {
                "products": [
                    ("Sony WH-1000XM5 降噪耳机", "¥2,499", "行业领先降噪，30小时续航，触控操作"),
                    ("Apple AirPods Pro 2", "¥1,899", "H2芯片，自适应降噪，空间音频"),
                    ("华为 FreeBuds Pro 3", "¥1,499", "星闪连接，静谧通话，鸿蒙生态"),
                    ("Bose QC45 降噪耳机", "¥2,299", "舒适佩戴，11级降噪可调"),
                ],
                "complementary": [
                    ("耳机收纳盒 硬壳款", "¥49", "防压防摔，便携挂钩"),
                    ("蓝牙接收器 5.3", "¥99", "老旧设备升级蓝牙"),
                    ("Type-C 充电线 1.5m", "¥29", "快充兼容，编织材质"),
                ],
                "branches": ["降噪 vs 音质怎么选", "运动耳机 vs 通勤耳机", "入耳 vs 头戴对比", "￥1000以内高性价比推荐"],
            },
            "fashion": {
                "products": [
                    ("Nike Air Max 270 运动鞋", "¥899", "大气垫缓震，日常穿搭+轻度运动"),
                    ("Adidas Ultraboost 23 跑鞋", "¥1,099", "Boost中底，回弹出色"),
                    ("安踏 C202 碳板跑鞋", "¥599", "国产性价比之选，碳板推进"),
                ],
                "complementary": [
                    ("运动袜 5双装", "¥49", "吸汗防臭，加厚毛巾底"),
                    ("鞋类防水喷雾", "¥39", "纳米防水，不伤材质"),
                ],
                "branches": ["通勤 vs 运动怎么选", "国产品牌 vs 国际大牌", "宽脚/扁平足怎么选鞋"],
            },
            "general": {
                "products": [
                    ("通用推荐", "价格不等", "请提供更多细节以获得精准推荐"),
                ],
                "complementary": [],
                "branches": ["告诉我你感兴趣的商品品类", "设定你的预算范围", "描述你的使用场景"],
            },
        }

        cat_data = catalog.get(category, catalog["general"])
        items = cat_data["products"]
        complements = cat_data.get("complementary", [])
        branches = cat_data.get("branches", [])

        items_text = "\n".join(
            f"| {name} | {price} | {desc} |" for name, price, desc in items
        )

        comp_text = ""
        if complements:
            comp_lines = "\n".join(
                f"| {name} | {price} | {desc} |" for name, price, desc in complements
            )
            comp_text = f"""\n### 配套推荐（一站式配齐）\n
| 配件 | 价格 | 推荐理由 |
|------|------|----------|
{comp_lines}
"""

        branch_text = ""
        if branches:
            branch_lines = "\n".join(
                f"- **{b}**" for b in branches
            )
            branch_text = f"""\n### 想深入了解？\n
{branch_lines}

> 点击上方话题或直接在下方输入框提问，我会继续为你详细解答。"""

        answer = f"""## 根据您的需求推荐 —— {category}

经过分析您的描述和对话上下文，为您推荐以下商品：

### 核心推荐
| 商品 | 价格 | 推荐理由 |
|------|------|----------|
{items_text}
{comp_text}
{branch_text}

> 第 {turn} 轮对话。系统已记住您之前的偏好。可以继续问"有没有更便宜的"、"对比前两个"等。"""

        plan = AgentPlan(
            original_query=user_msg,
            rewritten_query=f"推荐{category}品类商品",
            intent="recommendation",
            intent_clarity=0.85,
            steps=[
                AgentStep(step_index=0, description="搜索相关品类商品+配套推荐",
                    actions=[AgentAction(tool_name="rag_search", input={"query": f"{category} 推荐 配件"}, reason="品类检索+配套")]),
            ],
        )
        return AgentResponse(
            answer=answer,
            state=AgentState.DONE,
            plan=plan,
            elapsed_seconds=round(time.perf_counter() - t0, 3),
        )

    @staticmethod
    def _build_conversation_best_effort(t0: float, user_msg: str, turn: int) -> AgentResponse:
        """Build a Best-Effort fallback after multiple vague rounds."""
        plan = AgentPlan(
            original_query=user_msg,
            intent="general",
            intent_clarity=0.3,
            steps=[
                AgentStep(step_index=0, description="通用检索",
                    actions=[AgentAction(tool_name="rag_search", input={"query": user_msg}, reason="通用检索")]),
            ],
            final_synthesis_hint="多轮澄清后仍不明确，给出通用建议并邀请用户选择方向。",
        )
        answer = f"""## 通用推荐

经过 {turn} 轮交流，您的需求目前还不太明确。为了让推荐更精准，以下是一些热门方向供您选择：

### 热门品类速览
| 品类 | 代表商品 | 价格区间 |
|------|----------|----------|
| ☕ 咖啡机 | 德龙 EC685 / Breville BES870 | ¥799-¥3,299 |
| 👗 女装外套 | Theory / ICICLE / Massimo Dutti | ¥1,790-¥3,990 |
| 🏕 户外装备 | NatureHike 帐篷 / BD 头灯 | ¥159-¥499 |
| 🎧 蓝牙耳机 | AirPods Pro / Sony XM5 | ¥1,499-¥2,499 |
| 👟 运动鞋 | Nike / Adidas / 安踏 | ¥599-¥1,099 |

### 想深入了解？
- **按使用场景选** — 通勤/运动/办公/送礼
- **按性价比选** — 百元入门 / 千元进阶 / 旗舰体验
- **按人气排行选** — 销量 Top 10 / 好评 Top 10
- **按品牌选** — 国际大牌 / 国产新锐 / 专业小众

> 直接在下方输入框提问，如「推荐蓝牙耳机 通勤用 预算1500」或「对比 AirPods 和 Sony」，我会立刻给出精准推荐！"""

        return AgentResponse(
            answer=answer,
            state=AgentState.DONE,
            plan=plan,
            elapsed_seconds=round(time.perf_counter() - t0, 3),
        )

    # ------------------------------------------------------------------
    # Record builders (simulated tool outputs)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_festival_gear_records() -> list[ToolCallRecord]:
        return [
            ToolCallRecord(
                tool_name="rag_search",
                input={"query": "户外音乐节装备完全指南 夏季露营"},
                result=ToolResult(
                    tool_name="rag_search",
                    status="success",
                    summary=KB_ARTICLES["outdoor_festival_guide"][:500],
                    structured_data={
                        "answer": KB_ARTICLES["outdoor_festival_guide"],
                        "sources": [{"chunk_id": "kb_001", "file_id": "outdoor_guide", "text": KB_ARTICLES["outdoor_festival_guide"][:500]}],
                    },
                    elapsed_ms=245.0,
                ),
                step_index=0,
                idempotency_key="sim_step0_rag",
            ),
            ToolCallRecord(
                tool_name="web_search",
                input={"query": "杭州夏季户外音乐节攻略"},
                result=ToolResult(
                    tool_name="web_search",
                    status="success",
                    summary="杭州夏季气温28-35°C，潮湿多雨。建议携带轻量防水装备。西湖音乐节场地为草地，夜间温度降至22°C。",
                    structured_data={"summary": "杭州夏季户外音乐节：气温28-35°C，多雨潮湿，夜间22°C。场地草地。"},
                    elapsed_ms=380.0,
                ),
                step_index=1,
                idempotency_key="sim_step1_web",
            ),
            ToolCallRecord(
                tool_name="user_profile",
                input={"user_id": "user_xiaomei"},
                result=ToolResult(
                    tool_name="user_profile",
                    status="success",
                    summary="小美：户外爱好者，偏好迪卡侬和NatureHike品牌，历史购买登山鞋和睡袋，预算800-2000元。",
                    structured_data=MOCK_USERS["user_xiaomei"].preferences,
                    elapsed_ms=120.0,
                ),
                step_index=1,
                idempotency_key="sim_step1_profile",
            ),
            ToolCallRecord(
                tool_name="rag_search",
                input={"query": "轻量帐篷 折叠椅 速干衣 头灯 户外装备推荐"},
                result=ToolResult(
                    tool_name="rag_search",
                    status="success",
                    summary="推荐：NatureHike轻量帐篷(399)、探路者折叠椅(159)、迪卡侬速干衣(129)、BD头灯(298)、Osprey背包(499)、挪客防潮垫(189)",
                    structured_data={
                        "answer": "核心推荐：帐篷NatureHike 399元、折叠椅探路者159元、速干衣迪卡侬129元、头灯BD 298元、背包Osprey 499元、防潮垫挪客189元。总预算约1673元。",
                        "sources": [],
                    },
                    elapsed_ms=290.0,
                ),
                step_index=2,
                idempotency_key="sim_step2_rag",
            ),
            ToolCallRecord(
                tool_name="inventory_check",
                input={"product_ids": "OUT-001,OUT-002,OUT-003,OUT-004,OUT-005,OUT-006"},
                result=ToolResult(
                    tool_name="inventory_check",
                    status="success",
                    summary="全部有货：OUT-001(45件)、OUT-002(120件)、OUT-003(200件)、OUT-004(75件)、OUT-005(60件)、OUT-006(90件)",
                    structured_data={p.id: p.stock for p in OUTDOOR_PRODUCTS},
                    elapsed_ms=150.0,
                ),
                step_index=2,
                idempotency_key="sim_step2_inventory",
            ),
        ]

    @staticmethod
    def _build_festival_gear_answer() -> str:
        return """## 🎸 杭州夏季音乐节露营装备推荐

根据你的需求（杭州夏季、过夜露营两晚、偏好轻量性价比），为你定制了以下装备方案：

---

### 🏕️ 睡眠系统（核心装备）
| 商品 | 价格 | 推荐理由 |
|------|------|----------|
| **NatureHike 轻量防水帐篷** | ¥399 | 1.8kg超轻、3000mm防水，夏季阵雨无忧 |
| **挪客 充气防潮垫** | ¥189 | R值3.5、6.5cm厚，露营舒适保障 |

### 👕 穿着防护
| 商品 | 价格 | 推荐理由 |
|------|------|----------|
| **迪卡侬 UPF50+速干衣** | ¥129 | 杭州夏季必备防晒，透气快干 |

### 💡 照明与舒适
| 商品 | 价格 | 推荐理由 |
|------|------|----------|
| **Black Diamond 头灯** | ¥298 | 400流明亮度、IPX8防水，夜间活动必备 |
| **探路者 折叠椅** | ¥159 | 0.9kg便携，演出间隙舒适休息 |
| **Osprey Daylite 20L** | ¥499 | 日常背负舒适，分区收纳合理 |

---

### 💰 总预算：¥1,673（在你 800-2000 元预算范围内）

> ⚠️ **温馨提示**：杭州夏季多雨，建议额外准备防水袋保护电子设备。夜间温度约22°C，带一件薄外套即可。

> ✅ 以上商品全部有库存，可以直接下单。

---

### 🎁 配套推荐（一站式露营方案）
| 配件 | 价格 | 用途 |
|------|------|------|
| 户外急救包 基础版 | ¥79 | 创可贴/消毒/应急毯 |
| 防水袋套装 3件 | ¥59 | 保护手机/充电宝 |
| 便携水袋 2L | ¥49 | 夏季补水必备 |
| 驱蚊喷雾 50ml | ¥29 | 户外夜晚防蚊 |
| **露营总预算** | **¥1,889** | 全部装备+配件 |"""

    @staticmethod
    def _extract_festival_sources() -> list[dict[str, Any]]:
        return [
            {"chunk_id": "kb_001", "file_id": "outdoor_guide", "text": KB_ARTICLES["outdoor_festival_guide"][:300]},
        ]

    @staticmethod
    def _build_womenswear_records() -> list[ToolCallRecord]:
        return [
            ToolCallRecord(
                tool_name="user_profile", input={"user_id": "user_xiaoyu"},
                result=ToolResult(tool_name="user_profile", status="success",
                    summary="小雨：简约通勤风，已购COS羊绒衫、乐福鞋、Longchamp包。预算2000-5000元。搜索过'职场穿搭 女装''西装外套 百搭 品牌''通勤连衣裙推荐'。",
                    structured_data=MOCK_USERS["user_xiaoyu"].preferences, elapsed_ms=110.0),
                step_index=0, idempotency_key="sim_w_step0_profile"),
            ToolCallRecord(
                tool_name="rag_search", input={"query": "职场女装穿搭指南 简约风 外套 百搭"},
                result=ToolResult(tool_name="rag_search", status="success",
                    summary=KB_ARTICLES["womenswear_style_guide"][:500],
                    structured_data={"answer": KB_ARTICLES["womenswear_style_guide"], "sources": []},
                    elapsed_ms=260.0),
                step_index=0, idempotency_key="sim_w_step0_rag"),
            ToolCallRecord(
                tool_name="knowledge_graph", input={"query": "女士西装外套 简约风", "hops": 3},
                result=ToolResult(tool_name="knowledge_graph", status="success",
                    summary="KG多跳推理：(西装外套)→[Theory羊毛西装(¥2899,4.7★), ICICLE真丝衬衫(¥1299,4.8★), Maje阔腿西裤(¥1599,4.6★), COS针织裙(¥890,4.5★), Sandro茶歇裙(¥2199,4.9★)]→[用户评价：Theory版型精准、ICICLE面料一流]→[BOUGHT_WITH: 丝巾, 简约手袋]",
                    structured_data={
                        "entities": [
                            {"name": "Theory 羊毛西装外套", "price": 2899, "rating": 4.7, "style": "职场通勤"},
                            {"name": "ICICLE 真丝衬衫", "price": 1299, "rating": 4.8, "style": "通勤百搭"},
                            {"name": "Maje 高腰阔腿西裤", "price": 1599, "rating": 4.6, "style": "职场时尚"},
                            {"name": "COS 针织连衣裙", "price": 890, "rating": 4.5, "style": "极简通勤"},
                            {"name": "Sandro 法式茶歇裙", "price": 2199, "rating": 4.9, "style": "约会度假"},
                        ],
                        "recommended": "Theory西装+ICICLE衬衫+Maje西裤 — 三件套覆盖面试/通勤/日常，总预算¥5,797，符合用户中高端定位",
                    }, elapsed_ms=520.0),
                step_index=1, idempotency_key="sim_w_step1_kg"),
            ToolCallRecord(
                tool_name="inventory_check", input={"product_ids": "WOM-001,WOM-003,WOM-004"},
                result=ToolResult(tool_name="inventory_check", status="success",
                    summary="Theory西装外套:28件 | Maje阔腿西裤:32件 | COS针织连衣裙:55件 — 全部可售，尺码齐全",
                    structured_data={"WOM-001": 28, "WOM-003": 32, "WOM-004": 55}, elapsed_ms=130.0),
                step_index=2, idempotency_key="sim_w_step2_inv"),
            ToolCallRecord(
                tool_name="web_search", input={"query": "Theory西装 Maje西裤 COS连衣裙 通勤女装 真实评价 2026"},
                result=ToolResult(tool_name="web_search", status="success",
                    summary="用户反馈：Theory西装肩线精准、面料质感高级、穿3年不变形。Maje西裤垂坠感好、高腰设计显腿长。COS连衣裙性价比最高、但弹性一般。多数用户建议：投资一件好西装+若干百搭内搭。",
                    structured_data={}, elapsed_ms=340.0),
                step_index=2, idempotency_key="sim_w_step2_web"),
        ]

    @staticmethod
    def _build_womenswear_answer() -> str:
        return """## 职场女装个性化推荐

根据你的情况分析:
- 风格：简约通勤风
- 预算：3000元
- 尺码：M码
- 已购：COS羊绒衫、Sam Edelman乐福鞋、Longchamp饺子包 → 基础单品已打底！

---

### 首推：Theory 羊毛混纺西装外套 — ¥2,899

| 维度 | 评价 |
|------|------|
| 面料质感 | 96%羊毛+4%氨纶，垂坠又有型，穿3年不变形 |
| 版型剪裁 | 修身单排扣，肩线精准，M码上身刚好 |
| 百搭程度 | 配西裤是高管气场、配牛仔裤是smart casual |
| 用户口碑 | 4.7★ (456条评价)：「通勤战袍」「最值得投资的西装」 |

> **推荐理由**：你的预算刚好够到这件。简约通勤风的"天花板单品"，一件好西装能穿5年。你已有的乐福鞋+Longchamp包正好配它，不需要额外投入配饰。

### 搭配建议：ICICLE 之禾真丝衬衫 — ¥1,299

如果你想把整套搭配一步到位，ICICLE的100%桑蚕丝衬衫是西装内搭的最佳选择。飘带领设计配西装露出领口，精致度直接翻倍。

---

> **小雨的专属建议**：你已经有了COS羊绒衫（¥690）和乐福鞋（¥899），说明你对品质有要求。建议先入Theory西装（¥2,899），和现有的羊绒衫+乐福鞋+Longchamp包可以组成一套完整的通勤look。下个月再补ICICLE衬衫。

> Theory西装库存28件，M码尚有余量，建议尽快下单。

---

### 配套推荐（职场衣橱基础款）
| 单品 | 价格 | 搭配建议 |
|------|------|------|
| 真丝方巾 90cm | ¥299 | 系在包上或领口，增加层次感 |
| 简约通勤手袋 | ¥899 | 替代已有Longchamp的换季选择 |
| 防静电喷雾 100ml | ¥49 | 羊毛西装必备，喷一下告别吸腿 |
| **衣橱总预算** | **¥4,146** | 外套+丝巾+护理=春季通勤套装 |"""

    @staticmethod
    def _extract_womenswear_sources() -> list[dict[str, Any]]:
        return [
            {"chunk_id": "kb_002", "file_id": "womenswear_guide", "text": KB_ARTICLES["womenswear_style_guide"][:300]},
        ]

    @staticmethod
    def _build_competitor_records() -> list[ToolCallRecord]:
        return [
            ToolCallRecord(
                tool_name="price_analysis", input={"product_id": "WOM-001"},
                result=ToolResult(tool_name="price_analysis", status="success",
                    summary="Theory羊毛西装外套竞品价格：天猫¥2,899、京东¥2,849、抖音¥2,699、拼多多¥2,599。市场中位数¥2,774。我方定价¥2,899（高于中位数+¥125）。",
                    structured_data={"platforms": [
                        {"platform": "天猫旗舰店", "price": 2899, "promotion": "满2000减150"},
                        {"platform": "京东自营", "price": 2849, "promotion": "PLUS会员95折"},
                        {"platform": "抖音商城", "price": 2699, "promotion": "直播间赠丝巾"},
                        {"platform": "拼多多", "price": 2599, "promotion": "百亿补贴，无赠品"},
                    ]}, elapsed_ms=420.0),
                step_index=0, idempotency_key="sim_p_step0"),
            ToolCallRecord(
                tool_name="rag_search", input={"query": "女装外套市场定价策略 2026"},
                result=ToolResult(tool_name="rag_search", status="success",
                    summary=KB_ARTICLES["competitor_analysis_template"][:500],
                    structured_data={}, elapsed_ms=210.0),
                step_index=0, idempotency_key="sim_p_step0_rag"),
            ToolCallRecord(
                tool_name="web_search", input={"query": "Theory西装外套 2026年价格走势 女装"},
                result=ToolResult(tool_name="web_search", status="success",
                    summary="2026年Theory西装外套均价¥2,700-2,900波动，抖音直播价持续走低至¥2,599。618大促预计再降10-15%。ICICLE同款均价¥3,200，Theory性价比优势明显。",
                    structured_data={}, elapsed_ms=310.0),
                step_index=1, idempotency_key="sim_p_step1"),
        ]

    @staticmethod
    def _build_competitor_answer() -> str:
        return """## Theory 羊毛混纺西装外套 竞品定价分析

---

### 各平台价格对比

| 平台 | 价格 | 与中位数差 | 备注 |
|------|------|-----------|------|
| 天猫旗舰店 | ¥2,899 | +¥125 | 满2000减150，实际¥2,749 |
| 京东自营 | ¥2,849 | +¥75 | PLUS会员95折，实际¥2,707 |
| **抖音商城** | **¥2,699** | **-¥75** | 直播间赠丝巾（价值约¥99） |
| 拼多多 | ¥2,599 | -¥175 | 百亿补贴，无赠品 |
| 市场中位数 | **¥2,774** | — | — |

---

### 竞品对标

| 品牌 | 产品 | 价格 | 面料 | 优势 |
|------|------|------|------|------|
| Theory | 羊毛混纺西装 | ¥2,899 | 96%羊毛 | 品牌溢价 + 版型经典 |
| ICICLE | 之禾羊毛西装 | ¥3,200 | 100%羊毛 | 国货高端，面料更优 |
| Massimo Dutti | 羊毛混纺外套 | ¥1,790 | 50%羊毛 | 快时尚性价比 |
| 鄂尔多斯 | 羊绒西装外套 | ¥3,990 | 100%羊绒 | 顶级面料，送礼首选 |

---

### 定价建议

| 方案 | 价格 | 预期效果 |
|------|------|----------|
| **A. 小幅下调（推荐）** | ¥2,749 | 匹配天猫满减后价格 + 赠送丝巾，客户感知价值¥2,899 |
| B. 捆绑销售 | ¥2,899 | 原价不变 + 购买外套送¥299西裤券，提升连带率 |
| C. 会员专享 | ¥2,699 | 仅限会员，对标抖音直播价，锁定复购 |

### 综合建议
当前定价 ¥2,899 略高于市场中位数 ¥2,774（+4.5%），但品牌溢价合理。主要威胁来自抖音直播价 ¥2,599 的持续走低。**建议采用方案A + B组合：降至¥2,749 + 赠送丝巾 + 西裤券**，在维持品牌调性的同时提升竞争力。

> 618大促前不建议大幅降价。可在大促当日限时降至¥2,599 对标抖音价冲销量，日常维持¥2,749。"""