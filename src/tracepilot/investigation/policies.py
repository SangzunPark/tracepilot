"""Week 4 deterministic investigation policies.

Pure functions: no I/O, no clock, no randomness. The provider proposes; these
functions decide whether a proposal is permitted. No execution loop, MCP call,
or provider lives here.
"""

import hashlib
import json
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Final, TypeGuard

from pydantic import JsonValue, ValidationError

from tracepilot.investigation.models import (
    MAX_BLOCK_DETAIL_CHARS,
    TOOL_ARGUMENT_MODELS,
    TOOL_NAMES,
    BlockReason,
    InvestigationMode,
    InvestigationState,
    TerminationReason,
    ToolCallRecord,
    ToolName,
    ToolRequest,
)

ALLOWED_TOOLS: Final[frozenset[str]] = frozenset(TOOL_NAMES)
CASE_SCOPED_TOOLS: Final[frozenset[str]] = ALLOWED_TOOLS - {"search_runbooks"}
MAX_AGENT_STEPS: Final = 15

RUNTIME_CASE_ID_KEY: Final = "case_id"


class ArgumentValidationError(ValueError):
    """Provider-facing arguments failed schema-level validation."""

    def __init__(self, tool: ToolName, error: ValidationError) -> None:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: {item['type']}"
            for item in error.errors()
        )
        super().__init__(f"invalid arguments for {tool}: {problems}")
        self.tool = tool


@dataclass(frozen=True)
class NormalizedCall:
    """A schema-valid call: full normalized arguments (defaults materialized,
    datetimes in UTC, no case_id) and their fingerprint."""

    tool: ToolName
    arguments: dict[str, JsonValue]
    fingerprint: str


@dataclass(frozen=True)
class PolicyDecision:
    permitted: bool
    block_reason: BlockReason | None = None
    detail: str | None = None
    call: NormalizedCall | None = None  # set when permitted or DUPLICATE


# ---------------------------------------------------------------------------
# Allowlist, normalization, fingerprint
# ---------------------------------------------------------------------------


def is_allowed_tool(tool: str) -> TypeGuard[ToolName]:
    return tool in ALLOWED_TOOLS


