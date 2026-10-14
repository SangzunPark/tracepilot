"""Week 4 Adaptive runner: initial plan, one plan review (KEEP or REPLACE) per
fresh executed Observation, an append-only decision trace, and the shared
budget, policy, and synthesis contracts.

Main flows run against the in-memory MCP server; budget, termination, and
failure tests use stub clients, so the runner's own propagation is observed
without the mcp.Client task group's ExceptionGroup wrapping. Case data only
drives mechanics.
"""

import ast
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult
from pydantic import JsonValue, ValidationError

import tracepilot.investigation.adaptive_runner as adaptive_module
from tests.investigation.executor_support import SpyClient, StubClient
from tests.investigation.scripted_provider import ScriptedProvider, ScriptExhaustedError
from tests.mcp.support import build_service
from tracepilot.investigation.adaptive_runner import (
    AdaptiveRunResult,
    PlanDecisionRecord,
    run_adaptive,
)
from tracepilot.investigation.executor import ToolExecutionError, ToolExecutor
from tracepilot.investigation.models import (
    BlockReason,
    InitialInvestigationContext,
    InvestigationMode,
    InvestigationPlan,
    InvestigationState,
    PlanStep,
    TerminationReason,
    ToolCallRecord,
    ToolRequest,
)
from tracepilot.investigation.provider import (
    IncidentBrief,
    KeepPlanDecision,
    PlanReviewDecision,
    ProviderView,
    RevisionProposal,
    SynthesisProposal,
)
from tracepilot.mcp.server import create_mcp_server

CASE_ID = "INC-001"
INCIDENT = IncidentBrief(
    started_at=datetime(2026, 1, 12, 14, 0, tzinfo=UTC),
    affected_services=("checkout-api",),
    symptom="Checkout requests are failing.",
)
CONTEXT = InitialInvestigationContext(
    focus_services=("checkout-api",), initial_question="What is happening?"
)
SYNTHESIS = SynthesisProposal(
    root_cause_claim="claim", cited_evidence_ids=("log-001",), explanation="explanation"
)


def step(tool: str, **arguments: JsonValue) -> PlanStep:
    return PlanStep(request=ToolRequest(tool=tool, arguments=arguments))


LOGS = step("search_application_logs", service="checkout-api")
DEPLOYS = step("get_deployment_history", service="checkout-api")
METRICS = step("query_service_metrics", service="checkout-api")
RUNBOOKS = step("search_runbooks", query="connection")
LOGS_ERROR = step("search_application_logs", service="checkout-api", limit=0)
FORBIDDEN = step("get_feature_flag_history", service="checkout-api", case_id="INC-007")
INVALID = step("search_runbooks")
UNKNOWN = step("delete_database", force=True)


def plan_of(*steps: PlanStep) -> InvestigationPlan:
    return InvestigationPlan(steps=steps)


def keep(hypothesis: str | None = "kept", *questions: str) -> KeepPlanDecision:
    return KeepPlanDecision(working_hypothesis=hypothesis, open_questions=questions)


def replace(new_plan: InvestigationPlan, hypothesis: str | None = "replaced") -> RevisionProposal:
    return RevisionProposal(plan=new_plan, working_hypothesis=hypothesis, open_questions=("next?",))


@dataclass(frozen=True)
class RecordingExecutor(ToolExecutor):
    """Captures (plan, plan_version, cursor, plan_decision_at_step) per attempt."""

    seen: list[tuple[InvestigationPlan | None, int, int, int]] = field(default_factory=list)

    async def attempt(self, state: InvestigationState, request: ToolRequest) -> ToolCallRecord:
        self.seen.append(
            (state.plan, state.plan_version, state.current_step_index, state.plan_decision_at_step)
        )
        return await super().attempt(state, request)


def scripted(
    initial: InvestigationPlan, *reviews: PlanReviewDecision, **extra: Any
) -> ScriptedProvider:
    return ScriptedProvider(
        initial_plans=[initial], reviews=reviews, syntheses=[SYNTHESIS], **extra
    )


