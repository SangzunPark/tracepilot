"""Week 4 provider contract: the Provider Protocol, the provider-visible view
and incident brief, the revision and synthesis proposals, and the
deterministic ScriptedProvider test double."""

import ast
import asyncio
import inspect
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

import tracepilot.investigation.provider as provider_module
from tests.domain.support import EVALUATION_ROOT
from tests.investigation.scripted_provider import (
    OPERATIONS,
    ScriptedProvider,
    ScriptExhaustedError,
)
from tests.investigation.support import blocked, executed, inc013_context, plan, state
from tracepilot.investigation.models import (
    TOOL_NAMES,
    InvestigationMode,
    InvestigationPlan,
    InvestigationState,
    TerminationReason,
)
from tracepilot.investigation.provider import (
    MAX_CITED_EVIDENCE_IDS,
    MAX_EXPLANATION_CHARS,
    MAX_ROOT_CAUSE_CLAIM_CHARS,
    IncidentBrief,
    KeepPlanDecision,
    PlanReviewDecision,
    Provider,
    ProviderView,
    RevisionProposal,
    SynthesisProposal,
)

PROVIDER_SDKS = ("openai", "anthropic", "google-genai", "litellm", "mistralai", "cohere")
ANSWER_KEY_FRAGMENTS = ("ground_truth", "required", "acceptable", "root_cause", "category")

INITIAL = plan("search_application_logs", "get_deployment_history")
REVISED = RevisionProposal(
    plan=plan("get_feature_flag_history"),
    working_hypothesis="A flag change slowed checkout.",
    open_questions=("Which flag changed first?",),
)
KEPT = KeepPlanDecision(
    working_hypothesis="The deployment still looks responsible.",
    open_questions=("Did errors start before the deploy?",),
)
REVIEW_ADAPTER: TypeAdapter[PlanReviewDecision] = TypeAdapter(PlanReviewDecision)
BRIEF = IncidentBrief(
    started_at=datetime(2026, 4, 20, 16, 12, tzinfo=UTC),
    affected_services=("billing-api",),
    symptom="Invoice submissions became slow and began failing.",
)
SYNTHESIS = SynthesisProposal(
    root_cause_claim="A deployment exhausted the connection pool.",
    cited_evidence_ids=("deploy-001", "log-004"),
    explanation="Errors began right after the deployment and match pool exhaustion.",
)


def _view(current: InvestigationState | None = None) -> ProviderView:
    return ProviderView.from_state(current if current is not None else state(), BRIEF)


# ---------------------------------------------------------------------------
# Protocol shape
# ---------------------------------------------------------------------------


def test_protocol_has_exactly_three_async_operations() -> None:
    members = {
        name
        for name, value in vars(Provider).items()
        if inspect.isfunction(value) and not name.startswith("_")
    }
    assert members == set(OPERATIONS) == {"propose_initial_plan", "review_plan", "synthesize"}
    for name in members:
        assert inspect.iscoroutinefunction(getattr(Provider, name))
    assert not hasattr(Provider, "propose_next_action")


def test_protocol_signatures_take_a_view() -> None:
    for name in OPERATIONS:
        parameters = list(inspect.signature(getattr(Provider, name)).parameters)
        assert parameters == ["self", "view"]


def test_scripted_provider_satisfies_the_protocol() -> None:
    # Checked by mypy: the assignment must type-check.
    provider: Provider = ScriptedProvider()
    for name in OPERATIONS:
        assert inspect.iscoroutinefunction(getattr(provider, name))


class _NoSynthesis:
    async def propose_initial_plan(self, view: ProviderView) -> InvestigationPlan:
        return INITIAL

    async def review_plan(self, view: ProviderView) -> RevisionProposal:
        return REVISED


def test_incomplete_provider_does_not_satisfy_the_protocol() -> None:
    # mypy strict reports an unused ignore if this ever becomes compatible.
    provider: Provider = _NoSynthesis()  # type: ignore[assignment]
    assert not hasattr(provider, "synthesize")


# ---------------------------------------------------------------------------
# Provider-visible view
# ---------------------------------------------------------------------------


def test_view_fields_are_runtime_visible_only() -> None:
    assert set(ProviderView.model_fields) == {
        "incident",
        "initial_context",
        "available_tools",
        "plan",
        "plan_version",
        "current_step_index",
        "tool_calls",
        "open_questions",
        "working_hypothesis",
        "remaining_steps",
        "termination_reason",
    }
    for name in ProviderView.model_fields:
        assert not any(fragment in name for fragment in ANSWER_KEY_FRAGMENTS)


