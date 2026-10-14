"""Week 4 Plan Once runner: one provider plan, used exactly as returned and
never changed, executed through the shared static-plan loop.

Runner integration tests use the in-memory MCP server; failure tests use stub
clients so the runner's own propagation is observed without the mcp.Client
task group's ExceptionGroup wrapping. Case data only drives mechanics.
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
from pydantic import JsonValue

import tracepilot.investigation.plan_once_runner as plan_once_module
from tests.investigation.executor_support import SpyClient, StubClient
from tests.investigation.scripted_provider import ScriptedProvider, ScriptExhaustedError
from tests.mcp.support import build_service
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
from tracepilot.investigation.plan_once_runner import PlanOnceRunResult, run_plan_once
from tracepilot.investigation.policies import (
    execution_arguments,
    is_allowed_tool,
    normalize_call,
)
from tracepilot.investigation.provider import (
    IncidentBrief,
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


# permitted, executed MCP error, forbidden case_id, duplicate of step 1,
# unknown tool, later permitted action.
MIXED_PLAN = InvestigationPlan(
    steps=(
        step("search_application_logs", service="checkout-api"),
        step("search_application_logs", service="checkout-api", limit=0),
        step("get_deployment_history", service="checkout-api", case_id="INC-007"),
        step("search_application_logs", limit=50, service="checkout-api"),
        step("delete_database", force=True),
        step("search_runbooks", query="connection"),
    )
)
MIXED_OUTCOMES: list[BlockReason | None] = [
    None,
    None,
    BlockReason.FORBIDDEN_ARGUMENT,
    BlockReason.DUPLICATE,
    BlockReason.UNKNOWN_TOOL,
    None,
]


@dataclass(frozen=True)
class RecordingExecutor(ToolExecutor):
    """Captures the plan, version, and cursor seen at every attempt."""

    seen: list[tuple[InvestigationPlan | None, int, int]] = field(default_factory=list)

    async def attempt(self, state: InvestigationState, request: ToolRequest) -> ToolCallRecord:
        self.seen.append((state.plan, state.plan_version, state.current_step_index))
        return await super().attempt(state, request)


def scripted(*plans: InvestigationPlan, **extra: Any) -> ScriptedProvider:
    return ScriptedProvider(initial_plans=plans, syntheses=[SYNTHESIS], **extra)


def run_in_memory(
    provider: ScriptedProvider, **executor_options: Any
) -> tuple[PlanOnceRunResult, SpyClient, RecordingExecutor]:
    async def main() -> tuple[PlanOnceRunResult, SpyClient, RecordingExecutor]:
        async with Client(create_mcp_server(build_service())) as client:
            spy = SpyClient(client)
            executor = RecordingExecutor(spy, **executor_options)
            result = await run_plan_once(
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


def run_with(
    client: StubClient, provider: ScriptedProvider, **executor_options: Any
) -> PlanOnceRunResult:
    return asyncio.run(
        run_plan_once(
            case_id=CASE_ID,
            incident=INCIDENT,
            context=CONTEXT,
            executor=ToolExecutor(client, **executor_options),
            provider=provider,
        )
    )


def runbook_plan(count: int) -> InvestigationPlan:
    return InvestigationPlan(
        steps=tuple(step("search_runbooks", query=f"q{i}") for i in range(count))
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_initial_view_is_the_pre_planning_state() -> None:
    provider = scripted(MIXED_PLAN)
    run_in_memory(provider)

    operation, view = provider.views[0]
    pre_planning = InvestigationState(
        case_id=CASE_ID, mode=InvestigationMode.PLAN_ONCE, initial_context=CONTEXT
    )
    assert operation == "propose_initial_plan"
    assert (view.plan, view.plan_version, view.current_step_index) == (None, 0, 0)
    assert view.tool_calls == ()
    assert (view.incident, view.initial_context) == (INCIDENT, CONTEXT)
    assert view == ProviderView.from_state(pre_planning, INCIDENT, 15)


def test_plan_is_installed_at_version_one_with_cursor_zero() -> None:
    _, _, executor = run_in_memory(scripted(MIXED_PLAN))
    plan, version, cursor = executor.seen[0]
    assert (plan, version, cursor) == (MIXED_PLAN, 1, 0)


def test_provider_views_do_not_expose_mode() -> None:
    provider = scripted(MIXED_PLAN)
    run_in_memory(provider)
    for _, view in provider.views:
        assert not hasattr(view, "mode")
        assert "plan_once" not in view.model_dump_json()


def test_mixed_plan_runs_to_plan_exhausted() -> None:
    result, spy, _ = run_in_memory(scripted(MIXED_PLAN))
    state = result.state

    assert state.mode is InvestigationMode.PLAN_ONCE
    assert [record.block_reason for record in state.tool_calls] == MIXED_OUTCOMES
    error = state.tool_calls[1].observation
    assert error is not None and error.is_error
    assert state.current_step_index == 6
    assert (state.step_count, state.logical_tool_calls, state.execution_attempts) == (6, 3, 3)
    assert state.termination_reason is TerminationReason.PLAN_EXHAUSTED
    assert result.synthesis == SYNTHESIS
    assert len(spy.calls) == 3


def test_runs_are_deterministic() -> None:
    first, _, _ = run_in_memory(scripted(MIXED_PLAN))
    second, _, _ = run_in_memory(scripted(MIXED_PLAN))
    assert first == second


# ---------------------------------------------------------------------------
# Plan immutability
# ---------------------------------------------------------------------------


def _expected_mcp_call(request: ToolRequest) -> tuple[str, dict[str, JsonValue]]:
    assert is_allowed_tool(request.tool)
    call = normalize_call(request.tool, request.arguments)
    return request.tool, execution_arguments(call, CASE_ID)


def test_initial_plan_is_never_changed() -> None:
    snapshot = MIXED_PLAN.model_copy(deep=True)
    provider = scripted(MIXED_PLAN)

    result, spy, executor = run_in_memory(provider)
    final = result.state

    # Every attempt saw the same plan at version 1, with the cursor advancing.
    assert len(executor.seen) == len(snapshot.steps)
    for plan, version, _ in executor.seen:
        assert plan == snapshot
        assert version == 1
    assert [cursor for _, _, cursor in executor.seen] == list(range(len(snapshot.steps)))

    # The final plan equals the provider's plan as returned (an equal immutable
    # copy is acceptable; object identity is not part of the contract).
    assert final.plan == snapshot
    assert final.plan_version == 1
    assert final.plan_decision_at_step == 0
    assert [(s.request.tool, s.request.arguments, s.rationale) for s in final.plan.steps] == [
        (s.request.tool, s.request.arguments, s.rationale) for s in snapshot.steps
    ]

    # The scripted object itself was not mutated, and nothing was revised.
    assert MIXED_PLAN == snapshot
    assert provider.calls["review_plan"] == 0

    # MCP saw exactly the permitted steps, in plan order, with runtime case_id.
    permitted = [snapshot.steps[i] for i, outcome in enumerate(MIXED_OUTCOMES) if outcome is None]
    assert spy.calls == [_expected_mcp_call(s.request) for s in permitted]


def test_synthesis_view_carries_the_unchanged_plan() -> None:
    provider = scripted(MIXED_PLAN)
    run_in_memory(provider)
    operation, view = provider.views[-1]
    assert operation == "synthesize"
    assert view.plan == MIXED_PLAN
    assert view.plan_version == 1
    assert view.termination_reason is TerminationReason.PLAN_EXHAUSTED


# ---------------------------------------------------------------------------
# Provider call counts
# ---------------------------------------------------------------------------


def _counts(provider: ScriptedProvider) -> tuple[int, int, int]:
    calls = provider.calls
    return calls["propose_initial_plan"], calls["review_plan"], calls["synthesize"]


@pytest.mark.parametrize(
    "plan", [MIXED_PLAN, InvestigationPlan(), runbook_plan(20)], ids=["normal", "empty", "budget"]
)
def test_normal_call_counts(plan: InvestigationPlan) -> None:
    unused_revision = RevisionProposal(
        plan=InvestigationPlan(), working_hypothesis=None, open_questions=()
    )
    provider = scripted(plan, reviews=[unused_revision])
    run_with(empty_results_client(), provider)
    assert _counts(provider) == (1, 0, 1)


def test_initial_planning_failure_propagates_without_tools_or_synthesis() -> None:
    client = empty_results_client()
    provider = ScriptedProvider(syntheses=[SYNTHESIS])

    with pytest.raises(ScriptExhaustedError):
        run_with(client, provider)

    assert _counts(provider) == (1, 0, 0)
    assert client.calls == []


class _PlanningFails(ScriptedProvider):
    async def propose_initial_plan(self, view: ProviderView) -> InvestigationPlan:
        self.calls["propose_initial_plan"] += 1
        raise RuntimeError("model unavailable")


def test_initial_planning_exception_propagates_unchanged() -> None:
    client = empty_results_client()
    provider = _PlanningFails(syntheses=[SYNTHESIS])
    with pytest.raises(RuntimeError, match="model unavailable"):
        run_with(client, provider)
    assert _counts(provider) == (1, 0, 0)
    assert client.calls == []


def test_tool_execution_error_propagates_and_skips_synthesis() -> None:
    client = StubClient(error=OSError("pipe closed"))
    provider = scripted(runbook_plan(3))

    with pytest.raises(ToolExecutionError):
        run_with(client, provider)

    assert _counts(provider) == (1, 0, 0)
    assert len(client.calls) == 1


def test_synthesis_failure_propagates() -> None:
    provider = ScriptedProvider(initial_plans=[runbook_plan(2)])

    with pytest.raises(ScriptExhaustedError):
        run_with(empty_results_client(), provider)

    assert _counts(provider) == (1, 0, 1)


# ---------------------------------------------------------------------------
# Budget and termination
# ---------------------------------------------------------------------------


def test_custom_budget_reaches_both_provider_views() -> None:
    provider = scripted(runbook_plan(3))

    result = run_with(empty_results_client(), provider, max_steps=4)

    (_, planning_view), (_, synthesis_view) = provider.views
    assert planning_view.remaining_steps == 4
    assert result.state.step_count == 3
    assert synthesis_view.remaining_steps == 4 - 3


def test_default_budget_reaches_the_planning_view() -> None:
    provider = scripted(InvestigationPlan())
    run_with(empty_results_client(), provider)
    assert provider.views[0][1].remaining_steps == 15


def test_empty_plan_ends_plan_exhausted_without_tool_calls() -> None:
    client = empty_results_client()
    result = run_with(client, scripted(InvestigationPlan()))

    assert result.state.plan == InvestigationPlan()
    assert result.state.plan_version == 1
    assert result.state.step_count == 0
    assert result.state.termination_reason is TerminationReason.PLAN_EXHAUSTED
    assert client.calls == []


def test_plan_ending_exactly_at_the_budget_is_plan_exhausted() -> None:
    result = run_with(empty_results_client(), scripted(runbook_plan(4)), max_steps=4)
    assert result.state.step_count == 4
    assert result.state.termination_reason is TerminationReason.PLAN_EXHAUSTED


def test_plan_longer_than_the_budget_is_not_truncated_and_ends_budget_exhausted() -> None:
    plan = runbook_plan(20)
    client = empty_results_client()

    result = run_with(client, scripted(plan))

    assert result.state.plan == plan
    assert result.state.plan is not None and len(result.state.plan.steps) == 20
    assert result.state.step_count == 15
    assert result.state.current_step_index == 15
    assert result.state.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert len(client.calls) == 15


def test_blocked_steps_are_recorded_and_the_cursor_advances() -> None:
    plan = InvestigationPlan(
        steps=(
            step("search_runbooks", query="a", case_id=CASE_ID),
            step("search_runbooks"),
            step("search_runbooks", query="a"),
            step("search_runbooks", query="a"),
        )
    )
    client = empty_results_client()

    result = run_with(client, scripted(plan))

    assert [record.block_reason for record in result.state.tool_calls] == [
        BlockReason.FORBIDDEN_ARGUMENT,
        BlockReason.INVALID_ARGUMENTS,
        None,
        BlockReason.DUPLICATE,
    ]
    assert result.state.current_step_index == 4
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Result and boundaries
# ---------------------------------------------------------------------------


def test_result_is_frozen_with_exactly_state_and_synthesis() -> None:
    result = run_with(empty_results_client(), scripted(InvestigationPlan()))
    assert set(PlanOnceRunResult.model_fields) == {"state", "synthesis"}
    with pytest.raises(ValueError):
        result.synthesis = SYNTHESIS


def test_runner_does_not_use_the_fixed_playbook_or_data_access() -> None:
    tree = ast.parse(Path(plan_once_module.__file__).read_text())
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    for forbidden in (
        "tracepilot.investigation.playbook",
        "tracepilot.investigation.fixed_runner",
        "tracepilot.domain",
        "tracepilot.diagnostics",
    ):
        assert not any(module.startswith(forbidden) for module in imported)