def run_in_memory(
    provider: ScriptedProvider, **executor_options: Any
) -> tuple[AdaptiveRunResult, SpyClient, RecordingExecutor]:
    async def main() -> tuple[AdaptiveRunResult, SpyClient, RecordingExecutor]:
        async with Client(create_mcp_server(build_service())) as client:
            spy = SpyClient(client)
            executor = RecordingExecutor(spy, **executor_options)
            result = await run_adaptive(
                case_id=CASE_ID,
                incident=INCIDENT,
                context=CONTEXT,
                executor=executor,
                provider=provider,
            )
            return result, spy, executor

    return asyncio.run(main())


def empty_results_client() -> StubClient:
    return StubClient(result=CallToolResult(content=[], structured_content={"result": []}))


def run_with(client: StubClient, provider: Any, **executor_options: Any) -> AdaptiveRunResult:
    return asyncio.run(
        run_adaptive(
            case_id=CASE_ID,
            incident=INCIDENT,
            context=CONTEXT,
            executor=ToolExecutor(client, **executor_options),
            provider=provider,
        )
    )


def counts(provider: ScriptedProvider) -> tuple[int, int, int]:
    calls = provider.calls
    return calls["propose_initial_plan"], calls["review_plan"], calls["synthesize"]


def runbooks(*queries: str) -> InvestigationPlan:
    return plan_of(*(step("search_runbooks", query=query) for query in queries))


# ---------------------------------------------------------------------------
# Initial planning
# ---------------------------------------------------------------------------


def test_initial_planning_view_and_single_call() -> None:
    provider = scripted(InvestigationPlan())

    run_with(empty_results_client(), provider, max_steps=4)

    operation, view = provider.views[0]
    pre_planning = InvestigationState(
        case_id=CASE_ID, mode=InvestigationMode.ADAPTIVE, initial_context=CONTEXT
    )
    assert operation == "propose_initial_plan"
    assert view == ProviderView.from_state(pre_planning, INCIDENT, 4)
    assert (view.plan, view.plan_version, view.current_step_index) == (None, 0, 0)
    assert view.remaining_steps == 4
    assert not hasattr(view, "mode")
    assert provider.calls["propose_initial_plan"] == 1


def test_empty_initial_plan_ends_plan_exhausted_without_review() -> None:
    provider = scripted(InvestigationPlan())
    client = empty_results_client()

    result = run_with(client, provider)

    assert result.state.termination_reason is TerminationReason.PLAN_EXHAUSTED
    assert (result.state.plan_version, result.state.step_count) == (1, 0)
    assert result.decisions == ()
    assert result.initial_plan == InvestigationPlan()
    assert counts(provider) == (1, 0, 1)
    assert client.calls == []


def test_initial_plan_is_installed_as_returned() -> None:
    initial = plan_of(LOGS, FORBIDDEN, UNKNOWN)
    snapshot = initial.model_copy(deep=True)

    result, _, executor = run_in_memory(scripted(initial, keep()))

    plan, version, cursor, marker = executor.seen[0]
    assert (plan, version, cursor, marker) == (snapshot, 1, 0, 0)
    assert result.initial_plan == snapshot
    assert initial == snapshot


# ---------------------------------------------------------------------------
# KEEP
# ---------------------------------------------------------------------------


def test_keep_mid_plan_continues_the_same_plan() -> None:
    initial = plan_of(LOGS, DEPLOYS)
    provider = scripted(initial, keep("h1", "q1"), keep("h2"))

    result, spy, executor = run_in_memory(provider)

    # The second attempt runs the next step of the same plan after KEEP.
    assert executor.seen[1] == (initial, 1, 1, 1)
    first = result.decisions[0]
    assert (first.after_step, first.trigger, first.decision) == (1, "mid_plan", "keep")
    assert (first.previous_plan_version, first.new_plan_version) == (1, 1)
    assert (first.previous_plan, first.previous_cursor, first.replacement_plan) == (
        initial,
        1,
        None,
    )
    assert (first.previous_working_hypothesis, first.new_working_hypothesis) == (None, "h1")
    assert (first.previous_open_questions, first.new_open_questions) == ((), ("q1",))
    assert [name for name, _ in spy.calls] == ["search_application_logs", "get_deployment_history"]


def test_keep_replaces_hypothesis_and_questions_only() -> None:
    initial = plan_of(LOGS, DEPLOYS)
    result, _, _ = run_in_memory(scripted(initial, keep("h1", "q1"), keep("h2", "q2", "q3")))
    state = result.state

    assert (state.plan, state.plan_version, state.current_step_index) == (initial, 1, 2)
    assert (state.working_hypothesis, state.open_questions) == ("h2", ("q2", "q3"))
    assert state.plan_decision_at_step == 2
    assert len(state.tool_calls) == 2


