"""Week 4 Fixed Playbook runner over the in-memory MCP server.

The case data only drives mechanics (steps, cursor, termination, synthesis
calls); nothing here scores or inspects ground truth.
"""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult

from tests.investigation.executor_support import SpyClient, StubClient
from tests.investigation.scripted_provider import ScriptedProvider, ScriptExhaustedError
from tests.mcp.support import build_service
from tracepilot.domain.catalog import SERVICE_CATALOG
from tracepilot.investigation import fixed_runner
from tracepilot.investigation.executor import ToolExecutionError, ToolExecutor
from tracepilot.investigation.fixed_runner import FixedRunResult, run_fixed_playbook
from tracepilot.investigation.models import (
    BlockReason,
    InitialInvestigationContext,
    InvestigationMode,
    InvestigationPlan,
    PlanStep,
    TerminationReason,
    ToolRequest,
)
from tracepilot.investigation.plan_execution import record_termination
from tracepilot.investigation.playbook import PlaybookConfigurationError
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
SYNTHESIS = SynthesisProposal(
    root_cause_claim="claim", cited_evidence_ids=("log-001",), explanation="explanation"
)


def context(*focus: str) -> InitialInvestigationContext:
    return InitialInvestigationContext(focus_services=focus, initial_question="What is happening?")


def run(
    focus: tuple[str, ...],
    provider: ScriptedProvider | None = None,
    **executor_options: Any,
) -> tuple[FixedRunResult, SpyClient, ScriptedProvider]:
    scripted = provider if provider is not None else ScriptedProvider(syntheses=[SYNTHESIS])

    async def main() -> tuple[FixedRunResult, SpyClient]:
        async with Client(create_mcp_server(build_service())) as client:
            spy = SpyClient(client)
            result = await run_fixed_playbook(
                case_id=CASE_ID,
                incident=INCIDENT,
                context=context(*focus),
                executor=ToolExecutor(spy, **executor_options),
                provider=scripted,
            )
            return result, spy

    result, spy = asyncio.run(main())
    return result, spy, scripted


def request(tool: str, **arguments: Any) -> PlanStep:
    return PlanStep(request=ToolRequest(tool=tool, arguments=arguments))


def with_plan(monkeypatch: pytest.MonkeyPatch, plan: InvestigationPlan) -> None:
    monkeypatch.setattr(fixed_runner, "build_fixed_playbook", lambda ctx: plan)


# ---------------------------------------------------------------------------
# Normal runs
# ---------------------------------------------------------------------------


def test_checkout_run_executes_the_whole_playbook() -> None:
    result, spy, provider = run(("checkout-api",))
    state = result.state

    assert state.mode is InvestigationMode.FIXED_PLAYBOOK
    assert state.plan is not None and len(state.plan.steps) == 12
    assert (state.plan_version, state.current_step_index) == (1, 12)
    assert state.plan_decision_at_step == 0
    assert (state.step_count, state.logical_tool_calls, state.execution_attempts) == (12, 12, 12)
    assert state.termination_reason is TerminationReason.PLAN_EXHAUSTED
    assert [record.step for record in state.tool_calls] == list(range(1, 13))
    assert all(record.executed for record in state.tool_calls)
    assert [name for name, _ in spy.calls] == [step.request.tool for step in state.plan.steps]
    assert result.synthesis == SYNTHESIS


def test_runtime_case_id_reaches_mcp_and_runbooks_get_none() -> None:
    _, spy, _ = run(("billing-api",))
    for name, arguments in spy.calls:
        assert arguments is not None
        if name == "search_runbooks":
            assert "case_id" not in arguments
        else:
            assert arguments["case_id"] == CASE_ID


def test_fixed_never_plans_or_revises_and_synthesizes_once() -> None:
    _, _, provider = run(("checkout-api",))
    assert provider.calls == {"propose_initial_plan": 0, "review_plan": 0, "synthesize": 1}


def test_synthesis_sees_the_terminated_state() -> None:
    result, _, provider = run(("billing-api",))

    [(operation, view)] = provider.views
    assert operation == "synthesize"
    assert view == ProviderView.from_state(result.state, INCIDENT)
    assert view.termination_reason is TerminationReason.PLAN_EXHAUSTED
    assert view.incident == INCIDENT
    assert view.remaining_steps == 15 - 8


def test_unused_provider_scripts_stay_unused() -> None:
    provider = ScriptedProvider(
        initial_plans=[InvestigationPlan()],
        reviews=[
            RevisionProposal(plan=InvestigationPlan(), working_hypothesis=None, open_questions=())
        ],
        syntheses=[SYNTHESIS],
    )
    run(("postgresql",), provider)
    assert provider.calls["propose_initial_plan"] == provider.calls["review_plan"] == 0


def test_runs_are_deterministic() -> None:
    first, _, _ = run(("checkout-api",))
    second, _, _ = run(("checkout-api",))
    assert first == second


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_sixteen_step_playbook_ends_budget_exhausted_after_fifteen() -> None:
    result, spy, provider = run(tuple(SERVICE_CATALOG))
    state = result.state

    assert state.plan is not None and len(state.plan.steps) == 16
    assert state.step_count == 15
    assert state.current_step_index == 15
    assert state.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert len(spy.calls) == 15
    assert "search_runbooks" not in [name for name, _ in spy.calls]
    assert provider.calls["synthesize"] == 1


def test_small_budget_ends_budget_exhausted() -> None:
    result, spy, _ = run(("checkout-api",), max_steps=3)

    assert result.state.step_count == 3
    assert result.state.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert len(spy.calls) == 3