def normalize_arguments(tool: ToolName, arguments: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Validate with the local per-tool model and return the full normalized
    argument dict. Raises ArgumentValidationError on schema-level failure."""
    model = TOOL_ARGUMENT_MODELS[tool]
    try:
        validated = model.model_validate(dict(arguments))
    except ValidationError as error:
        raise ArgumentValidationError(tool, error) from error
    return validated.model_dump(mode="json")


def compute_fingerprint(tool: ToolName, normalized_arguments: Mapping[str, JsonValue]) -> str:
    """SHA-256 over canonical JSON of tool name plus normalized arguments.
    The runtime case_id is never part of it."""
    canonical = json.dumps(
        {"tool": tool, "arguments": dict(normalized_arguments)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_call(tool: ToolName, arguments: Mapping[str, JsonValue]) -> NormalizedCall:
    normalized = normalize_arguments(tool, arguments)
    return NormalizedCall(tool, normalized, compute_fingerprint(tool, normalized))


# ---------------------------------------------------------------------------
# Request evaluation
# ---------------------------------------------------------------------------


def _blocked(
    reason: BlockReason, detail: str, call: NormalizedCall | None = None
) -> PolicyDecision:
    return PolicyDecision(
        permitted=False,
        block_reason=reason,
        detail=detail[:MAX_BLOCK_DETAIL_CHARS],
        call=call,
    )


def evaluate_request(request: ToolRequest, prior_fingerprints: Collection[str]) -> PolicyDecision:
    """Decide one action attempt. Fixed order: UNKNOWN_TOOL, FORBIDDEN_ARGUMENT,
    INVALID_ARGUMENTS, DUPLICATE, then permitted."""
    if not is_allowed_tool(request.tool):
        return _blocked(BlockReason.UNKNOWN_TOOL, "tool is not in the allowlist")
    if RUNTIME_CASE_ID_KEY in request.arguments:
        return _blocked(
            BlockReason.FORBIDDEN_ARGUMENT,
            "case_id is runtime-owned and must not be supplied",
        )
    try:
        call = normalize_call(request.tool, request.arguments)
    except ArgumentValidationError as error:
        return _blocked(BlockReason.INVALID_ARGUMENTS, str(error))
    if call.fingerprint in prior_fingerprints:
        return _blocked(BlockReason.DUPLICATE, "identical call was already permitted", call)
    return PolicyDecision(permitted=True, call=call)


def prior_fingerprints(records: Iterable[ToolCallRecord]) -> frozenset[str]:
    """Duplicate set: fingerprints of permitted calls, whether or not their
    execution returned an error. Blocked attempts never seed it."""
    return frozenset(
        record.fingerprint
        for record in records
        if record.permitted and record.fingerprint is not None
    )


def execution_arguments(call: NormalizedCall, runtime_case_id: str) -> dict[str, JsonValue]:
    """The MCP arguments for a permitted call: the runtime-owned case_id is
    injected for case-scoped tools only. Returns a new dict."""
    if RUNTIME_CASE_ID_KEY in call.arguments:
        raise ValueError("normalized arguments must not contain case_id")
    if call.tool not in CASE_SCOPED_TOOLS:
        return dict(call.arguments)
    if not runtime_case_id:
        raise ValueError("runtime_case_id must not be empty")
    return {RUNTIME_CASE_ID_KEY: runtime_case_id, **call.arguments}


# ---------------------------------------------------------------------------
# Step budget and structural termination
# ---------------------------------------------------------------------------


def _check_budget_inputs(agent_steps: int, max_steps: int) -> None:
    if agent_steps < 0:
        raise ValueError("agent_steps must not be negative")
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")


def remaining_steps(agent_steps: int, max_steps: int = MAX_AGENT_STEPS) -> int:
    _check_budget_inputs(agent_steps, max_steps)
    return max(max_steps - agent_steps, 0)


def is_budget_exhausted(agent_steps: int, max_steps: int = MAX_AGENT_STEPS) -> bool:
    _check_budget_inputs(agent_steps, max_steps)
    return agent_steps >= max_steps


def is_plan_exhausted(state: InvestigationState) -> bool:
    """True when a plan exists and its cursor has passed the last step. An
    empty plan is exhausted immediately; no plan yet is not exhausted."""
    return state.plan is not None and state.current_step_index >= len(state.plan.steps)


def structural_termination(
    state: InvestigationState, max_steps: int = MAX_AGENT_STEPS
) -> TerminationReason | None:
    """The structural termination reason, or None while the investigation may
    continue. An already recorded termination_reason is final.

    An exhausted plan is terminal for Fixed Playbook and Plan Once, and for
    Adaptive when no plan review is pending. Terminal plan exhaustion takes
    precedence over budget exhaustion. An exhausted Adaptive plan with a
    pending plan review (a fresh executed Observation not yet consumed by a
    KEEP/REPLACE decision) is not terminal while budget remains: the review
    must run first. With the budget exhausted it is BUDGET_EXHAUSTED, and no
    further review happens.
    """
    if state.termination_reason is not None:
        return state.termination_reason
    budget_exhausted = is_budget_exhausted(state.step_count, max_steps)
    if is_plan_exhausted(state) and not plan_review_pending(state):
        return TerminationReason.PLAN_EXHAUSTED
    if budget_exhausted:
        return TerminationReason.BUDGET_EXHAUSTED
    return None


# ---------------------------------------------------------------------------
# Adaptive plan review
# ---------------------------------------------------------------------------


def plan_review_pending(state: InvestigationState) -> bool:
    """Whether an Adaptive plan-review opportunity is pending: an executed
    record (fresh Observation) later than plan_decision_at_step. Fixed
    Playbook and Plan Once never review. A blocked attempt is not an
    Observation and creates no opportunity; an executed MCP error is one.
    Both KEEP and REPLACE set plan_decision_at_step to the current step
    count, so one Observation yields at most one review decision."""
    if state.mode is not InvestigationMode.ADAPTIVE:
        return False
    if state.plan is None or state.termination_reason is not None:
        return False
    return any(
        record.executed and record.step > state.plan_decision_at_step for record in state.tool_calls
    )