def test_keep_at_plan_exhaustion_is_valid_and_ends_plan_exhausted() -> None:
    initial = plan_of(LOGS)
    provider = scripted(initial, keep("still the deploy"))

    result, _, _ = run_in_memory(provider)

    [decision] = result.decisions
    assert (decision.trigger, decision.decision) == ("plan_exhausted", "keep")
    assert decision.replacement_plan is None
    assert result.state.plan == initial  # not converted to an empty plan
    assert result.state.plan_version == 1
    assert result.state.termination_reason is TerminationReason.PLAN_EXHAUSTED
    assert counts(provider) == (1, 1, 1)


# ---------------------------------------------------------------------------
# REPLACE
# ---------------------------------------------------------------------------


def test_replace_mid_plan_installs_future_actions_exactly() -> None:
    initial = plan_of(LOGS, DEPLOYS, METRICS)
    replacement = plan_of(RUNBOOKS)
    snapshot = replacement.model_copy(deep=True)
    provider = scripted(initial, replace(replacement, "flag change"), keep("flag change"))

    result, spy, executor = run_in_memory(provider)
    state = result.state

    # Next attempt runs the replacement's first step: version 2, cursor 0.
    assert executor.seen[1] == (snapshot, 2, 0, 1)
    assert (state.plan, state.plan_version) == (snapshot, 2)
    # DEPLOYS and METRICS were abandoned; history stays in tool_calls.
    assert [name for name, _ in spy.calls] == ["search_application_logs", "search_runbooks"]
    assert [record.tool for record in state.tool_calls] == [
        "search_application_logs",
        "search_runbooks",
    ]
    assert state.working_hypothesis == "flag change"

    first = result.decisions[0]
    assert (first.after_step, first.trigger, first.decision) == (1, "mid_plan", "replace")
    assert (first.previous_plan_version, first.new_plan_version) == (1, 2)
    assert (first.previous_plan, first.previous_cursor) == (initial, 1)
    assert first.replacement_plan == snapshot
    assert (first.previous_working_hypothesis, first.new_working_hypothesis) == (
        None,
        "flag change",
    )
    assert first.new_open_questions == ("next?",)
    assert result.initial_plan == initial
    assert replacement == snapshot


def test_replace_with_empty_plan_ends_plan_exhausted() -> None:
    provider = scripted(plan_of(LOGS, DEPLOYS), replace(InvestigationPlan()))

    result, spy, _ = run_in_memory(provider)

    [decision] = result.decisions
    assert (decision.trigger, decision.decision) == ("mid_plan", "replace")
    assert decision.replacement_plan == InvestigationPlan()
    assert result.state.plan == InvestigationPlan()
    assert result.state.plan_version == 2
    assert result.state.termination_reason is TerminationReason.PLAN_EXHAUSTED
    assert len(spy.calls) == 1


def test_replacement_actions_are_not_sanitized() -> None:
    replacement = plan_of(FORBIDDEN, UNKNOWN, RUNBOOKS)
    provider = scripted(plan_of(LOGS), replace(replacement), keep())

    result, _, _ = run_in_memory(provider)

    assert result.state.plan == replacement
    assert [record.block_reason for record in result.state.tool_calls] == [
        None,
        BlockReason.FORBIDDEN_ARGUMENT,
        BlockReason.UNKNOWN_TOOL,
        None,
    ]


# ---------------------------------------------------------------------------
# Multiple reviews and the trace
# ---------------------------------------------------------------------------


