"""Week 4 three-mode fairness and integration contract.

Proves experimental integrity before evaluation: for the same case, every mode
runs through run_investigation with the same frozen data, IncidentBrief,
preregistered context, budget, timeout, and tool boundary, and the provider
never sees the mode, the case ID, or answer-key content. It does not score or
compare effectiveness.

Intended differences (provider call counts, action sequences, tool-call
counts, plans, decision traces, steps, termination, synthesis text) are never
compared across modes; only each mode's approved semantics are checked.
"""

import ast
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import tests.evaluation.support as support_module
from tests.domain.support import EVALUATION_ROOT
from tests.evaluation.support import (
    RecordingClient,
    fresh_runtime,
    frozen_case_ids,
    incident_brief,
    preregistration,
)
from tests.investigation.scripted_provider import ScriptedProvider
from tracepilot.investigation.adaptive_runner import AdaptiveRunResult
from tracepilot.investigation.executor import ToolExecutor
from tracepilot.investigation.fixed_runner import FixedRunResult
from tracepilot.investigation.graph import InvestigationRunResult, run_investigation
from tracepilot.investigation.models import (
    InitialInvestigationContext,
    InvestigationMode,
    InvestigationPlan,
    InvestigationState,
    PlanStep,
    ToolRequest,
)
from tracepilot.investigation.plan_once_runner import PlanOnceRunResult
from tracepilot.investigation.policies import ALLOWED_TOOLS
from tracepilot.investigation.provider import (
    KeepPlanDecision,
    ProviderView,
    SynthesisProposal,
)

SYNTHESIS = SynthesisProposal(
    root_cause_claim="placeholder claim",
    cited_evidence_ids=(),
    explanation="placeholder explanation",
)
RESULT_TYPES: dict[InvestigationMode, type] = {
    InvestigationMode.FIXED_PLAYBOOK: FixedRunResult,
    InvestigationMode.PLAN_ONCE: PlanOnceRunResult,
    InvestigationMode.ADAPTIVE: AdaptiveRunResult,
}
# Labels that must never reach a provider view: modes double as graph nodes.
MODE_LABELS = tuple(mode.value for mode in InvestigationMode)
ANSWER_KEY_FIELDS = (
    "root_cause",
    "category",
    "required_evidence",
    "temporal_evidence",
    "mechanistic_evidence",
    "acceptable_evidence",
    "leakage_aliases",
)

# Representative cases, selected for structural reasons only:
#   INC-001: default context, one focus service
#   INC-004: default context, two focus services in manifest order
#   INC-013: the only preregistered tool constraint
#   INC-015: the only custom (pre-approved) context
REPRESENTATIVE_CASES = ("INC-001", "INC-004", "INC-013", "INC-015")
ALL_CASES = frozen_case_ids()


def run(
    case_id: str, mode: InvestigationMode, provider: ScriptedProvider
) -> tuple[InvestigationRunResult, RecordingClient, ToolExecutor]:
    """One comparative run: always through run_investigation, with fresh
    dependencies over the same frozen data."""

    async def main() -> tuple[InvestigationRunResult, RecordingClient, ToolExecutor]:
        async with fresh_runtime() as (client, executor):
            result = await run_investigation(
                case_id=case_id,
                mode=mode,
                incident=incident_brief(case_id),
                context=preregistration().context_for(case_id),
                executor=executor,
                provider=provider,
            )
            return result, client, executor

    return asyncio.run(main())


def empty_plan_provider() -> ScriptedProvider:
    return ScriptedProvider(initial_plans=[InvestigationPlan()], syntheses=[SYNTHESIS])


def generic_provider(context: InitialInvestigationContext) -> ScriptedProvider:
    """The same plan shape for every case, built only from the preregistered
    context: metrics for the first focus service. Adaptive always KEEPs."""
    plan = InvestigationPlan(
        steps=(
            PlanStep(
                request=ToolRequest(
                    tool="query_service_metrics",
                    arguments={"service": context.focus_services[0]},
                )
            ),
        )
    )
    keeps = [KeepPlanDecision(working_hypothesis=None, open_questions=())] * 15
    return ScriptedProvider(initial_plans=[plan], reviews=keeps, syntheses=[SYNTHESIS])


def pre_planning_view(case_id: str, mode: InvestigationMode) -> ProviderView:
    state = InvestigationState(
        case_id=case_id, mode=mode, initial_context=preregistration().context_for(case_id)
    )
    return ProviderView.from_state(
        state, incident_brief(case_id), preregistration().shared.max_agent_steps
    )


