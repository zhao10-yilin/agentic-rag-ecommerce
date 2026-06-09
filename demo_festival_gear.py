"""End-to-end festival gear demo — full Agent flow with live plan visualization.

Simulates: User says "I'm going to a music festival" -> Agent clarifies ->
User responds -> Agent plans, executes, reflects, synthesizes -> Final answer.
Each phase is printed with timing and status, simulating what a screen
recording would show.

Run: python -X utf8 demo_festival_gear.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SEP = "=" * 65


async def simulate_festival_gear_flow():
    print()
    print(SEP)
    print("  END-TO-END: Outdoor Music Festival Gear Recommendation")
    print("  Full Agentic RAG Flow with Live Step Visualization")
    print(SEP)
    print()

    # --- Turn 1: Vague input ---
    user_msg = "我要去参加一个户外音乐节，帮我推荐一套装备吧"
    _print_user_message(user_msg)
    await asyncio.sleep(0.3)

    _print_phase("CLARIFYING", "Intent clarity = 0.4 (below 0.7 threshold)")
    await asyncio.sleep(0.2)

    clarification = (
        "好的！户外音乐节需要不少准备呢～您是打算过夜露营，"
        "还是当天来回？另外，音乐节在什么季节、在哪个城市？"
        "这些会决定推荐什么装备哦。"
    )
    _print_agent_message(clarification, is_question=True)

    # --- Turn 2: User clarifies ---
    print()
    user_response = "夏季的音乐节，在杭州，会过夜露营两晚"
    _print_user_message(user_response)
    await asyncio.sleep(0.3)

    # --- Planning ---
    _print_phase("PLANNING", "Decomposing into execution plan...")
    await asyncio.sleep(0.4)

    plan_steps = [
        ("Step 0", "rag_search", "搜索知识库：户外音乐节装备指南", "no deps", 245),
        ("Step 1", "web_search", "实时搜索：杭州夏季天气", "no deps", 380),
        ("Step 1", "user_profile", "加载用户画像：小美", "no deps", 120),
        ("Step 2", "rag_search", "检索具体商品推荐", "deps=[0,1]", 290),
        ("Step 2", "inventory_check", "批量检查6件商品库存", "deps=[0,1]", 150),
    ]
    _print_plan_graph(plan_steps)
    await asyncio.sleep(0.3)

    # --- Validation ---
    _print_phase("VALIDATING", "PlanValidator: 5 actions, 0 errors, 0 circular deps -> PASS")
    await asyncio.sleep(0.1)

    # --- Execution ---
    _print_phase("EXECUTING", "Running 3 steps with parallel dispatch...")
    print()

    step_results = [
        ("Step 0", [
            ("rag_search", "success", 245, "找到3篇指南：音乐节装备清单、露营攻略、夏季防晒指南"),
        ]),
        ("Step 1", [
            ("web_search", "success", 380, "杭州夏季28-35C，多雨潮湿，夜间22C。场地为草地。"),
            ("user_profile", "success", 120, "小美：户外爱好者，偏好迪卡侬/NatureHike，预算800-2000元"),
        ]),
        ("Step 2", [
            ("rag_search", "success", 290, "推荐6件商品：帐篷399、折叠椅159、速干衣129、头灯298、背包499、防潮垫189"),
            ("inventory_check", "success", 150, "全部有货：OUT-001(45), OUT-002(120), OUT-003(200), OUT-004(75), OUT-005(60), OUT-006(90)"),
        ]),
    ]

    for step_name, actions in step_results:
        _print_step_header(step_name, actions)
        await asyncio.sleep(0.2)
        for tool_name, status, elapsed, summary in actions:
            _print_tool_result(tool_name, status, elapsed, summary)

    print()

    # --- Reflection ---
    _print_phase("REFLECTING", "LLM Reflector: checking consistency...")
    await asyncio.sleep(0.3)
    print("  [OK] All tools returned success")
    print("  [OK] Inventory confirms all 6 products in stock")
    print("  [OK] User budget (800-2000) covers recommended total (1673)")
    print("  [OK] Weather data (rain) -> waterproof tent recommendation is correct")
    print("  -> needs_replan: false, overall_quality: 0.95")
    print()

    # --- Synthesis ---
    _print_phase("SYNTHESIZING", "Building final recommendation...")
    await asyncio.sleep(0.3)

    answer = """## Outdoor Music Festival Gear (Hangzhou, Summer)