def test_keep_then_replace_then_keep_then_replace() -> None:
    initial = plan_of(LOGS, DEPLOYS, METRICS)
    second = plan_of(METRICS, RUNBOOKS)
    provider = scripted(
        initial, keep("h1"), replace(second, "h2"), keep("h3"), replace(InvestigationPlan(), "h4")
    )

    result, spy, executor = run_in_memory(provider)
    decisions = result.decisions

    assert [(d.after_step, d.trigger, d.decision) for d in decisions] == [
        (1, "mid_plan", "keep"),
        (2, "mid_plan", "replace"),
        (3, "mid_plan", "keep"),
        (4, "plan_exhausted", "replace"),
    ]
    assert [(d.previous_plan_version, d.new_plan_version) for d in decisions] == [
        (1, 1),
        (1, 2),
        (2, 2),
        (2, 3),
    ]
    assert [d.previous_plan for d in decisions] == [initial, initial, second, second]
    assert [d.previous_cursor for d in decisions] == [1, 2, 1, 2]
    assert [d.new_working_hypothesis for d in decisions] == ["h1", "h2", "h3", "h4"]
    assert [d.previous_working_hypothesis for d in decisions] == [None, "h1", "h2", "h3"]
    assert [marker for *_, marker in executor.seen] == [0, 1, 2, 3]
    assert result.state.plan_decision_at_step == 4
    assert result.state.plan_version == 3
    assert result.initial_plan == initial
    assert len(spy.calls) == 4
    assert counts(provider) == (1, 4, 1)


def test_decision_record_is_not_part_of_state_or_provider_view() -> None:
    for model in (InvestigationState, ProviderView):
        assert not {"decisions", "initial_plan"} & set(model.model_fields)


def test_review_view_shows_the_pre_decision_state() -> None:
    initial = plan_of(LOGS, DEPLOYS)
    provider = scripted(initial, keep(), keep())

    run_in_memory(provider, max_steps=6)

    _, review_view = provider.views[1]
    assert provider.views[1][0] == "review_plan"
    assert (review_view.plan, review_view.current_step_index) == (initial, 1)
    assert len(review_view.tool_calls) == 1
    assert review_view.remaining_steps == 5
    assert not hasattr(review_view, "mode")


@pytest.mark.parametrize(
    "fields",
    [
        {"decision": "keep", "replacement_plan": {"steps": []}},
        {"decision": "keep", "new_plan_version": 2},
        {"decision": "replace", "replacement_plan": None, "new_plan_version": 2},
        {"decision": "replace", "replacement_plan": {"steps": []}, "new_plan_version": 1},
        {"trigger": "plan_exhausted"},
        {"after_step": 0},
        {"confidence": 0.9},
    ],
    ids=[
        "keep-with-plan",
        "keep-new-version",
        "replace-without-plan",
        "replace-same-version",
        "wrong-trigger",
        "zero-step",
        "extra-field",
    ],
)
def test_decision_record_rejects_inconsistent_values(fields: dict[str, Any]) -> None:
    valid: dict[str, Any] = {
        "after_step": 1,
        "trigger": "mid_plan",
        "decision": "keep",
        "previous_plan_version": 1,
        "new_plan_version": 1,
        "previous_plan": plan_of(LOGS, DEPLOYS).model_dump(),
        "previous_cursor": 1,
        "replacement_plan": None,
        "previous_working_hypothesis": None,
        "new_working_hypothesis": "h",
        "previous_open_questions": (),
        "new_open_questions": (),
    }
    PlanDecisionRecord.model_validate(valid)
    with pytest.raises(ValidationError):
        PlanDecisionRecord.model_validate({**valid, **fields})


def test_result_shape() -> None:
    assert set(AdaptiveRunResult.model_fields) == {
        "state",
        "synthesis",
        "initial_plan",
        "decisions",
    }
    result = run_with(empty_results_client(), scripted(InvestigationPlan()))
    with pytest.raises(ValidationError):
        result.decisions = ()


# ---------------------------------------------------------------------------
# Blocked actions and executed errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("blocked_step", "reason"),
    [
        (FORBIDDEN, BlockReason.FORBIDDEN_ARGUMENT),
        (INVALID, BlockReason.INVALID_ARGUMENTS),
        (UNKNOWN, BlockReason.UNKNOWN_TOOL),
        (LOGS, BlockReason.DUPLICATE),
    ],
    ids=["forbidden", "invalid", "unknown", "duplicate"],
)
def test_blocked_middle_step_creates_no_review(blocked_step: PlanStep, reason: BlockReason) -> None:
    provider = scripted(plan_of(LOGS, blocked_step, DEPLOYS), keep(), keep())

    result, _, _ = run_in_memory(provider)

    assert result.state.tool_calls[1].block_reason is reason
    assert [d.after_step for d in result.decisions] == [1, 3]
    assert counts(provider) == (1, 2, 1)