def assert_view_is_neutral(view: ProviderView, case_id: str) -> None:
    dumped = view.model_dump_json()
    assert "mode" not in ProviderView.model_fields
    assert case_id not in dumped
    for label in MODE_LABELS:
        assert label not in dumped, label
    for field in ANSWER_KEY_FIELDS:
        assert not any(field in name for name in ProviderView.model_fields)
        assert field not in dumped


def assert_common_invariants(
    case_id: str,
    mode: InvestigationMode,
    result: InvestigationRunResult,
    client: RecordingClient,
    executor: ToolExecutor,
    provider: ScriptedProvider,
) -> None:
    shared = preregistration().shared
    context = preregistration().context_for(case_id)
    brief = incident_brief(case_id)

    # Input and entry point
    assert type(result) is RESULT_TYPES[mode]
    assert result.state.mode is mode
    assert result.state.case_id == case_id
    assert result.state.initial_context == context
    # Resources
    assert executor.max_steps == shared.max_agent_steps
    assert executor.timeout_seconds == shared.tool_timeout_seconds
    assert all(timeout == shared.tool_timeout_seconds for _, _, timeout in client.calls)
    # Tools and isolation: only this run's case, only the approved tools
    for name, arguments, _ in client.calls:
        assert name in ALLOWED_TOOLS
        if arguments is not None and "case_id" in arguments:
            assert arguments["case_id"] == case_id
    # Provider visibility
    for _, view in provider.views:
        assert view.incident == brief
        assert view.initial_context == context
        assert_view_is_neutral(view, case_id)
    # Output: the provider's synthesis, unchanged, from the terminated state
    operation, synthesis_view = provider.views[-1]
    assert operation == "synthesize"
    assert provider.calls["synthesize"] == 1
    assert result.synthesis == SYNTHESIS
    assert isinstance(result.synthesis, SynthesisProposal)
    assert synthesis_view.termination_reason is not None
    assert synthesis_view == ProviderView.from_state(result.state, brief, shared.max_agent_steps)


# ---------------------------------------------------------------------------
# Coverage and runtime-safe inputs
# ---------------------------------------------------------------------------


def test_frozen_cases_equal_the_preregistration_and_the_expected_set() -> None:
    expected = [f"INC-{n:03d}" for n in range(1, 21)]
    assert ALL_CASES == list(preregistration().case_ids) == expected


@pytest.mark.parametrize("case_id", ALL_CASES)
def test_incident_brief_uses_only_runtime_safe_fields(case_id: str) -> None:
    manifest = json.loads((EVALUATION_ROOT / "cases" / case_id / "incident.json").read_text())
    brief = incident_brief(case_id)

    assert set(type(brief).model_fields) == {"started_at", "affected_services", "symptom"}
    assert list(brief.affected_services) == manifest["affected_services"]
    assert brief.symptom == manifest["symptom"]
    assert brief.started_at.isoformat().replace("+00:00", "Z") == manifest["started_at"]
    dumped = brief.model_dump_json()
    assert case_id not in dumped
    assert manifest["title"] not in dumped


def test_representative_cases_have_their_structural_properties() -> None:
    prereg = preregistration()
    constrained = [c for c in prereg.case_ids if prereg.context_for(c).tool_constraints]
    template = "What explains the observed incident symptoms: "
    custom = [
        c
        for c in prereg.case_ids
        if not prereg.context_for(c).initial_question.startswith(template)
    ]
    assert len(prereg.context_for("INC-001").focus_services) == 1
    assert len(prereg.context_for("INC-004").focus_services) == 2
    assert constrained == ["INC-013"]
    assert custom == ["INC-015"]


# ---------------------------------------------------------------------------
# Plan Once vs Adaptive first-view fairness (all cases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", ALL_CASES)
def test_pre_planning_views_are_equal_for_plan_once_and_adaptive(case_id: str) -> None:
    plan_once = pre_planning_view(case_id, InvestigationMode.PLAN_ONCE)
    adaptive = pre_planning_view(case_id, InvestigationMode.ADAPTIVE)

    assert plan_once == adaptive
    assert plan_once.model_dump_json() == adaptive.model_dump_json()
    assert_view_is_neutral(plan_once, case_id)


