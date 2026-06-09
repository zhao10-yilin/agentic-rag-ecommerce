"""Feature-flag driven canary rollout with automated guardrails.

Architecture
------------
1. **ExperimentBucketer** (existing) — user_id → group (control / canary).
2. **ConfigVariant** (new) — maps each group to a Planner prompt template,
   model temperature, max_steps, etc.
3. **RolloutDecider** (new) — compares A/B metrics and decides:
   EXPAND (increase canary %), HOLD (wait for more data), ROLLBACK.
4. **Guardrail** (new) — if a critical metric (e.g., error rate) crosses a
   threshold, the canary is killed automatically regardless of other metrics.

Flow per request
----------------
1. Bucket user → "control" or "canary_v2"
2. Lookup ConfigVariant for that group → inject into Planner
3. Record all AgentResponses with experiment_group label
4. RolloutDecider runs periodically (e.g., hourly) on accumulated metrics
5. If canary beats control on primary metric AND all guardrails pass → expand
6. If any guardrail violated → immediate rollback
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decision types
# ---------------------------------------------------------------------------


class RolloutAction(str, Enum):
    EXPAND = "expand"       # Increase canary traffic %
    HOLD = "hold"           # Wait for more data / statistical significance
    ROLLBACK = "rollback"   # Kill canary, revert to control
    COMPLETE = "complete"   # Canary is now the default (100%)


class GuardrailStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"     # Approaching threshold
    VIOLATED = "violated"   # Threshold crossed → auto-rollback


# ---------------------------------------------------------------------------
# Config variant
# ---------------------------------------------------------------------------


@dataclass
class ConfigVariant:
    """A named configuration variant for A/B testing.

    Each field can be overridden per variant.  Fields left as None inherit
    from the base (control) config.
    """

    name: str
    planner_system_prompt: str | None = None       # Override planning prompt
    planner_temperature: float | None = None        # Override LLM temperature
    max_plan_steps: int | None = None
    intent_clarity_threshold: float | None = None
    max_reflection_rounds: int | None = None
    # Model override (e.g., test a cheaper model for planning)
    light_model_override: str | None = None

    def apply_to_planner(self, base_prompt: str, base_temperature: float) -> tuple[str, float]:
        """Return (effective_prompt, effective_temperature) for this variant."""
        prompt = self.planner_system_prompt or base_prompt
        temp = self.planner_temperature if self.planner_temperature is not None else base_temperature
        return prompt, temp


# ---------------------------------------------------------------------------
# Rollout config
# ---------------------------------------------------------------------------


@dataclass
class RolloutConfig:
    """Controls the canary rollout lifecycle.

    Parameters
    ----------
    experiment_name:
        Unique name for this experiment.
    control_variant:
        The current production config.
    canary_variant:
        The new config being tested.
    canary_pct:
        Percentage of traffic (0-100) routed to the canary variant.
    min_sample_size:
        Minimum number of events per group before making a decision.
    significance_level:
        P-value threshold for statistical significance (simplified heuristic).
    primary_metric:
        The metric to optimize (e.g., "conversion_rate").
    min_relative_improvement:
        Canary must beat control by at least this % on the primary metric.
    guardrails:
        List of guardrail metrics with absolute thresholds.
    """

    experiment_name: str
    control_variant: ConfigVariant
    canary_variant: ConfigVariant
    canary_pct: float = 5.0
    min_sample_size: int = 100
    significance_level: float = 0.05
    primary_metric: str = "conversion_rate"
    min_relative_improvement: float = 0.0  # 0 = any improvement is enough
    guardrails: list["Guardrail"] = field(default_factory=list)


@dataclass
class Guardrail:
    """A metric that must NOT degrade beyond an absolute threshold.

    If ANY guardrail is violated, the canary is killed immediately,
    regardless of the primary metric.
    """

    metric_name: str              # e.g. "error_rate", "avg_latency_ms"
    max_absolute_value: float     # e.g. error_rate must stay below 0.05
    description: str = ""


# ---------------------------------------------------------------------------
# RolloutDecider
# ---------------------------------------------------------------------------


class RolloutDecider:
    """Compares control vs canary metrics and recommends an action.

    This is a heuristic decision engine, NOT a full statistical framework.
    For production use with rigorous statistics, integrate with an
    experimentation platform (e.g., GrowthBook, LaunchDarkly, or a custom
    Bayesian engine).  This implementation provides the integration point
    and a reasonable default heuristic.

    Parameters
    ----------
    config:
        The rollout configuration.
    """

    def __init__(self, config: RolloutConfig) -> None:
        self._config = config

    def decide(
        self,
        control_metrics: dict[str, float],
        canary_metrics: dict[str, float],
        *,
        control_events: int = 0,
        canary_events: int = 0,
    ) -> tuple[RolloutAction, str]:
        """Compare control vs canary metrics and return (action, reason).

        Decision order:
        1. Guardrails first — any violation → immediate ROLLBACK.
        2. Sample size check — insufficient data → HOLD.
        3. Primary metric comparison — canary better → EXPAND, worse → ROLLBACK.
        4. Within noise → HOLD.
        """
        # ---- 1. Guardrail check ----
        for guardrail in self._config.guardrails:
            canary_value = canary_metrics.get(guardrail.metric_name)
            if canary_value is None:
                continue
            if canary_value > guardrail.max_absolute_value:
                return (
                    RolloutAction.ROLLBACK,
                    f"护栏告警: {guardrail.metric_name}={canary_value} 超过阈值 {guardrail.max_absolute_value} — {guardrail.description}",
                )

            # Warning if approaching threshold (within 20%)
            if canary_value > guardrail.max_absolute_value * 0.8:
                logger.warning(
                    "Guardrail approaching: %s=%.4f (threshold=%.4f)",
                    guardrail.metric_name, canary_value, guardrail.max_absolute_value,
                )

        # ---- 2. Sample size ----
        if control_events < self._config.min_sample_size or canary_events < self._config.min_sample_size:
            return (
                RolloutAction.HOLD,
                f"样本不足: control={control_events}, canary={canary_events}, min={self._config.min_sample_size}",
            )

        # ---- 3. Primary metric ----
        primary = self._config.primary_metric
        control_val = control_metrics.get(primary, 0)
        canary_val = canary_metrics.get(primary, 0)

        if control_val == 0:
            return RolloutAction.HOLD, f"Control {primary} is zero, cannot compare"

        relative_change = (canary_val - control_val) / control_val

        if relative_change >= self._config.min_relative_improvement:
            if self._config.canary_pct >= 50:
                return (
                    RolloutAction.COMPLETE,
                    f"Canary {primary}={canary_val:.4f} vs control {control_val:.4f} (+{relative_change:.1%}) → 全量发布",
                )
            return (
                RolloutAction.EXPAND,
                f"Canary {primary}={canary_val:.4f} vs control {control_val:.4f} (+{relative_change:.1%}) → 扩大放量",
            )
        elif relative_change < -0.05:  # More than 5% worse
            return (
                RolloutAction.ROLLBACK,
                f"Canary {primary}={canary_val:.4f} vs control {control_val:.4f} ({relative_change:.1%}) → 回滚",
            )
        else:
            return (
                RolloutAction.HOLD,
                f"Canary {primary}={canary_val:.4f} vs control {control_val:.4f} ({relative_change:.1%}) → 差异不显著，继续观察",
            )

    def compute_next_canary_pct(self, action: RolloutAction) -> float:
        """Given an action, compute the suggested new canary percentage."""
        if action == RolloutAction.ROLLBACK:
            return 0.0
        if action == RolloutAction.EXPAND:
            # Double the canary percentage, capped at 50%
            return min(self._config.canary_pct * 2, 50.0)
        if action == RolloutAction.COMPLETE:
            return 100.0
        return self._config.canary_pct  # HOLD


# ---------------------------------------------------------------------------
# Planner integration — selects variant per request
# ---------------------------------------------------------------------------


class VariantRouter:
    """Routes each request to the correct Planner config variant.

    Parameters
    ----------
    rollout_config:
        The active rollout configuration.
    bucketer:
        The ExperimentBucketer that assigns users to groups.
    """

    def __init__(
        self,
        rollout_config: RolloutConfig,
        bucketer: Any,  # ExperimentBucketer
    ) -> None:
        self._config = rollout_config
        self._bucketer = bucketer

    def select_variant(self, user_id: str) -> tuple[ConfigVariant, str]:
        """Return (variant, group_label) for this user_id.

        Uses hash-based bucketing: user_id is hashed, and if the hash
        falls within the canary percentile, the canary variant is served.
        """
        group = self._bucketer.assign(user_id)

        # Override: if this experiment's canary_pct is > 0, use a
        # percentile-based split within the "treatment" group.
        # This allows multiple simultaneous experiments without interference.
        if self._config.canary_pct > 0:
            percentile = self._hash_to_percentile(user_id, self._config.experiment_name)
            if percentile < self._config.canary_pct:
                return self._config.canary_variant, f"{self._config.experiment_name}_canary"

        return self._config.control_variant, group

    @staticmethod
    def _hash_to_percentile(user_id: str, experiment: str) -> float:
        """Hash user_id → deterministic float in [0, 100)."""
        seed = f"{experiment}:canary:{user_id}"
        hash_hex = hashlib.sha256(seed.encode()).hexdigest()[:8]
        return (int(hash_hex, 16) % 10000) / 100.0


# ---------------------------------------------------------------------------
# Example: a Planner prompt change canary
# ---------------------------------------------------------------------------

# Control — current production prompt (abbreviated)
CONTROL_VARIANT = ConfigVariant(
    name="control_v1",
    # Uses base Planner prompt (no override)
)

# Canary — new prompt with stronger tool-ordering guidance
CANARY_V2 = ConfigVariant(
    name="canary_v2_ordered_tools",
    planner_system_prompt="""\
你是一个电商智能助手的任务规划器...（新版本prompt）
新增规则：如果用户提到价格比较，必须优先调用 price_analysis 而非 rag_search。
""",
    planner_temperature=0.1,  # Lower temperature for more deterministic plans
)

# Guardrails: metrics that must NOT degrade
GUARDRAILS = [
    Guardrail(
        metric_name="error_rate",
        max_absolute_value=0.05,
        description="错误率不能超过5%",
    ),
    Guardrail(
        metric_name="avg_latency_seconds",
        max_absolute_value=10.0,
        description="平均延迟不能超过10秒",
    ),
    Guardrail(
        metric_name="plan_validation_failure_rate",
        max_absolute_value=0.10,
        description="计划校验失败率不能超过10%",
    ),
]

# Rollout config
DEFAULT_ROLLOUT = RolloutConfig(
    experiment_name="planner_prompt_v2",
    control_variant=CONTROL_VARIANT,
    canary_variant=CANARY_V2,
    canary_pct=5.0,           # Start at 5%
    min_sample_size=200,       # Need 200 events per group
    primary_metric="conversion_rate",
    min_relative_improvement=0.0,  # Any improvement is acceptable
    guardrails=GUARDRAILS,
)