def test_view_has_no_case_id() -> None:
    view = _view()
    assert "case_id" not in ProviderView.model_fields
    assert "INC-001" not in view.model_dump_json()
    with pytest.raises(ValidationError):
        ProviderView.model_validate({**view.model_dump(), "case_id": "INC-001"})


def test_view_does_not_expose_investigation_mode() -> None:
    view = _view()
    assert "mode" not in ProviderView.model_fields
    assert not any("mode" in name for name in ProviderView.model_fields)
    for mode in InvestigationMode:
        assert mode.value not in view.model_dump_json()
    with pytest.raises(ValidationError):
        ProviderView.model_validate({**view.model_dump(), "mode": "plan_once"})


def test_pre_planning_views_are_equal_across_plan_once_and_adaptive() -> None:
    def pre_planning(mode: InvestigationMode) -> InvestigationState:
        return InvestigationState(case_id="INC-013", mode=mode, initial_context=inc013_context())

    plan_once = ProviderView.from_state(pre_planning(InvestigationMode.PLAN_ONCE), BRIEF, 7)
    adaptive = ProviderView.from_state(pre_planning(InvestigationMode.ADAPTIVE), BRIEF, 7)

    assert plan_once == adaptive
    assert plan_once.model_dump_json() == adaptive.model_dump_json()
    assert (plan_once.plan, plan_once.plan_version, plan_once.remaining_steps) == (None, 0, 7)


def test_view_mirrors_state() -> None:
    current = state(
        records=(executed(1), blocked(2)),
        plan_decision_at_step=1,
        plan_version=2,
        current_step_index=1,
    ).model_copy(
        update={"open_questions": ("Did the deploy change pool size?",), "working_hypothesis": "h"}
    )

    view = ProviderView.from_state(current, BRIEF)

    assert view.incident == BRIEF
    assert view.initial_context == current.initial_context
    assert view.available_tools == TOOL_NAMES
    assert view.plan == current.plan
    assert (view.plan_version, view.current_step_index) == (2, 1)
    assert view.tool_calls == current.tool_calls
    assert view.open_questions == current.open_questions
    assert view.working_hypothesis == "h"
    assert view.termination_reason is None


def test_view_carries_tool_specific_constraints_not_a_global_window() -> None:
    current = InvestigationState(
        case_id="INC-013",
        mode=InvestigationMode.PLAN_ONCE,
        initial_context=inc013_context(),
    )
    view = ProviderView.from_state(current, BRIEF)
    assert view.initial_context.tool_constraints[0].tool == "get_deployment_history"


@pytest.mark.parametrize(("steps", "remaining"), [(0, 15), (14, 1), (15, 0)])
def test_view_reports_remaining_steps(steps: int, remaining: int) -> None:
    records = tuple(blocked(step) for step in range(1, steps + 1))
    assert _view(state(records=records)).remaining_steps == remaining


def test_view_honours_custom_budget() -> None:
    view = ProviderView.from_state(state(records=(blocked(1),)), BRIEF, max_steps=3)
    assert view.remaining_steps == 2


def test_view_reports_termination_for_synthesis() -> None:
    terminated = state().model_copy(update={"termination_reason": TerminationReason.PLAN_EXHAUSTED})
    assert _view(terminated).termination_reason is TerminationReason.PLAN_EXHAUSTED


def test_view_is_frozen_and_building_it_does_not_mutate_state() -> None:
    current = state(records=(executed(1),))
    before = current.model_copy(deep=True)

    view = ProviderView.from_state(current, BRIEF)

    assert current == before
    with pytest.raises(ValidationError):
        view.remaining_steps = 99


# ---------------------------------------------------------------------------
# Incident brief
# ---------------------------------------------------------------------------


def test_incident_brief_fields_exclude_identity_and_answer_keys() -> None:
    assert set(IncidentBrief.model_fields) == {"started_at", "affected_services", "symptom"}


def test_provider_module_does_not_import_domain_models() -> None:
    tree = ast.parse(Path(provider_module.__file__).read_text())
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(module.startswith("tracepilot.domain") for module in imported)


