"""Canary auto-rollback demo — simulates error rate spike and automated response.

Demonstrates:
1. Normal operation: canary at 5%, metrics look good
2. Error rate spike: canary error rate jumps to 8% (exceeds 5% guardrail)
3. RolloutDecider detects guardrail violation
4. Auto-rollback: canary_pct -> 0, admin notification sent
5. Recovery: error drops, system returns to normal

Run: python demo_rollback.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def simulate_rollout_cycle():
    """Run through a full canary lifecycle with auto-rollback."""
    from agentic_rag.evaluation.rollout import (
        RolloutConfig, RolloutDecider, RolloutAction, Guardrail,
        ConfigVariant, VariantRouter,
    )
    from agentic_rag.evaluation.attribution import ExperimentBucketer

    print("=" * 65)
    print("  CANARY AUTO-ROLLBACK DEMO")
    print("  Demonstrates: error spike -> guardrail violation -> auto-rollback")
    print("=" * 65)
    print()

    # Setup
    guardrails = [
        Guardrail(metric_name="error_rate", max_absolute_value=0.05,
                  description="Error rate must stay below 5%"),
        Guardrail(metric_name="p99_latency_ms", max_absolute_value=8000,
                  description="P99 latency must stay below 8s"),
    ]

    config = RolloutConfig(
        experiment_name="planner_prompt_v2",
        control_variant=ConfigVariant(name="control_v1"),
        canary_variant=ConfigVariant(name="canary_v2_ordered_tools"),
        canary_pct=5.0,
        min_sample_size=50,
        primary_metric="conversion_rate",
        guardrails=guardrails,
    )

    bucketer = ExperimentBucketer("rollout_demo", ["control", "treatment"])
    decider = RolloutDecider(config)
    router = VariantRouter(config, bucketer)

    # Phase 1: Normal operation
    print("--- Phase 1: Normal Operation (canary=5%) ---")
    action, reason = decider.decide(
        control_metrics={"conversion_rate": 0.032, "error_rate": 0.02, "p99_latency_ms": 3500},
        canary_metrics={"conversion_rate": 0.041, "error_rate": 0.03, "p99_latency_ms": 3600},
        control_events=200, canary_events=200,
    )
    _print_decision(action, reason, config.canary_pct)

    # Traffic check
    _print_traffic_split(router, config.canary_pct)

    # Phase 2: Error spike!
    print()
    print("--- Phase 2: Error Rate Spike! (canary error 8% > guardrail 5%) ---")
    time.sleep(0.5)

    action, reason = decider.decide(
        control_metrics={"conversion_rate": 0.032, "error_rate": 0.02, "p99_latency_ms": 3550},
        canary_metrics={"conversion_rate": 0.045, "error_rate": 0.08, "p99_latency_ms": 7200},
        control_events=250, canary_events=250,
    )
    _print_decision(action, reason, config.canary_pct)

    # Auto-rollback executes
    if action == RolloutAction.ROLLBACK:
        new_pct = decider.compute_next_canary_pct(action)
        print()
        print(f"  AUTO-ROLLBACK EXECUTED: canary_pct {config.canary_pct}% -> {new_pct}%")

        # Simulate the rollback notification that would appear in the admin panel
        print()
        print("  " + "=" * 55)
        print("  [ADMIN ALERT] Canary auto-rollback triggered!")
        print(f"  Experiment: {config.experiment_name}")
        print(f"  Reason: {reason}")
        print(f"  Action: ROLLBACK (canary_pct set to {new_pct}%)")
        print(f"  Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("  [ACTION REQUIRED] Investigate canary error rate spike.")
        print("  " + "=" * 55)

        config.canary_pct = new_pct

    # Phase 3: After rollback
    print()
    print("--- Phase 3: Post-Rollback Recovery (canary=0%) ---")

    action, reason = decider.decide(
        control_metrics={"conversion_rate": 0.032, "error_rate": 0.02, "p99_latency_ms": 3500},
        canary_metrics={"conversion_rate": 0.032, "error_rate": 0.02, "p99_latency_ms": 3500},
        control_events=300, canary_events=0,
    )
    _print_decision(action, reason, 0.0)

    # Phase 4: After fixing the issue, restart canary
    print()
    print("--- Phase 4: Issue Fixed, Re-start Canary at 5% ---")
    config.canary_pct = 5.0

    action, reason = decider.decide(
        control_metrics={"conversion_rate": 0.032, "error_rate": 0.02, "p99_latency_ms": 3500},
        canary_metrics={"conversion_rate": 0.039, "error_rate": 0.025, "p99_latency_ms": 3700},
        control_events=100, canary_events=100,
    )
    _print_decision(action, reason, config.canary_pct)

    # Summary
    print()
    print("=" * 65)
    print("  DEMO COMPLETE")
    print("=" * 65)
    print()
    print("Key takeaways:")
    print("  1. Guardrail violation (error_rate 8% > 5%) triggered ROLLBACK")
    print("  2. Rollback was automatic — no human intervention required")
    print("  3. Even though conversion_rate improved (+40%), safety gate prevailed")
    print("  4. After fix, canary was re-started cleanly")
    print("  5. Admin panel would show the alert in real-time")


def _print_decision(action, reason, current_pct):
    icon = {
        "expand": "[EXPAND]",
        "hold": "[HOLD]",
        "rollback": "[ROLLBACK]",
        "complete": "[COMPLETE]",
    }.get(action.value, "[?]")

    print(f"  Decision: {icon} {action.value}")
    print(f"  Reason: {reason}")
    print(f"  Current canary: {current_pct}%")


def _print_traffic_split(router, canary_pct):
    canary_count = 0
    total = 1000
    for i in range(total):
        variant, group = router.select_variant(f"user_{i}")
        if "canary" in group:
            canary_count += 1
    print(f"  Traffic split (simulated 1000 users):")
    print(f"    Control: {total - canary_count} ({100 - canary_count/total*100:.1f}%)")
    print(f"    Canary:  {canary_count} ({canary_count/total*100:.1f}%)")


if __name__ == "__main__":
    simulate_rollout_cycle()
