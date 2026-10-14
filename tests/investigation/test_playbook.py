"""Week 4 global Fixed Playbook: deterministic scope, exact tool sequence per
service type, tool-specific time constraints, and policy compatibility.

Built and checked from the catalog and tool contracts only; no case data or
ground truth is used.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import JsonValue

from tracepilot.domain.catalog import SERVICE_CATALOG
from tracepilot.investigation.models import (
    BlockReason,
    InitialInvestigationContext,
    InvestigationPlan,
    ToolTimeConstraint,
)
from tracepilot.investigation.playbook import (
    PlaybookConfigurationError,
    build_fixed_playbook,
    fixed_playbook_scope,
)
from tracepilot.investigation.policies import evaluate_request

START = datetime(2026, 4, 6, 13, 50, tzinfo=UTC)
END = datetime(2026, 4, 6, 14, 15, tzinfo=UTC)

APPLICATION = [
    "query_service_metrics",
    "search_application_logs",
    "get_deployment_history",
    "get_feature_flag_history",
]
DATABASE = ["query_service_metrics", "search_application_logs"]

ALL_FOCUS_COMBINATIONS: list[tuple[str, ...]] = [
    ("checkout-api",),
    ("billing-api",),
    ("authentication-service",),
    ("postgresql",),
    ("payment-gateway",),
    ("checkout-api", "billing-api"),
    ("billing-api", "checkout-api"),
    tuple(SERVICE_CATALOG),
]


def context(
    *focus: str, constraints: tuple[ToolTimeConstraint, ...] = ()
) -> InitialInvestigationContext:
    return InitialInvestigationContext(
        focus_services=focus, initial_question="What is happening?", tool_constraints=constraints
    )


def calls(plan: InvestigationPlan) -> list[tuple[str, dict[str, JsonValue]]]:
    return [(step.request.tool, step.request.arguments) for step in plan.steps]


def constraint(tool: str, service: str | None = None) -> ToolTimeConstraint:
    return ToolTimeConstraint.model_validate(
        {"tool": tool, "service": service, "start_time": START, "end_time": END}
    )


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("focus", "scope"),
    [
        (
            ("checkout-api",),
            ("checkout-api", "authentication-service", "postgresql", "payment-gateway"),
        ),
        (("billing-api",), ("billing-api", "postgresql", "payment-gateway")),
        (("authentication-service",), ("authentication-service", "postgresql")),
        (("postgresql",), ("postgresql",)),
        (("payment-gateway",), ("payment-gateway",)),
        (
            ("checkout-api", "billing-api"),
            (
                "checkout-api",
                "billing-api",
                "authentication-service",
                "postgresql",
                "payment-gateway",
            ),
        ),
        (
            ("billing-api", "checkout-api"),
            (
                "billing-api",
                "checkout-api",
                "postgresql",
                "payment-gateway",
                "authentication-service",
            ),
        ),
        (
            ("postgresql", "checkout-api"),
            ("postgresql", "checkout-api", "authentication-service", "payment-gateway"),
        ),
    ],
)
def test_scope_is_breadth_first_in_focus_then_catalog_order(
    focus: tuple[str, ...], scope: tuple[str, ...]
) -> None:
    assert fixed_playbook_scope(focus) == scope


def test_scope_expansion_is_transitive_breadth_first() -> None:
    catalog = {
        "a": {"type": "application", "dependencies": ["b", "c"]},
        "b": {"type": "application", "dependencies": ["d"]},
        "c": {"type": "database", "dependencies": ["d", "a"]},
        "d": {"type": "external", "dependencies": []},
    }
    assert fixed_playbook_scope(("a",), catalog) == ("a", "b", "c", "d")


def test_scope_removes_duplicates_on_first_occurrence() -> None:
    scope = fixed_playbook_scope(("checkout-api", "authentication-service", "postgresql"))
    assert scope == ("checkout-api", "authentication-service", "postgresql", "payment-gateway")
    assert len(set(scope)) == len(scope)


@pytest.mark.parametrize(
    "catalog",
    [
        {"a": {"type": "application", "dependencies": ["missing"]}},
        {"a": {"type": "queue", "dependencies": []}},
        {"a": {"type": "application", "dependencies": "b"}},
    ],
    ids=["unknown-dependency", "unknown-type", "malformed-dependencies"],
)
def test_invalid_catalog_is_a_configuration_error(
    catalog: dict[str, dict[str, object]],
) -> None:
    with pytest.raises(PlaybookConfigurationError):
        build_fixed_playbook(
            InitialInvestigationContext.model_construct(
                focus_services=("a",), initial_question="q", tool_constraints=()
            ),
            catalog,
        )


# ---------------------------------------------------------------------------
# Tool sequence
# ---------------------------------------------------------------------------


def test_checkout_playbook_exact_sequence() -> None:
    assert calls(build_fixed_playbook(context("checkout-api"))) == [
        *[(tool, {"service": "checkout-api"}) for tool in APPLICATION],
        *[(tool, {"service": "authentication-service"}) for tool in APPLICATION],
        *[(tool, {"service": "postgresql"}) for tool in DATABASE],
        ("get_external_service_status", {"provider": "payment-gateway"}),
        ("search_runbooks", {"query": ""}),
    ]


@pytest.mark.parametrize(
    ("focus", "size"),
    [
        (("checkout-api",), 12),
        (("billing-api",), 8),
        (("authentication-service",), 7),
        (("postgresql",), 3),
        (("payment-gateway",), 2),
        (("checkout-api", "billing-api"), 16),
        (tuple(SERVICE_CATALOG), 16),
    ],
)
def test_playbook_sizes(focus: tuple[str, ...], size: int) -> None:
    assert len(build_fixed_playbook(context(*focus)).steps) == size


def test_plan_is_not_truncated_to_the_step_budget() -> None:
    assert len(build_fixed_playbook(context(*SERVICE_CATALOG)).steps) == 16


@pytest.mark.parametrize("focus", ALL_FOCUS_COMBINATIONS)
def test_tools_follow_service_type(focus: tuple[str, ...]) -> None:
    plan = build_fixed_playbook(context(*focus))
    expected: list[tuple[str, dict[str, JsonValue]]] = []
    for service in fixed_playbook_scope(focus):
        service_type = SERVICE_CATALOG[service]["type"]
        if service_type == "application":
            expected += [(tool, {"service": service}) for tool in APPLICATION]
        elif service_type == "database":
            expected += [(tool, {"service": service}) for tool in DATABASE]
        else:
            expected.append(("get_external_service_status", {"provider": service}))
    expected.append(("search_runbooks", {"query": ""}))

    assert calls(plan) == expected


@pytest.mark.parametrize("focus", ALL_FOCUS_COMBINATIONS)
def test_no_root_cause_specific_filters(focus: tuple[str, ...]) -> None:
    for tool, arguments in calls(build_fixed_playbook(context(*focus))):
        assert set(arguments) <= {"service", "provider", "query"}
        if tool == "search_runbooks":
            assert arguments == {"query": ""}
        else:
            assert "query" not in arguments
        for forbidden in ("metric", "flag_name", "limit", "start_time", "end_time"):
            assert forbidden not in arguments


def test_runbook_step_is_last_and_unfiltered() -> None:
    last = build_fixed_playbook(context("billing-api")).steps[-1]
    assert (last.request.tool, last.request.arguments) == ("search_runbooks", {"query": ""})


def test_steps_have_bounded_deterministic_rationales() -> None:
    plan = build_fixed_playbook(context("checkout-api"))
    assert plan.steps[0].rationale == "fixed playbook: query_service_metrics for checkout-api"
    assert all(step.rationale for step in plan.steps)


@pytest.mark.parametrize("focus", ALL_FOCUS_COMBINATIONS)
def test_playbook_is_deterministic(focus: tuple[str, ...]) -> None:
    assert build_fixed_playbook(context(*focus)) == build_fixed_playbook(context(*focus))


@pytest.mark.parametrize("focus", ALL_FOCUS_COMBINATIONS)
def test_every_step_is_policy_permitted_and_nonduplicate(focus: tuple[str, ...]) -> None:
    prior: set[str] = set()
    for step in build_fixed_playbook(context(*focus)).steps:
        decision = evaluate_request(step.request, prior)
        assert decision.permitted, decision
        assert decision.call is not None
        prior.add(decision.call.fingerprint)


def test_constrained_steps_are_policy_permitted() -> None:
    plan = build_fixed_playbook(
        context("checkout-api", constraints=(constraint("get_deployment_history"),))
    )
    prior: set[str] = set()
    for step in plan.steps:
        decision = evaluate_request(step.request, prior)
        assert decision.block_reason is not BlockReason.INVALID_ARGUMENTS
        assert decision.permitted and decision.call is not None
        prior.add(decision.call.fingerprint)


# ---------------------------------------------------------------------------
# Tool-specific time constraints
# ---------------------------------------------------------------------------


def _windowed(plan: InvestigationPlan) -> list[tuple[str, JsonValue]]:
    return [
        (
            step.request.tool,
            step.request.arguments.get("service", step.request.arguments.get("provider")),
        )
        for step in plan.steps
        if "start_time" in step.request.arguments
    ]


def test_inc013_style_constraint_applies_only_to_deployment_history() -> None:
    plan = build_fixed_playbook(
        context("checkout-api", constraints=(constraint("get_deployment_history"),))
    )

    assert _windowed(plan) == [
        ("get_deployment_history", "checkout-api"),
        ("get_deployment_history", "authentication-service"),
    ]
    for step in plan.steps:
        if step.request.tool == "get_deployment_history":
            assert step.request.arguments["start_time"] == "2026-04-06T13:50:00Z"
            assert step.request.arguments["end_time"] == "2026-04-06T14:15:00Z"
    assert len(plan.steps) == 12


def test_service_scoped_constraint_applies_to_one_step() -> None:
    plan = build_fixed_playbook(
        context("checkout-api", constraints=(constraint("search_application_logs", "postgresql"),))
    )
    assert _windowed(plan) == [("search_application_logs", "postgresql")]


def test_external_constraint_matches_provider() -> None:
    plan = build_fixed_playbook(
        context(
            "billing-api",
            constraints=(constraint("get_external_service_status", "payment-gateway"),),
        )
    )
    assert _windowed(plan) == [("get_external_service_status", "payment-gateway")]


def test_constraint_times_are_canonical_utc() -> None:
    seoul = timezone(timedelta(hours=9))
    shifted = ToolTimeConstraint(
        tool="get_deployment_history",
        service="billing-api",
        start_time=START.astimezone(seoul),
        end_time=END.astimezone(seoul),
    )
    [step] = [
        step
        for step in build_fixed_playbook(context("billing-api", constraints=(shifted,))).steps
        if "start_time" in step.request.arguments
    ]
    assert step.request.arguments["start_time"] == "2026-04-06T13:50:00Z"


def test_no_constraints_means_no_time_window() -> None:
    assert _windowed(build_fixed_playbook(context(*SERVICE_CATALOG))) == []


def test_runbook_constraint_is_invalid() -> None:
    with pytest.raises(PlaybookConfigurationError, match="search_runbooks"):
        build_fixed_playbook(context("checkout-api", constraints=(constraint("search_runbooks"),)))


@pytest.mark.parametrize(
    ("focus", "unmatched"),
    [
        (("checkout-api",), constraint("get_deployment_history", "billing-api")),
        (("postgresql",), constraint("get_feature_flag_history")),
        (("postgresql",), constraint("get_external_service_status")),
        (("billing-api",), constraint("query_service_metrics", "authentication-service")),
    ],
    ids=["service-out-of-scope", "tool-not-for-database", "no-external", "dep-not-in-scope"],
)
def test_unmatched_constraint_is_invalid(
    focus: tuple[str, ...], unmatched: ToolTimeConstraint
) -> None:
    with pytest.raises(PlaybookConfigurationError, match="match no playbook step"):
        build_fixed_playbook(context(*focus, constraints=(unmatched,)))


def test_step_matched_by_two_constraints_is_invalid() -> None:
    both = (
        constraint("get_deployment_history"),
        constraint("get_deployment_history", "checkout-api"),
    )
    with pytest.raises(PlaybookConfigurationError, match="more than one"):
        build_fixed_playbook(context("checkout-api", constraints=both))


def test_building_does_not_mutate_context() -> None:
    ctx = context("checkout-api", constraints=(constraint("get_deployment_history"),))
    before = ctx.model_copy(deep=True)
    build_fixed_playbook(ctx)
    assert ctx == before
