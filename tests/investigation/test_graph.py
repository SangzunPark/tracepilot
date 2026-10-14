"""Week 4 shared LangGraph runtime: a shallow dispatch graph over the existing
mode runners.

The primary contract is behavioral parity: for the same inputs and equivalent
fresh test doubles, run_investigation produces exactly what the direct runner
produces (state, synthesis, trace, provider calls and views, MCP calls).
"""

import ast
import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult
from pydantic import JsonValue

import tracepilot.investigation.graph as graph_module
from tests.investigation.executor_support import SpyClient, StubClient
from tests.investigation.scripted_provider import ScriptedProvider, ScriptExhaustedError
from tests.mcp.support import build_service
from tracepilot.domain.catalog import SERVICE_CATALOG
from tracepilot.investigation.adaptive_runner import AdaptiveRunResult, run_adaptive
from tracepilot.investigation.executor import ToolExecutionError, ToolExecutor
from tracepilot.investigation.fixed_runner import FixedRunResult, run_fixed_playbook
from tracepilot.investigation.graph import (
    MODE_NODES,
    InvestigationDependencies,
    InvestigationGraphOutput,
    InvestigationGraphState,
    InvestigationRequest,
    build_investigation_graph,
    route_investigation,
    run_investigation,
)
from tracepilot.investigation.models import (
    BlockReason,
    InitialInvestigationContext,
    InvestigationMode,
    InvestigationPlan,
    PlanStep,
    TerminationReason,
    ToolRequest,
)
from tracepilot.investigation.plan_once_runner import PlanOnceRunResult, run_plan_once
from tracepilot.investigation.provider import (
    IncidentBrief,
    KeepPlanDecision,
    PlanReviewDecision,
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
CHECKOUT = InitialInvestigationContext(
    focus_services=("checkout-api",), initial_question="What is happening?"
)
ALL_SERVICES = InitialInvestigationContext(
    focus_services=tuple(SERVICE_CATALOG), initial_question="What is happening?"
)
SYNTHESIS = SynthesisProposal(
    root_cause_claim="claim", cited_evidence_ids=("log-001",), explanation="explanation"
)

DIRECT_RUNNERS: dict[InvestigationMode, Callable[..., Awaitable[Any]]] = {
    InvestigationMode.FIXED_PLAYBOOK: run_fixed_playbook,
    InvestigationMode.PLAN_ONCE: run_plan_once,
    InvestigationMode.ADAPTIVE: run_adaptive,
}
RESULT_TYPES: dict[InvestigationMode, type] = {
    InvestigationMode.FIXED_PLAYBOOK: FixedRunResult,
    InvestigationMode.PLAN_ONCE: PlanOnceRunResult,
    InvestigationMode.ADAPTIVE: AdaptiveRunResult,
}


def step(tool: str, **arguments: JsonValue) -> PlanStep:
    return PlanStep(request=ToolRequest(tool=tool, arguments=arguments))


LOGS = step("search_application_logs", service="checkout-api")
LOGS_SAME = step("search_application_logs", limit=50, query=None, service="checkout-api")
LOGS_ERROR = step("search_application_logs", service="checkout-api", limit=0)
DEPLOYS = step("get_deployment_history", service="checkout-api")
METRICS = step("query_service_metrics", service="checkout-api")
RUNBOOKS = step("search_runbooks", query="connection")
FORBIDDEN = step("get_feature_flag_history", service="checkout-api", case_id="INC-007")
UNKNOWN = step("delete_database", force=True)


def plan_of(*steps: PlanStep) -> InvestigationPlan:
    return InvestigationPlan(steps=steps)


def runbooks(*queries: str) -> InvestigationPlan:
    return plan_of(*(step("search_runbooks", query=query) for query in queries))


def keep(hypothesis: str = "kept") -> KeepPlanDecision:
    return KeepPlanDecision(working_hypothesis=hypothesis, open_questions=())


def replace(new_plan: InvestigationPlan) -> RevisionProposal:
    return RevisionProposal(plan=new_plan, working_hypothesis="replaced", open_questions=())


def scripted(
    *plans: InvestigationPlan, reviews: tuple[PlanReviewDecision, ...] = ()
) -> Callable[[], ScriptedProvider]:
    """A factory, so each run gets a fresh, equivalent provider."""
    return lambda: ScriptedProvider(initial_plans=plans, reviews=reviews, syntheses=[SYNTHESIS])


async def _invoke(
    path: str,
    mode: InvestigationMode,
    context: InitialInvestigationContext,
    executor: ToolExecutor,
    provider: ScriptedProvider,
) -> Any:
    if path == "direct":
        return await DIRECT_RUNNERS[mode](
            case_id=CASE_ID,
            incident=INCIDENT,
            context=context,
            executor=executor,
            provider=provider,
        )
    return await run_investigation(
        case_id=CASE_ID,
        mode=mode,
        incident=INCIDENT,
        context=context,
        executor=executor,
        provider=provider,
    )


def run_in_memory(
    path: str,
    mode: InvestigationMode,
    provider: ScriptedProvider,
    context: InitialInvestigationContext = CHECKOUT,
    **executor_options: Any,
) -> tuple[Any, SpyClient]:
    async def main() -> tuple[Any, SpyClient]:
        async with Client(create_mcp_server(build_service())) as client:
            spy = SpyClient(client)
            result = await _invoke(
                path, mode, context, ToolExecutor(spy, **executor_options), provider
            )
            return result, spy

    return asyncio.run(main())


def assert_parity(
    mode: InvestigationMode,
    make_provider: Callable[[], ScriptedProvider],
    context: InitialInvestigationContext = CHECKOUT,
    **executor_options: Any,
) -> Any:
    direct_provider, graph_provider = make_provider(), make_provider()
    direct, direct_spy = run_in_memory("direct", mode, direct_provider, context, **executor_options)
    via_graph, graph_spy = run_in_memory("graph", mode, graph_provider, context, **executor_options)

    expected_type: Any = RESULT_TYPES[mode]
    assert type(direct) is expected_type
    assert type(via_graph) is expected_type
    assert via_graph == direct
    assert via_graph.state == direct.state
    assert via_graph.synthesis == direct.synthesis
    assert graph_provider.calls == direct_provider.calls
    assert graph_provider.views == direct_provider.views
    assert graph_spy.calls == direct_spy.calls
    assert via_graph.state.mode is mode
    return via_graph


# ---------------------------------------------------------------------------
# Fixed Playbook parity
# ---------------------------------------------------------------------------


def test_fixed_playbook_parity() -> None:
    result = assert_parity(InvestigationMode.FIXED_PLAYBOOK, scripted())
    assert result.state.termination_reason is TerminationReason.PLAN_EXHAUSTED
    assert result.state.step_count == 12


def test_fixed_playbook_budget_parity() -> None:
    result = assert_parity(InvestigationMode.FIXED_PLAYBOOK, scripted(), ALL_SERVICES)
    assert result.state.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert result.state.step_count == 15


# ---------------------------------------------------------------------------
# Plan Once parity
# ---------------------------------------------------------------------------


def test_plan_once_empty_plan_parity() -> None:
    result = assert_parity(InvestigationMode.PLAN_ONCE, scripted(InvestigationPlan()))
    assert result.state.step_count == 0
    assert result.state.termination_reason is TerminationReason.PLAN_EXHAUSTED


def test_plan_once_mixed_plan_parity() -> None:
    mixed = plan_of(LOGS, LOGS_ERROR, FORBIDDEN, LOGS_SAME, UNKNOWN, RUNBOOKS)
    result = assert_parity(InvestigationMode.PLAN_ONCE, scripted(mixed))
    assert result.state.plan == mixed
    assert [record.block_reason for record in result.state.tool_calls] == [
        None,
        None,
        BlockReason.FORBIDDEN_ARGUMENT,
        BlockReason.DUPLICATE,
        BlockReason.UNKNOWN_TOOL,
        None,
    ]


def test_plan_once_budget_parity() -> None:
    result = assert_parity(
        InvestigationMode.PLAN_ONCE, scripted(runbooks("a", "b", "c", "d")), max_steps=3
    )
    assert result.state.termination_reason is TerminationReason.BUDGET_EXHAUSTED


# ---------------------------------------------------------------------------
# Adaptive parity
# ---------------------------------------------------------------------------

ADAPTIVE_SCENARIOS: dict[str, tuple[Callable[[], ScriptedProvider], dict[str, Any]]] = {
    "empty-initial-plan": (scripted(InvestigationPlan()), {}),
    "keep-mid-plan": (scripted(plan_of(LOGS, DEPLOYS), reviews=(keep(), keep())), {}),
    "keep-at-exhaustion": (scripted(plan_of(LOGS), reviews=(keep(),)), {}),
    "replace": (
        scripted(plan_of(LOGS, DEPLOYS, METRICS), reviews=(replace(plan_of(RUNBOOKS)), keep())),
        {},
    ),
    "empty-replacement": (
        scripted(plan_of(LOGS, DEPLOYS), reviews=(replace(InvestigationPlan()),)),
        {},
    ),
    "budget-before-review": (
        scripted(runbooks("a", "b", "c"), reviews=(keep(),)),
        {"max_steps": 2},
    ),
    "duplicate-across-versions": (
        scripted(plan_of(LOGS, DEPLOYS), reviews=(replace(plan_of(LOGS_SAME, RUNBOOKS)), keep())),
        {},
    ),
    "executed-error": (scripted(plan_of(LOGS_ERROR), reviews=(keep(),)), {}),
}


@pytest.mark.parametrize("scenario", ADAPTIVE_SCENARIOS)
def test_adaptive_parity(scenario: str) -> None:
    make_provider, options = ADAPTIVE_SCENARIOS[scenario]
    result = assert_parity(InvestigationMode.ADAPTIVE, make_provider, **options)
    assert isinstance(result, AdaptiveRunResult)
    assert result.initial_plan == make_provider()._initial_plans[0]


def test_adaptive_trace_parity_details() -> None:
    reviews = (
        keep("h1"),
        replace(plan_of(METRICS, RUNBOOKS)),
        keep("h3"),
        replace(InvestigationPlan()),
    )
    result = assert_parity(
        InvestigationMode.ADAPTIVE, scripted(plan_of(LOGS, DEPLOYS, METRICS), reviews=reviews)
    )
    assert [record.decision for record in result.decisions] == [
        "keep",
        "replace",
        "keep",
        "replace",
    ]
    assert result.state.plan_version == 3


def test_adaptive_budget_ends_without_a_final_review() -> None:
    result = assert_parity(
        InvestigationMode.ADAPTIVE,
        scripted(runbooks("a", "b", "c"), reviews=(keep(),)),
        max_steps=2,
    )
    assert result.state.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert len(result.decisions) == 1


def test_adaptive_duplicate_spans_versions_through_the_graph() -> None:
    result = ADAPTIVE_SCENARIOS["duplicate-across-versions"]
    via_graph = assert_parity(InvestigationMode.ADAPTIVE, result[0])
    assert via_graph.state.tool_calls[1].block_reason is BlockReason.DUPLICATE


# ---------------------------------------------------------------------------
# Failure parity
# ---------------------------------------------------------------------------


def _empty_results_client() -> StubClient:
    return StubClient(result=CallToolResult(content=[], structured_content={"result": []}))


FAILURES: dict[
    str,
    tuple[
        InvestigationMode,
        Callable[[], StubClient],
        Callable[[], ScriptedProvider],
        type[BaseException],
    ],
] = {
    "tool-execution-error": (
        InvestigationMode.ADAPTIVE,
        lambda: StubClient(error=OSError("pipe closed")),
        scripted(runbooks("a", "b"), reviews=(keep(),)),
        ToolExecutionError,
    ),
    "fixed-tool-execution-error": (
        InvestigationMode.FIXED_PLAYBOOK,
        lambda: StubClient(error=OSError("pipe closed")),
        scripted(),
        ToolExecutionError,
    ),
    "initial-planning-failure": (
        InvestigationMode.PLAN_ONCE,
        _empty_results_client,
        lambda: ScriptedProvider(syntheses=[SYNTHESIS]),
        ScriptExhaustedError,
    ),
    "adaptive-review-failure": (
        InvestigationMode.ADAPTIVE,
        _empty_results_client,
        lambda: ScriptedProvider(initial_plans=[runbooks("a", "b")], syntheses=[SYNTHESIS]),
        ScriptExhaustedError,
    ),
    "synthesis-failure": (
        InvestigationMode.FIXED_PLAYBOOK,
        _empty_results_client,
        lambda: ScriptedProvider(),
        ScriptExhaustedError,
    ),
}


@pytest.mark.parametrize("failure", FAILURES)
def test_failure_parity(failure: str) -> None:
    mode, make_client, make_provider, error_type = FAILURES[failure]
    outcomes = []
    for path in ("direct", "graph"):
        client, provider = make_client(), make_provider()
        with pytest.raises(error_type) as raised:
            asyncio.run(_invoke(path, mode, CHECKOUT, ToolExecutor(client), provider))
        outcomes.append(
            (type(raised.value), str(raised.value), dict(provider.calls), list(client.calls))
        )

    direct, via_graph = outcomes
    assert via_graph == direct
    assert not isinstance(via_graph[0], BaseExceptionGroup)


def test_tool_execution_error_is_not_retried_by_the_graph() -> None:
    client = StubClient(error=OSError("pipe closed"))
    provider = scripted(runbooks("a", "b"))()
    with pytest.raises(ToolExecutionError):
        asyncio.run(
            _invoke("graph", InvestigationMode.PLAN_ONCE, CHECKOUT, ToolExecutor(client), provider)
        )
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Routing and the public boundary
# ---------------------------------------------------------------------------


def _request(mode: InvestigationMode) -> InvestigationRequest:
    return {"case_id": CASE_ID, "mode": mode, "incident": INCIDENT, "context": CHECKOUT}


@pytest.mark.parametrize(
    ("mode", "node"),
    [
        (InvestigationMode.FIXED_PLAYBOOK, "fixed_playbook"),
        (InvestigationMode.PLAN_ONCE, "plan_once"),
        (InvestigationMode.ADAPTIVE, "adaptive"),
    ],
)
def test_router_is_deterministic_by_mode(mode: InvestigationMode, node: str) -> None:
    request = _request(mode)
    snapshot = dict(request)
    assert route_investigation(request) == node
    assert route_investigation(request) == node
    assert dict(request) == snapshot


def test_every_mode_has_exactly_one_route() -> None:
    assert set(MODE_NODES) == set(InvestigationMode)
    assert len(set(MODE_NODES.values())) == len(InvestigationMode)


@pytest.mark.parametrize("mode", ["adaptive", "plan_once", "unknown", None, 3])
def test_invalid_mode_is_rejected_before_invocation(mode: object) -> None:
    client = _empty_results_client()
    provider = scripted(InvestigationPlan())()

    with pytest.raises(ValueError, match="InvestigationMode"):
        asyncio.run(
            run_investigation(
                case_id=CASE_ID,
                mode=mode,  # type: ignore[arg-type]
                incident=INCIDENT,
                context=CHECKOUT,
                executor=ToolExecutor(client),
                provider=provider,
            )
        )

    assert provider.calls == {"propose_initial_plan": 0, "review_plan": 0, "synthesize": 0}
    assert client.calls == []


def test_graph_has_the_three_mode_nodes() -> None:
    nodes = set(build_investigation_graph().get_graph().nodes)
    assert {"fixed_playbook", "plan_once", "adaptive"} <= nodes


def test_graph_output_holds_only_the_result() -> None:
    client = _empty_results_client()
    provider = scripted(InvestigationPlan())()
    dependencies = InvestigationDependencies(executor=ToolExecutor(client), provider=provider)

    output = asyncio.run(
        build_investigation_graph().ainvoke(
            _request(InvestigationMode.PLAN_ONCE), context=dependencies
        )
    )

    assert set(output) == {"result"}
    assert isinstance(output["result"], PlanOnceRunResult)
    for value in output.values():
        assert value is not provider and value is not dependencies.executor


def test_graph_types_hold_no_investigation_semantics_or_dependencies() -> None:
    state_fields = set(InvestigationGraphState.__annotations__)
    assert set(InvestigationRequest.__annotations__) == {"case_id", "mode", "incident", "context"}
    assert state_fields == {"case_id", "mode", "incident", "context", "result"}
    assert set(InvestigationGraphOutput.__annotations__) == {"result"}
    forbidden = {
        "plan",
        "plan_version",
        "current_step_index",
        "tool_calls",
        "working_hypothesis",
        "open_questions",
        "termination_reason",
        "plan_decision_at_step",
        "executor",
        "provider",
    }
    assert not state_fields & forbidden
    assert set(InvestigationDependencies.__dataclass_fields__) == {"executor", "provider"}
    assert InvestigationDependencies.__dataclass_params__.frozen  # type: ignore[attr-defined]


def test_provider_views_stay_mode_neutral_through_the_graph() -> None:
    provider = scripted(plan_of(LOGS), reviews=(keep(),))()
    run_in_memory("graph", InvestigationMode.ADAPTIVE, provider)
    for _, view in provider.views:
        dumped = view.model_dump_json()
        assert not hasattr(view, "mode")
        for label in ("adaptive", "plan_once", "fixed_playbook"):
            assert label not in dumped


# ---------------------------------------------------------------------------
# Concurrency and context isolation
# ---------------------------------------------------------------------------


class _TaggedClient(StubClient):
    """Returns records tagged with its own label, yielding to other tasks."""

    def __init__(self, tag: str) -> None:
        super().__init__(
            result=CallToolResult(content=[], structured_content={"result": [{"source_id": tag}]})
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> CallToolResult:
        await asyncio.sleep(0)
        return await super().call_tool(name, arguments, read_timeout_seconds)


def test_concurrent_runs_keep_their_own_dependencies() -> None:
    client_a, client_b = _TaggedClient("A"), _TaggedClient("B")
    provider_a = scripted(runbooks("a1", "a2", "a3"), reviews=(keep("A"), keep("A"), keep("A")))()
    provider_b = scripted(runbooks("b1", "b2"))()

    async def both() -> tuple[Any, Any]:
        return await asyncio.gather(
            run_investigation(
                case_id=CASE_ID,
                mode=InvestigationMode.ADAPTIVE,
                incident=INCIDENT,
                context=CHECKOUT,
                executor=ToolExecutor(client_a),
                provider=provider_a,
            ),
            run_investigation(
                case_id="INC-002",
                mode=InvestigationMode.PLAN_ONCE,
                incident=INCIDENT,
                context=CHECKOUT,
                executor=ToolExecutor(client_b),
                provider=provider_b,
            ),
        )

    result_a, result_b = asyncio.run(both())

    assert isinstance(result_a, AdaptiveRunResult)
    assert isinstance(result_b, PlanOnceRunResult)
    assert [arguments["query"] for _, arguments in client_a.calls if arguments] == [
        "a1",
        "a2",
        "a3",
    ]
    assert [arguments["query"] for _, arguments in client_b.calls if arguments] == ["b1", "b2"]
    for result, tag in ((result_a, "A"), (result_b, "B")):
        for record in result.state.tool_calls:
            assert record.observation is not None
            assert record.observation.records == ({"source_id": tag},)
    assert (result_a.state.case_id, result_b.state.case_id) == (CASE_ID, "INC-002")
    assert provider_a.calls == {"propose_initial_plan": 1, "review_plan": 3, "synthesize": 1}
    assert provider_b.calls == {"propose_initial_plan": 1, "review_plan": 0, "synthesize": 1}


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


def test_graph_module_dispatches_without_reimplementing_runners() -> None:
    tree = ast.parse(Path(graph_module.__file__).read_text())
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    for forbidden in (
        "tracepilot.domain",
        "tracepilot.diagnostics",
        "tracepilot.mcp",
        "tracepilot.investigation.policies",
        "tracepilot.investigation.plan_execution",
        "openai",
        "anthropic",
        "mcp",
    ):
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.") for module in imported_modules
        )
    for runner_internal in (
        "execute_static_plan",
        "structural_termination",
        "plan_review_pending",
        "record_tool_call",
        "advance_cursor",
        "record_termination",
        "evaluate_request",
    ):
        assert runner_internal not in names
    assert not any("ground_truth" in literal or "evaluation/" in literal for literal in literals)
    for runner in ("run_fixed_playbook", "run_plan_once", "run_adaptive"):
        assert runner in names


def test_runners_do_not_depend_on_the_graph() -> None:
    import tracepilot.investigation.adaptive_runner as adaptive
    import tracepilot.investigation.fixed_runner as fixed
    import tracepilot.investigation.plan_once_runner as plan_once

    for module in (fixed, plan_once, adaptive):
        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text())
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "tracepilot.investigation.graph" not in modules
        assert not any(module.startswith("langgraph") for module in modules)
