"""Agent state machine — states and transitions.

State flow::

    CLARIFYING → PLANNING → VALIDATING → EXECUTING → REFLECTING → SYNTHESIZING → DONE
        ↑              ↑                        ↓              ↓
        └── re-enter ──┘                        └── re-plan ──┘
"""

from __future__ import annotations

from agentic_rag.models import AgentState

# Valid transitions
_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.CLARIFYING: {AgentState.PLANNING, AgentState.DONE, AgentState.ERROR},
    AgentState.PLANNING: {AgentState.VALIDATING, AgentState.CLARIFYING, AgentState.ERROR},
    AgentState.VALIDATING: {AgentState.EXECUTING, AgentState.PLANNING, AgentState.ERROR},
    AgentState.EXECUTING: {AgentState.REFLECTING, AgentState.ERROR},
    AgentState.REFLECTING: {AgentState.SYNTHESIZING, AgentState.PLANNING, AgentState.ERROR},
    AgentState.SYNTHESIZING: {AgentState.DONE, AgentState.ERROR},
    AgentState.DONE: set(),
    AgentState.ERROR: {AgentState.DONE},
}

# Terminal states
TERMINAL_STATES: set[AgentState] = {AgentState.DONE, AgentState.ERROR}


def can_transition(from_state: AgentState, to_state: AgentState) -> bool:
    """Check whether a state transition is allowed."""
    return to_state in _TRANSITIONS.get(from_state, set())


def assert_transition(from_state: AgentState, to_state: AgentState) -> None:
    """Raise ``ValueError`` if the transition is invalid."""
    if not can_transition(from_state, to_state):
        raise ValueError(
            f"Invalid state transition: {from_state.value} → {to_state.value}"
        )


def is_terminal(state: AgentState) -> bool:
    return state in TERMINAL_STATES