def test_plan_that_ends_exactly_at_the_budget_is_plan_exhausted() -> None:
    result, _, _ = run(("authentication-service",), max_steps=7)
    assert result.state.step_count == 7
    assert result.state.termination_reason is TerminationReason.PLAN_EXHAUSTED


# ---------------------------------------------------------------------------
# Blocked steps and unusual plans
# ---------------------------------------------------------------------------


def test_blocked_step_is_consumed_and_the_cursor_advances(monkeypatch: pytest.MonkeyPatch) -> None:
    with_plan(
        monkeypatch,
        InvestigationPlan(
            steps=(
                request("get_deployment_history", service="checkout-api"),
                request("get_deployment_history", service="checkout-api", case_id="INC-002"),
                request("get_deployment_history", service="checkout-api"),
                request("search_runbooks", query=""),
            )
        ),
    )

    result, spy, provider = run(("checkout-api",))
    records = result.state.tool_calls

    assert [record.block_reason for record in records] == [
        None,
        BlockReason.FORBIDDEN_ARGUMENT,
        BlockReason.DUPLICATE,
        None,
    ]
    assert result.state.current_step_index == 4
    assert (result.state.step_count, result.state.logical_tool_calls) == (4, 2)
    assert result.state.plan_version == 1
    assert result.state.termination_reason is TerminationReason.PLAN_EXHAUSTED
    assert len(spy.calls) == 2
    assert provider.calls["review_plan"] == 0


def test_empty_plan_terminates_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    with_plan(monkeypatch, InvestigationPlan())

    result, spy, provider = run(("checkout-api",))

    assert result.state.step_count == 0
    assert result.state.termination_reason is TerminationReason.PLAN_EXHAUSTED
    assert spy.calls == []
    assert provider.calls["synthesize"] == 1


def test_executed_mcp_error_does_not_stop_the_playbook(monkeypatch: pytest.MonkeyPatch) -> None:
    with_plan(
        monkeypatch,
        InvestigationPlan(
            steps=(
                request("search_application_logs", service="checkout-api", limit=0),
                request("search_runbooks", query=""),
            )
        ),
    )

    result, _, _ = run(("checkout-api",))

    first, second = result.state.tool_calls
    assert first.observation is not None and first.observation.is_error
    assert second.executed
    assert result.state.termination_reason is TerminationReason.PLAN_EXHAUSTED


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def _run_with(
    executor: ToolExecutor, provider: ScriptedProvider | Any
) -> Callable[[], Coroutine[Any, Any, FixedRunResult]]:
    async def call() -> FixedRunResult:
        return await run_fixed_playbook(
            case_id=CASE_ID,
            incident=INCIDENT,
            context=context("checkout-api"),
            executor=executor,
            provider=provider,
        )

    return call


def test_tool_execution_error_aborts_and_skips_synthesis() -> None:
    client = StubClient(error=OSError("pipe closed"))
    provider = ScriptedProvider(syntheses=[SYNTHESIS])

    with pytest.raises(ToolExecutionError):
        asyncio.run(_run_with(ToolExecutor(client), provider)())

    assert len(client.calls) == 1
    assert provider.calls["synthesize"] == 0


def _empty_results_client() -> StubClient:
    # No task group around the run, so the runner's own propagation is observed
    # directly (the in-memory mcp.Client wraps errors in an ExceptionGroup).
    return StubClient(result=CallToolResult(content=[], structured_content={"result": []}))


def test_unscripted_synthesis_failure_propagates() -> None:
    client = _empty_results_client()

    with pytest.raises(ScriptExhaustedError):
        asyncio.run(_run_with(ToolExecutor(client), ScriptedProvider())())
    assert len(client.calls) == 12


class _FailingSynthesis(ScriptedProvider):
    async def synthesize(self, view: ProviderView) -> SynthesisProposal:
        raise RuntimeError("model unavailable")


def test_synthesis_exception_propagates_unchanged() -> None:
    with pytest.raises(RuntimeError, match="model unavailable"):
        asyncio.run(_run_with(ToolExecutor(_empty_results_client()), _FailingSynthesis())())


def test_configuration_error_happens_before_any_tool_call() -> None:
    client = StubClient(error=AssertionError("must not be called"))
    bad = InitialInvestigationContext.model_validate(
        {
            "focus_services": ["postgresql"],
            "initial_question": "q",
            "tool_constraints": [
                {
                    "tool": "search_runbooks",
                    "start_time": datetime(2026, 1, 1, tzinfo=UTC),
                    "end_time": datetime(2026, 1, 2, tzinfo=UTC),
                }
            ],
        }
    )

    async def call() -> FixedRunResult:
        return await run_fixed_playbook(
            case_id=CASE_ID,
            incident=INCIDENT,
            context=bad,
            executor=ToolExecutor(client),
            provider=ScriptedProvider(syntheses=[SYNTHESIS]),
        )

    with pytest.raises(PlaybookConfigurationError):
        asyncio.run(call())
    assert client.calls == []


def test_termination_reason_is_written_once() -> None:
    result, _, _ = run(("postgresql",))
    with pytest.raises(RuntimeError, match="already recorded"):
        record_termination(result.state, TerminationReason.BUDGET_EXHAUSTED)


def test_result_is_frozen_and_typed() -> None:
    result, _, _ = run(("payment-gateway",))
    assert set(FixedRunResult.model_fields) == {"state", "synthesis"}
    with pytest.raises(ValueError):
        result.synthesis = SYNTHESIS