@pytest.mark.parametrize(
    "fields",
    [
        {"started_at": datetime(2026, 4, 20, 16, 12)},
        {"affected_services": []},
        {"symptom": ""},
        {"case_id": "INC-015"},
        {"title": "Billing API invoice submission slowdown"},
        {"category": "database"},
    ],
    ids=["naive-start", "no-services", "empty-symptom", "case-id", "title", "category"],
)
def test_incident_brief_rejects_invalid_or_forbidden_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        IncidentBrief.model_validate({**BRIEF.model_dump(), **fields})


def test_incident_brief_is_frozen() -> None:
    with pytest.raises(ValidationError):
        BRIEF.symptom = "changed"


def test_view_requires_an_incident_brief() -> None:
    view = _view()
    with pytest.raises(ValidationError):
        ProviderView.model_validate({k: v for k, v in view.model_dump().items() if k != "incident"})


# ---------------------------------------------------------------------------
# Revision proposal
# ---------------------------------------------------------------------------


def test_revision_proposal_fields() -> None:
    assert set(RevisionProposal.model_fields) == {
        "decision",
        "plan",
        "working_hypothesis",
        "open_questions",
    }
    assert REVISED.decision == "replace"


def test_revision_proposal_documents_future_actions_only() -> None:
    doc = RevisionProposal.__doc__ or ""
    assert "FUTURE actions only" in doc
    assert "tool_calls" in doc


def test_revision_proposal_requires_complete_replacement_values() -> None:
    for missing in ("plan", "working_hypothesis", "open_questions"):
        fields = {k: v for k, v in REVISED.model_dump().items() if k != missing}
        with pytest.raises(ValidationError):
            RevisionProposal.model_validate(fields)


def test_revision_proposal_accepts_state_bounds() -> None:
    proposal = RevisionProposal(
        plan=InvestigationPlan(),
        working_hypothesis="h" * 1000,
        open_questions=("q" * 300,) * 10,
    )
    assert len(proposal.open_questions) == 10


@pytest.mark.parametrize(
    "fields",
    [
        {"working_hypothesis": "h" * 1001},
        {"open_questions": ["q"] * 11},
        {"open_questions": ["q" * 301]},
        {"open_questions": [""]},
        {"confidence": 0.9},
        {"completed": True},
    ],
    ids=[
        "long-hypothesis",
        "too-many-questions",
        "long-question",
        "empty-question",
        "conf",
        "done",
    ],
)
def test_revision_proposal_rejects_invalid_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RevisionProposal.model_validate({**REVISED.model_dump(), **fields})


def test_revision_proposal_values_fit_investigation_state() -> None:
    # Complete replacement values the runner can apply to state as-is.
    revised_state = state().model_copy(
        update={
            "working_hypothesis": REVISED.working_hypothesis,
            "open_questions": REVISED.open_questions,
        }
    )
    InvestigationState.model_validate(revised_state.model_dump())


# ---------------------------------------------------------------------------
# KEEP decision and the plan-review result type
# ---------------------------------------------------------------------------


def test_keep_decision_fields_carry_no_plan() -> None:
    assert set(KeepPlanDecision.model_fields) == {
        "decision",
        "working_hypothesis",
        "open_questions",
    }
    assert KEPT.decision == "keep"
    with pytest.raises(ValidationError):
        KeepPlanDecision.model_validate({**KEPT.model_dump(), "plan": INITIAL.model_dump()})


def test_keep_decision_requires_hypothesis_and_questions() -> None:
    for missing in ("working_hypothesis", "open_questions"):
        fields = {k: v for k, v in KEPT.model_dump().items() if k != missing}
        with pytest.raises(ValidationError):
            KeepPlanDecision.model_validate(fields)
    assert KeepPlanDecision(working_hypothesis=None, open_questions=()).open_questions == ()


@pytest.mark.parametrize(
    "fields",
    [
        {"working_hypothesis": "h" * 1001},
        {"open_questions": ["q"] * 11},
        {"open_questions": ["q" * 301]},
        {"open_questions": [""]},
        {"decision": "replace"},
        {"confidence": 0.9},
        {"completed": True},
        {"correct": True},
        {"mode": "adaptive"},
        {"terminate": True},
    ],
    ids=[
        "long-hypothesis",
        "too-many-questions",
        "long-question",
        "empty-question",
        "wrong-discriminator",
        "confidence",
        "completion",
        "correctness",
        "mode",
        "termination-authority",
    ],
)
def test_keep_decision_rejects_invalid_or_forbidden_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        KeepPlanDecision.model_validate({**KEPT.model_dump(), **fields})