@pytest.mark.parametrize("blocked_step", [FORBIDDEN, INVALID, UNKNOWN, LOGS], ids=str)
def test_blocked_final_step_ends_plan_exhausted_without_review(blocked_step: PlanStep) -> None:
    provider = scripted(plan_of(LOGS, blocked_step), keep())

    result, _, _ = run_in_memory(provider)

    assert [d.after_step for d in result.decisions] == [1]
    assert result.state.step_count == 2
    assert result.state.termination_reason is TerminationReason.PLAN_EXHAUSTED


def test_executed_mcp_error_triggers_exactly_one_review() -> None:
    provider = scripted(plan_of(LOGS_ERROR), keep("the error is informative"))

    result, _, _ = run_in_memory(provider)

    observation = result.state.tool_calls[0].observation
    assert observation is not None and observation.is_error
    [decision] = result.decisions
    assert (decision.after_step, decision.trigger) == (1, "plan_exhausted")
    assert counts(provider) == (1, 1, 1)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_final_budget_step_ends_budget_exhausted_without_review() -> None:
    provider = scripted(runbooks("a", "b", "c"), keep())

    result = run_with(empty_results_client(), provider, max_steps=2)

    assert result.state.step_count == 2
    assert result.state.tool_calls[-1].executed
    assert result.state.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert [d.after_step for d in result.decisions] == [1]
    assert counts(provider) == (1, 1, 1)


def test_budget_spans_plan_versions() -> None:
    provider = scripted(runbooks("a", "b"), replace(runbooks("c", "d", "e")), keep(), keep())
    client = empty_results_client()

    result = run_with(client, provider, max_steps=3)

    assert result.state.step_count == 3
    assert result.state.plan_version == 2
    assert result.state.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert [d.after_step for d in result.decisions] == [1, 2]
    assert len(client.calls) == 3


def test_blocked_attempts_consume_budget_without_reviews() -> None:
    provider = scripted(plan_of(FORBIDDEN, UNKNOWN, RUNBOOKS))

    result = run_with(empty_results_client(), provider, max_steps=2)

    assert result.state.step_count == 2
    assert result.state.logical_tool_calls == 0
    assert result.state.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert counts(provider) == (1, 0, 1)


# ---------------------------------------------------------------------------
# Duplicates across plan versions
# ---------------------------------------------------------------------------


def test_duplicate_detection_spans_replacement_plans() -> None:
    same_as_logs = step("search_application_logs", limit=50, query=None, service="checkout-api")
    provider = scripted(plan_of(LOGS, DEPLOYS), replace(plan_of(same_as_logs, RUNBOOKS)), keep())

    result, spy, _ = run_in_memory(provider)
    records = result.state.tool_calls

    assert records[1].block_reason is BlockReason.DUPLICATE
    assert records[1].fingerprint == records[0].fingerprint
    assert [name for name, _ in spy.calls] == ["search_application_logs", "search_runbooks"]
    # The duplicate was blocked, so only LOGS and RUNBOOKS produced reviews.
    assert [d.after_step for d in result.decisions] == [1, 3]


# ---------------------------------------------------------------------------
# Progress and termination
# ---------------------------------------------------------------------------


class _EndlessReviewer(ScriptedProvider):
    """Never runs out of review decisions: always KEEP or always REPLACE with
    fresh, never-duplicate actions. Records the event order with the executor."""

    def __init__(self, mode: str, events: list[str], initial: InvestigationPlan) -> None:
        super().__init__(initial_plans=[initial], syntheses=[SYNTHESIS])
        self.mode = mode
        self.events = events

    async def review_plan(self, view: ProviderView) -> PlanReviewDecision:
        self.calls["review_plan"] += 1
        self.events.append("review")
        if self.mode == "keep":
            return keep()
        return replace(
            runbooks(f"r{self.calls['review_plan']}-a", f"r{self.calls['review_plan']}-b")
        )


@dataclass(frozen=True)
class _EventExecutor(ToolExecutor):
    events: list[str] = field(default_factory=list)

    async def attempt(self, state: InvestigationState, request: ToolRequest) -> ToolCallRecord:
        record = await super().attempt(state, request)
        self.events.append("executed" if record.executed else "blocked")
        return record