@pytest.mark.parametrize("case_id", ALL_CASES)
def test_first_provider_views_are_equal_through_the_runtime(case_id: str) -> None:
    structural = pre_planning_view(case_id, InvestigationMode.PLAN_ONCE)
    first_views = []
    for mode in (InvestigationMode.PLAN_ONCE, InvestigationMode.ADAPTIVE):
        provider = empty_plan_provider()
        result, client, executor = run(case_id, mode, provider)
        assert_common_invariants(case_id, mode, result, client, executor, provider)
        operation, view = provider.views[0]
        assert operation == "propose_initial_plan"
        assert client.calls == []
        first_views.append(view)

    plan_once_view, adaptive_view = first_views
    assert plan_once_view == adaptive_view == structural
    assert plan_once_view.model_dump_json() == adaptive_view.model_dump_json()
    assert plan_once_view.remaining_steps == 15


# ---------------------------------------------------------------------------
# Fixed fairness (all cases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", ALL_CASES)
def test_fixed_receives_the_same_inputs_and_resources(case_id: str) -> None:
    provider = ScriptedProvider(syntheses=[SYNTHESIS])

    result, client, executor = run(case_id, InvestigationMode.FIXED_PLAYBOOK, provider)

    assert_common_invariants(
        case_id, InvestigationMode.FIXED_PLAYBOOK, result, client, executor, provider
    )
    assert executor.max_steps == 15
    assert client.calls  # Fixed always acts
    assert all(timeout == 10.0 for _, _, timeout in client.calls)
    assert provider.calls == {"propose_initial_plan": 0, "review_plan": 0, "synthesize": 1}
    assert [operation for operation, _ in provider.views] == ["synthesize"]


# ---------------------------------------------------------------------------
# Representative end-to-end lifecycle (4 cases x 3 modes)
# ---------------------------------------------------------------------------


def run_all_modes(
    case_id: str,
) -> dict[InvestigationMode, tuple[InvestigationRunResult, RecordingClient, ScriptedProvider]]:
    context = preregistration().context_for(case_id)
    runs = {}
    for mode in InvestigationMode:
        provider = (
            ScriptedProvider(syntheses=[SYNTHESIS])
            if mode is InvestigationMode.FIXED_PLAYBOOK
            else generic_provider(context)
        )
        result, client, executor = run(case_id, mode, provider)
        assert_common_invariants(case_id, mode, result, client, executor, provider)
        runs[mode] = (result, client, provider)
    return runs


@pytest.mark.parametrize("case_id", REPRESENTATIVE_CASES)
def test_representative_three_mode_lifecycle(case_id: str) -> None:
    runs = run_all_modes(case_id)

    fixed_provider = runs[InvestigationMode.FIXED_PLAYBOOK][2]
    assert fixed_provider.calls == {"propose_initial_plan": 0, "review_plan": 0, "synthesize": 1}

    plan_once_provider = runs[InvestigationMode.PLAN_ONCE][2]
    assert plan_once_provider.calls == {
        "propose_initial_plan": 1,
        "review_plan": 0,
        "synthesize": 1,
    }

    adaptive_result, _, adaptive_provider = runs[InvestigationMode.ADAPTIVE]
    assert isinstance(adaptive_result, AdaptiveRunResult)
    assert adaptive_provider.calls["propose_initial_plan"] == 1
    assert adaptive_provider.calls["synthesize"] == 1
    assert adaptive_provider.calls["review_plan"] == len(adaptive_result.decisions) >= 1
    assert all(record.decision == "keep" for record in adaptive_result.decisions)


@pytest.mark.parametrize("case_id", REPRESENTATIVE_CASES)
def test_all_modes_read_the_same_case_data(case_id: str) -> None:
    runs = run_all_modes(case_id)

    observations: dict[str, Any] = {}
    shared_fingerprints = set()
    for result, _, _ in runs.values():
        for record in result.state.tool_calls:
            if not record.executed or record.fingerprint is None:
                continue
            if record.fingerprint in observations:
                assert record.observation == observations[record.fingerprint]
                shared_fingerprints.add(record.fingerprint)
            observations[record.fingerprint] = record.observation
    # Fixed's first step and the generic plan step coincide, so overlap exists.
    assert shared_fingerprints


# ---------------------------------------------------------------------------
# Ground-truth leakage (separate; reads answers only to look for them)
# ---------------------------------------------------------------------------


def _answer_strings(case_id: str) -> list[str]:
    truth = json.loads((EVALUATION_ROOT / "ground_truth" / f"{case_id}.json").read_text())
    return [truth["root_cause"], *truth.get("leakage_aliases", [])]