@pytest.mark.parametrize("field", ["confidence", "completed", "correct", "mode", "terminate"])
def test_revision_proposal_rejects_authority_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        RevisionProposal.model_validate({**REVISED.model_dump(), field: True})


def test_review_decision_is_discriminated_by_decision() -> None:
    keep = REVIEW_ADAPTER.validate_python(
        {"decision": "keep", "working_hypothesis": None, "open_questions": []}
    )
    replace = REVIEW_ADAPTER.validate_python(
        {
            "decision": "replace",
            "plan": {"steps": []},
            "working_hypothesis": "h",
            "open_questions": [],
        }
    )
    assert isinstance(keep, KeepPlanDecision)
    assert isinstance(replace, RevisionProposal)
    assert REVIEW_ADAPTER.validate_json(KEPT.model_dump_json()) == KEPT
    assert REVIEW_ADAPTER.validate_json(REVISED.model_dump_json()) == REVISED


@pytest.mark.parametrize(
    "payload",
    [
        {"working_hypothesis": None, "open_questions": []},
        {"decision": "revise", "working_hypothesis": None, "open_questions": []},
        {
            "decision": "keep",
            "plan": {"steps": []},
            "working_hypothesis": None,
            "open_questions": [],
        },
        {"decision": "replace", "working_hypothesis": None, "open_questions": []},
    ],
    ids=["no-discriminator", "unknown-decision", "keep-with-plan", "replace-without-plan"],
)
def test_review_decision_rejects_ambiguous_payloads(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        REVIEW_ADAPTER.validate_python(payload)


def test_review_plan_returns_the_review_decision_type() -> None:
    annotations = inspect.get_annotations(Provider.review_plan, eval_str=True)
    assert annotations["return"] == PlanReviewDecision


# ---------------------------------------------------------------------------
# Synthesis proposal
# ---------------------------------------------------------------------------


def test_synthesis_has_no_completion_authority() -> None:
    assert set(SynthesisProposal.model_fields) == {
        "root_cause_claim",
        "cited_evidence_ids",
        "explanation",
    }
    for field in ("completed", "sufficient", "passed", "status", "confidence"):
        with pytest.raises(ValidationError):
            SynthesisProposal.model_validate({**SYNTHESIS.model_dump(), field: True})


def test_synthesis_allows_no_citations() -> None:
    # Whether that is sufficient is for the later completion evaluator.
    assert SynthesisProposal(root_cause_claim="c", cited_evidence_ids=(), explanation="e")


@pytest.mark.parametrize(
    "fields",
    [
        {"root_cause_claim": ""},
        {"root_cause_claim": "c" * (MAX_ROOT_CAUSE_CLAIM_CHARS + 1)},
        {"explanation": ""},
        {"explanation": "e" * (MAX_EXPLANATION_CHARS + 1)},
        {"cited_evidence_ids": ["log-1", "log-1"]},
        {"cited_evidence_ids": [""]},
        {"cited_evidence_ids": [f"log-{i}" for i in range(MAX_CITED_EVIDENCE_IDS + 1)]},
    ],
    ids=[
        "empty-claim",
        "long-claim",
        "empty-explanation",
        "long-explanation",
        "duplicate-citation",
        "empty-citation",
        "too-many-citations",
    ],
)
def test_synthesis_rejects_invalid_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SynthesisProposal.model_validate({**SYNTHESIS.model_dump(), **fields})


# ---------------------------------------------------------------------------
# ScriptedProvider
# ---------------------------------------------------------------------------


def test_scripted_initial_plan() -> None:
    provider = ScriptedProvider(initial_plans=[INITIAL])
    assert asyncio.run(provider.propose_initial_plan(_view())) == INITIAL


def test_scripted_revisions_are_returned_in_order() -> None:
    second = RevisionProposal(
        plan=plan("search_runbooks"), working_hypothesis=None, open_questions=()
    )
    provider = ScriptedProvider(reviews=[REVISED, second])
    view = _view()

    assert asyncio.run(provider.review_plan(view)) == REVISED
    assert asyncio.run(provider.review_plan(view)) == second


def test_scripted_synthesis() -> None:
    provider = ScriptedProvider(syntheses=[SYNTHESIS])
    assert asyncio.run(provider.synthesize(_view())) == SYNTHESIS


def test_scripted_responses_can_be_empty_plans() -> None:
    empty = RevisionProposal(plan=InvestigationPlan(), working_hypothesis=None, open_questions=())
    provider = ScriptedProvider(reviews=[empty])
    decision = asyncio.run(provider.review_plan(_view()))
    assert isinstance(decision, RevisionProposal)
    assert decision.plan.steps == ()


def test_scripted_reviews_can_mix_keep_and_replace() -> None:
    provider = ScriptedProvider(reviews=[KEPT, REVISED, KEPT])
    view = _view()
    decisions = [asyncio.run(provider.review_plan(view)) for _ in range(3)]
    assert [decision.decision for decision in decisions] == ["keep", "replace", "keep"]
    assert provider.calls["review_plan"] == 3


def test_calls_are_counted_per_operation_and_views_recorded() -> None:
    provider = ScriptedProvider(
        initial_plans=[INITIAL], reviews=[REVISED, REVISED], syntheses=[SYNTHESIS]
    )
    first, second = _view(), _view(state(records=(executed(1),)))

    assert provider.calls == dict.fromkeys(OPERATIONS, 0)
    asyncio.run(provider.propose_initial_plan(first))
    asyncio.run(provider.review_plan(second))
    asyncio.run(provider.review_plan(second))
    asyncio.run(provider.synthesize(second))

    assert provider.calls == {"propose_initial_plan": 1, "review_plan": 2, "synthesize": 1}
    assert provider.views == [
        ("propose_initial_plan", first),
        ("review_plan", second),
        ("review_plan", second),
        ("synthesize", second),
    ]


@pytest.mark.parametrize("operation", OPERATIONS)
def test_unscripted_operation_fails_clearly(operation: str) -> None:
    provider = ScriptedProvider()

    with pytest.raises(ScriptExhaustedError, match=operation):
        asyncio.run(getattr(provider, operation)(_view()))
    assert provider.calls[operation] == 1


def test_script_exhaustion_after_last_response() -> None:
    provider = ScriptedProvider(initial_plans=[INITIAL])
    asyncio.run(provider.propose_initial_plan(_view()))

    with pytest.raises(ScriptExhaustedError, match="called 2 time"):
        asyncio.run(provider.propose_initial_plan(_view()))
    assert provider.calls["propose_initial_plan"] == 2


def test_scripts_are_independent_per_operation() -> None:
    provider = ScriptedProvider(initial_plans=[INITIAL])
    with pytest.raises(ScriptExhaustedError):
        asyncio.run(provider.review_plan(_view()))
    assert asyncio.run(provider.propose_initial_plan(_view())) == INITIAL


def test_script_is_copied_at_construction() -> None:
    plans = [INITIAL]
    provider = ScriptedProvider(initial_plans=plans)
    plans[0] = REVISED.plan
    plans.append(REVISED.plan)

    assert asyncio.run(provider.propose_initial_plan(_view())) == INITIAL
    with pytest.raises(ScriptExhaustedError):
        asyncio.run(provider.propose_initial_plan(_view()))


def test_provider_calls_do_not_mutate_view_or_state() -> None:
    current = state(records=(executed(1),))
    before_state = current.model_copy(deep=True)
    view = ProviderView.from_state(current, BRIEF)
    before_view = view.model_copy(deep=True)
    provider = ScriptedProvider(initial_plans=[INITIAL], reviews=[REVISED], syntheses=[SYNTHESIS])

    asyncio.run(provider.propose_initial_plan(view))
    asyncio.run(provider.review_plan(view))
    asyncio.run(provider.synthesize(view))

    assert view == before_view
    assert current == before_state


def test_scripted_provider_is_deterministic() -> None:
    def run() -> list[object]:
        provider = ScriptedProvider(initial_plans=[INITIAL], syntheses=[SYNTHESIS])
        return [
            asyncio.run(provider.propose_initial_plan(_view())),
            asyncio.run(provider.synthesize(_view())),
            dict(provider.calls),
        ]

    assert run() == run()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def test_project_declares_no_provider_sdk() -> None:
    pyproject = tomllib.loads((EVALUATION_ROOT.parent / "pyproject.toml").read_text())
    declared = [
        *pyproject["project"]["dependencies"],
        *(dep for group in pyproject["dependency-groups"].values() for dep in group),
    ]
    for dependency in declared:
        name = dependency.split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip()
        assert name.lower() not in PROVIDER_SDKS