Based on your needs (summer camping, 2 nights, lightweight + value), here's your gear plan:

### Sleep System
- NatureHike Ultralight Tent (399 yuan, 1.8kg, 3000mm waterproof)
- Nuoke Inflatable Pad (189 yuan, R=3.5, 6.5cm thick)

### Clothing & Protection
- Decathlon UPF50+ Quick-Dry Shirt (129 yuan)

### Lighting & Comfort
- Black Diamond Headlamp (298 yuan, 400 lumens, IPX8)
- Toread Folding Chair (159 yuan, 0.9kg)
- Osprey Daylite 20L Backpack (499 yuan)

### Total: 1,673 yuan (within your 800-2000 budget)

> Hangzhou summer tip: bring waterproof bags for electronics. Night temps ~22C, pack a light jacket.
> All 6 items in stock. Ready to order."""

    _print_final_answer(answer)

    # --- Metrics ---
    print()
    print(f"  Total elapsed: 5.52s")
    print(f"  Tool calls: 5 (5 success, 0 degraded, 0 error)")
    print(f"  Reflection rounds: 1")
    print(f"  Plan steps: 3")
    print(f"  Experiment group: control")
    print()

    print(SEP)
    print("  FLOW COMPLETE")
    print("  This is what a screen recording would capture in real-time.")
    print(SEP)


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def _print_user_message(msg: str):
    print(f"  [USER] {msg}")


def _print_agent_message(msg: str, is_question: bool = False):
    prefix = "[AGENT -> CLARIFY]" if is_question else "[AGENT]"
    print(f"  {prefix} {msg}")


def _print_phase(phase: str, detail: str):
    colors = {
        "CLARIFYING": "\033[91m", "PLANNING": "\033[94m",
        "VALIDATING": "\033[95m", "EXECUTING": "\033[92m",
        "REFLECTING": "\033[93m", "SYNTHESIZING": "\033[96m",
    }
    c = colors.get(phase, "")
    reset = "\033[0m"
    print(f"  {c}[{phase}]{reset} {detail}")


def _print_plan_graph(steps: list[tuple]):
    """Print a simple visual dependency graph."""
    print()
    print("  Plan DAG:")
    current_step = ""
    for step_name, tool, desc, deps, elapsed in steps:
        if step_name != current_step:
            if current_step:
                print()
            current_step = step_name
            print(f"  {step_name}:")
        dep_str = f" [{deps}]" if "deps" in deps else ""
        print(f"    +-- {tool}(query){dep_str}")
        print(f"    |   -> {desc}")
    print()


def _print_step_header(step_name: str, actions: list):
    parallel = " (PARALLEL)" if len(actions) > 1 else ""
    print(f"  [{step_name}]{parallel}")
    print(f"  {'':-<40}")


def _print_tool_result(tool_name: str, status: str, elapsed_ms: float, summary: str):
    icon = {"success": "[OK]", "degraded": "[WARN]", "error": "[FAIL]", "timeout": "[TIMEOUT]"}.get(status, "[?]")
    print(f"  {icon} {tool_name} ({elapsed_ms}ms)")
    print(f"     {summary[:100]}")


def _print_final_answer(answer: str):
    print()
    print("  " + "-" * 55)
    for line in answer.split("\n"):
        print(f"  {line}")
    print("  " + "-" * 55)


if __name__ == "__main__":
    asyncio.run(simulate_festival_gear_flow())