@pytest.mark.parametrize("case_id", ALL_CASES)
def test_runtime_inputs_carry_no_answer_strings(case_id: str) -> None:
    inputs = (
        incident_brief(case_id).model_dump_json()
        + preregistration().context_for(case_id).model_dump_json()
    ).casefold()
    for answer in _answer_strings(case_id):
        assert answer.casefold() not in inputs, answer


@pytest.mark.parametrize("case_id", REPRESENTATIVE_CASES)
def test_provider_views_carry_no_answer_strings(case_id: str) -> None:
    runs = run_all_modes(case_id)
    answers = [answer.casefold() for answer in _answer_strings(case_id)]
    for _, _, provider in runs.values():
        for _, view in provider.views:
            dumped = view.model_dump_json().casefold()
            for answer in answers:
                assert answer not in dumped, answer


RUNTIME_MODULES = (
    "src/tracepilot/investigation/graph.py",
    "src/tracepilot/investigation/fixed_runner.py",
    "src/tracepilot/investigation/plan_once_runner.py",
    "src/tracepilot/investigation/adaptive_runner.py",
    "src/tracepilot/investigation/plan_execution.py",
    "src/tracepilot/investigation/executor.py",
    "src/tracepilot/investigation/provider.py",
    "src/tracepilot/investigation/playbook.py",
    "src/tracepilot/investigation/models.py",
    "src/tracepilot/investigation/policies.py",
    "src/tracepilot/evaluation/preregistration.py",
)
FORBIDDEN_IDENTIFIERS = {
    "GroundTruth",
    "root_cause",
    "category",
    "leakage_aliases",
    "required_evidence_ids",
    "temporal_evidence_ids",
    "mechanistic_evidence_ids",
    "acceptable_evidence_sets",
    "load_ground_truth",
    "discover_ground_truth_paths",
}


def _docstrings(tree: ast.Module) -> set[ast.Constant]:
    found = set()
    for node in [tree, *ast.walk(tree)]:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module):
            if node.body and isinstance(node.body[0], ast.Expr):
                value = node.body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.add(value)
    return found


@pytest.mark.parametrize(
    "path",
    [*RUNTIME_MODULES, "tests/evaluation/support.py"],
)
def test_runtime_input_path_never_touches_answer_keys(path: str) -> None:
    root = EVALUATION_ROOT.parent
    tree = ast.parse((root / path).read_text())
    docstrings = _docstrings(tree)
    identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    identifiers |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    identifiers |= {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    subscript_keys = {
        node.slice.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    }
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docstrings
    ]
    assert not identifiers & FORBIDDEN_IDENTIFIERS
    assert not subscript_keys & FORBIDDEN_IDENTIFIERS
    assert not any("ground_truth" in literal for literal in literals)


def test_comparisons_use_only_the_shared_entry_point() -> None:
    tree = ast.parse(Path(__file__).read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "run_investigation" in imported
    assert not imported & {"run_fixed_playbook", "run_plan_once", "run_adaptive"}
    support_tree = ast.parse(Path(support_module.__file__).read_text())
    support_imports = {
        alias.name
        for node in ast.walk(support_tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not support_imports & {"run_fixed_playbook", "run_plan_once", "run_adaptive"}


# ---------------------------------------------------------------------------
# Repeatability and isolation
# ---------------------------------------------------------------------------


def test_same_mode_and_configuration_repeat_exactly() -> None:
    context = preregistration().context_for("INC-013")
    first, first_client, _ = run("INC-013", InvestigationMode.ADAPTIVE, generic_provider(context))
    second, second_client, _ = run("INC-013", InvestigationMode.ADAPTIVE, generic_provider(context))

    assert first == second
    assert first_client.calls == second_client.calls
    assert first_client is not second_client


def test_runs_do_not_share_histories() -> None:
    providers = [empty_plan_provider(), empty_plan_provider()]
    first, first_client, first_executor = run("INC-001", InvestigationMode.PLAN_ONCE, providers[0])
    second, second_client, second_executor = run(
        "INC-015", InvestigationMode.ADAPTIVE, providers[1]
    )

    assert first_client is not second_client and first_executor is not second_executor
    assert [provider.calls["propose_initial_plan"] for provider in providers] == [1, 1]
    assert all(view.incident == incident_brief("INC-001") for _, view in providers[0].views)
    assert all(view.incident == incident_brief("INC-015") for _, view in providers[1].views)
    assert (first.state.case_id, second.state.case_id) == ("INC-001", "INC-015")