@pytest.mark.parametrize("mode", ["keep", "replace"])
@pytest.mark.parametrize(
    "initial",
    [runbooks(*[f"q{i}" for i in range(20)]), plan_of(RUNBOOKS, FORBIDDEN, UNKNOWN, INVALID)],
    ids=["long-plan", "mostly-blocked"],
)
@pytest.mark.parametrize("max_steps", [1, 4, 15])
def test_loop_terminates_with_one_review_per_executed_observation(
    mode: str, initial: InvestigationPlan, max_steps: int
) -> None:
    events: list[str] = []
    provider = _EndlessReviewer(mode, events, initial)
    executor = _EventExecutor(empty_results_client(), max_steps=max_steps, events=events)

    result = asyncio.run(
        run_adaptive(
            case_id=CASE_ID,
            incident=INCIDENT,
            context=CONTEXT,
            executor=executor,
            provider=provider,
        )
    )

    actions = [event for event in events if event != "review"]
    assert len(actions) == result.state.step_count <= max_steps
    assert events.count("review") == len(result.decisions) == provider.calls["review_plan"]
    final_unreviewed = events[-1] == "executed"
    assert events.count("review") == events.count("executed") - (1 if final_unreviewed else 0)
    if final_unreviewed:
        assert result.state.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    for previous, following in zip(events, events[1:], strict=False):
        assert not (previous == "review" and following == "review")
        if following == "review":
            assert previous == "executed"
    assert [d.after_step for d in result.decisions] == sorted(
        {d.after_step for d in result.decisions}
    )
    assert counts(provider) == (1, events.count("review"), 1)


def test_runs_are_deterministic() -> None:
    def once() -> AdaptiveRunResult:
        initial = plan_of(LOGS, DEPLOYS, METRICS)
        return run_in_memory(scripted(initial, keep("h1"), replace(plan_of(RUNBOOKS)), keep()))[0]

    assert once() == once()


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def test_initial_planning_failure_propagates() -> None:
    client = empty_results_client()
    provider = ScriptedProvider(syntheses=[SYNTHESIS])

    with pytest.raises(ScriptExhaustedError):
        run_with(client, provider)

    assert counts(provider) == (1, 0, 0)
    assert client.calls == []


def test_review_failure_propagates_without_further_actions() -> None:
    client = empty_results_client()
    provider = ScriptedProvider(initial_plans=[runbooks("a", "b")], syntheses=[SYNTHESIS])

    with pytest.raises(ScriptExhaustedError, match="review_plan"):
        run_with(client, provider)

    assert len(client.calls) == 1
    assert counts(provider) == (1, 1, 0)


def test_tool_execution_error_propagates_and_skips_synthesis() -> None:
    client = StubClient(error=OSError("pipe closed"))
    provider = scripted(runbooks("a", "b"), keep())

    with pytest.raises(ToolExecutionError):
        run_with(client, provider)

    assert counts(provider) == (1, 0, 0)


def test_synthesis_failure_propagates() -> None:
    provider = ScriptedProvider(initial_plans=[InvestigationPlan()])
    with pytest.raises(ScriptExhaustedError, match="synthesize"):
        run_with(empty_results_client(), provider)
    assert counts(provider) == (1, 0, 1)


class _MalformedReviewer(ScriptedProvider):
    async def review_plan(self, view: ProviderView) -> Any:
        self.calls["review_plan"] += 1
        return {"decision": "keep", "working_hypothesis": None, "open_questions": []}


def test_malformed_review_result_propagates_unrepaired() -> None:
    client = empty_results_client()
    provider = _MalformedReviewer(initial_plans=[runbooks("a", "b")], syntheses=[SYNTHESIS])

    with pytest.raises(TypeError):
        run_with(client, provider)

    assert len(client.calls) == 1
    assert provider.calls["synthesize"] == 0


def test_invalid_review_values_fail_validation() -> None:
    with pytest.raises(ValidationError):
        KeepPlanDecision(working_hypothesis="h" * 1001, open_questions=())


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


def test_runner_has_its_own_loop_and_no_data_access() -> None:
    tree = ast.parse(Path(adaptive_module.__file__).read_text())
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "execute_static_plan" not in imported_names
    for forbidden in (
        "tracepilot.investigation.playbook",
        "tracepilot.investigation.fixed_runner",
        "tracepilot.investigation.plan_once_runner",
        "tracepilot.domain",
        "tracepilot.diagnostics",
        "openai",
        "anthropic",
    ):
        assert not any(module.startswith(forbidden) for module in imported_modules)
