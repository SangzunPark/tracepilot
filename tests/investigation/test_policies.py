"""Week 4 deterministic policies: allowlist, case_id ownership, schema-level
validation, normalization and fingerprints, duplicate blocking, step budget,
structural termination, and Adaptive plan-review opportunities."""

import copy
import hashlib
from typing import Any

import pytest
from pydantic import JsonValue

from tests.investigation.support import blocked, executed, plan, state
from tracepilot.investigation.models import (
    TOOL_ARGUMENT_MODELS,
    TOOL_NAMES,
    BlockReason,
    InvestigationMode,
    InvestigationPlan,
    Observation,
    TerminationReason,
    ToolCallRecord,
    ToolName,
    ToolRequest,
)
from tracepilot.investigation.policies import (
    ALLOWED_TOOLS,
    CASE_SCOPED_TOOLS,
    MAX_AGENT_STEPS,
    ArgumentValidationError,
    NormalizedCall,
    compute_fingerprint,
    evaluate_request,
    execution_arguments,
    is_allowed_tool,
    is_budget_exhausted,
    is_plan_exhausted,
    normalize_arguments,
    normalize_call,
    plan_review_pending,
    prior_fingerprints,
    remaining_steps,
    structural_termination,
)

# Minimal valid provider-facing arguments for each tool.
MINIMAL_ARGUMENTS: dict[ToolName, dict[str, JsonValue]] = {
    "query_service_metrics": {"service": "checkout-api"},
    "search_application_logs": {"service": "checkout-api"},
    "get_deployment_history": {"service": "checkout-api"},
    "get_feature_flag_history": {"service": "checkout-api"},
    "get_external_service_status": {"provider": "payment-gateway"},
    "search_runbooks": {"query": "connection pool"},
}

# Every optional argument with a None default, per tool.
NULLABLE_ARGUMENTS: list[tuple[ToolName, str]] = [
    ("query_service_metrics", "metric"),
    ("query_service_metrics", "start_time"),
    ("query_service_metrics", "end_time"),
    ("search_application_logs", "query"),
    ("search_application_logs", "start_time"),
    ("search_application_logs", "end_time"),
    ("get_deployment_history", "start_time"),
    ("get_deployment_history", "end_time"),
    ("get_feature_flag_history", "flag_name"),
    ("get_feature_flag_history", "start_time"),
    ("get_feature_flag_history", "end_time"),
    ("get_external_service_status", "start_time"),
    ("get_external_service_status", "end_time"),
    ("search_runbooks", "service"),
]

DATETIME_TOOLS: list[ToolName] = [tool for tool in TOOL_NAMES if tool != "search_runbooks"]


def _fingerprint(tool: ToolName, arguments: dict[str, JsonValue]) -> str:
    return normalize_call(tool, arguments).fingerprint


def _decide(tool: str, arguments: dict[str, JsonValue], prior: frozenset[str] = frozenset()) -> Any:
    return evaluate_request(ToolRequest(tool=tool, arguments=arguments), prior)


# ---------------------------------------------------------------------------
# Constants and allowlist
# ---------------------------------------------------------------------------


def test_allowed_tools_are_the_six_approved_tools() -> None:
    assert ALLOWED_TOOLS == frozenset(TOOL_NAMES)
    assert len(ALLOWED_TOOLS) == 6


def test_case_scoped_tools_are_all_but_search_runbooks() -> None:
    assert CASE_SCOPED_TOOLS == ALLOWED_TOOLS - {"search_runbooks"}
    assert len(CASE_SCOPED_TOOLS) == 5


def test_max_agent_steps() -> None:
    assert MAX_AGENT_STEPS == 15


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_each_allowed_tool_is_permitted(tool: ToolName) -> None:
    assert is_allowed_tool(tool)
    decision = _decide(tool, MINIMAL_ARGUMENTS[tool])
    assert decision.permitted
    assert decision.block_reason is None
    assert decision.call is not None and decision.call.tool == tool


@pytest.mark.parametrize(
    "tool",
    ["delete_database", "", "Search_Runbooks", "search_runbooks ", "query_service_metric"],
)
def test_unknown_tool_is_blocked(tool: str) -> None:
    assert not is_allowed_tool(tool)
    decision = _decide(tool, {"query": "x"})
    assert not decision.permitted
    assert decision.block_reason is BlockReason.UNKNOWN_TOOL
    assert decision.call is None
    assert decision.detail


# ---------------------------------------------------------------------------
# case_id ownership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", TOOL_NAMES)
@pytest.mark.parametrize("case_id", ["INC-001", "INC-002", "../ground_truth", None, ""])
def test_provider_supplied_case_id_is_blocked(tool: ToolName, case_id: str | None) -> None:
    decision = _decide(tool, {**MINIMAL_ARGUMENTS[tool], "case_id": case_id})

    assert not decision.permitted
    assert decision.block_reason is BlockReason.FORBIDDEN_ARGUMENT
    assert decision.call is None


@pytest.mark.parametrize("tool", sorted(CASE_SCOPED_TOOLS))
def test_runtime_case_id_is_injected_for_case_scoped_tools(tool: ToolName) -> None:
    call = normalize_call(tool, MINIMAL_ARGUMENTS[tool])

    arguments = execution_arguments(call, "INC-013")

    assert arguments["case_id"] == "INC-013"
    assert {key: value for key, value in arguments.items() if key != "case_id"} == call.arguments


def test_search_runbooks_gets_no_case_id() -> None:
    call = normalize_call("search_runbooks", MINIMAL_ARGUMENTS["search_runbooks"])

    arguments = execution_arguments(call, "INC-013")

    assert "case_id" not in arguments
    assert arguments == call.arguments


def test_execution_arguments_returns_a_new_dict() -> None:
    call = normalize_call("search_runbooks", MINIMAL_ARGUMENTS["search_runbooks"])
    arguments = execution_arguments(call, "INC-001")
    arguments["query"] = "changed"
    assert call.arguments["query"] == "connection pool"


def test_execution_arguments_rejects_smuggled_case_id_and_empty_runtime_case_id() -> None:
    smuggled = NormalizedCall("get_deployment_history", {"case_id": "INC-002"}, "f" * 64)
    with pytest.raises(ValueError):
        execution_arguments(smuggled, "INC-001")
    call = normalize_call("get_deployment_history", {"service": "checkout-api"})
    with pytest.raises(ValueError):
        execution_arguments(call, "")


def test_fingerprint_and_normalized_arguments_exclude_case_id() -> None:
    call = normalize_call("get_deployment_history", {"service": "checkout-api"})
    assert "case_id" not in call.arguments
    assert execution_arguments(call, "INC-001") != execution_arguments(call, "INC-002")
    # The fingerprint does not depend on which case is being investigated.
    assert call.fingerprint == compute_fingerprint("get_deployment_history", call.arguments)


# ---------------------------------------------------------------------------
# Schema-level validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("query_service_metrics", {}),
        ("get_external_service_status", {"service": "payment-gateway"}),
        ("search_runbooks", {"service": "postgresql"}),
        ("search_application_logs", {"service": "checkout-api", "level": "ERROR"}),
        ("search_runbooks", {"query": "pool", "case": "INC-001"}),
        ("search_application_logs", {"service": "checkout-api", "limit": None}),
        ("search_runbooks", {"query": "pool", "limit": None}),
        ("search_application_logs", {"service": "checkout-api", "limit": "many"}),
        ("search_application_logs", {"service": "checkout-api", "limit": 2.5}),
        ("query_service_metrics", {"service": 42}),
        ("query_service_metrics", {"service": None}),
        ("query_service_metrics", {"service": "checkout-api", "start_time": "yesterday"}),
        ("get_deployment_history", {"service": ["checkout-api"]}),
    ],
    ids=[
        "missing-service",
        "wrong-required-key",
        "missing-query",
        "unknown-key",
        "unknown-key-runbooks",
        "logs-limit-none",
        "runbooks-limit-none",
        "limit-not-int",
        "limit-fractional",
        "service-not-str",
        "service-none",
        "unparseable-datetime",
        "service-list",
    ],
)
def test_schema_invalid_arguments_are_blocked(
    tool: ToolName, arguments: dict[str, JsonValue]
) -> None:
    with pytest.raises(ArgumentValidationError):
        normalize_arguments(tool, arguments)

    decision = _decide(tool, arguments)

    assert not decision.permitted
    assert decision.block_reason is BlockReason.INVALID_ARGUMENTS
    assert decision.call is None
    assert decision.detail is not None and tool in decision.detail


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("query_service_metrics", {"service": "no-such-service"}),
        ("get_external_service_status", {"provider": "no-such-provider"}),
        (
            "search_application_logs",
            {
                "service": "checkout-api",
                "start_time": "2026-04-06T15:00:00Z",
                "end_time": "2026-04-06T14:00:00Z",
            },
        ),
        ("search_application_logs", {"service": "checkout-api", "limit": 0}),
        ("search_runbooks", {"query": "pool", "limit": -1}),
    ],
    ids=["unknown-service", "unknown-provider", "inverted-range", "logs-limit-0", "runbooks-neg"],
)
def test_domain_level_problems_are_left_for_mcp(
    tool: ToolName, arguments: dict[str, JsonValue]
) -> None:
    assert _decide(tool, arguments).permitted


def test_lax_coercions_match_the_mcp_server() -> None:
    normalized = normalize_arguments(
        "search_application_logs", {"service": "checkout-api", "limit": "50"}
    )
    assert normalized["limit"] == 50


def test_validation_detail_does_not_echo_argument_values() -> None:
    decision = _decide("search_runbooks", {"query": "pool", "secret_token": "s3cr3t"})
    assert decision.detail is not None
    assert "s3cr3t" not in decision.detail


# ---------------------------------------------------------------------------
# Normalization and fingerprints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_normalization_materializes_every_field(tool: ToolName) -> None:
    normalized = normalize_arguments(tool, MINIMAL_ARGUMENTS[tool])

    assert set(normalized) == set(TOOL_ARGUMENT_MODELS[tool].model_fields)


def test_omitted_logs_limit_equals_explicit_default() -> None:
    base: dict[str, JsonValue] = {"service": "checkout-api", "query": "timeout"}
    assert _fingerprint("search_application_logs", base) == _fingerprint(
        "search_application_logs", {**base, "limit": 50}
    )
    assert _fingerprint("search_application_logs", base) != _fingerprint(
        "search_application_logs", {**base, "limit": 49}
    )


def test_omitted_runbooks_limit_equals_explicit_default() -> None:
    base: dict[str, JsonValue] = {"query": "connection pool"}
    assert _fingerprint("search_runbooks", base) == _fingerprint(
        "search_runbooks", {**base, "limit": 20}
    )
    assert _fingerprint("search_runbooks", base) != _fingerprint(
        "search_runbooks", {**base, "limit": 50}
    )


@pytest.mark.parametrize(("tool", "argument"), NULLABLE_ARGUMENTS)
def test_omitted_nullable_equals_explicit_none(tool: ToolName, argument: str) -> None:
    base = MINIMAL_ARGUMENTS[tool]
    assert _fingerprint(tool, base) == _fingerprint(tool, {**base, argument: None})


EQUIVALENT_DATETIMES: list[JsonValue] = [
    "2026-04-06T13:50:00Z",
    "2026-04-06T13:50:00+00:00",
    "2026-04-06T22:50:00+09:00",
    "2026-04-06T08:50:00-05:00",
    "2026-04-06T13:50:00",
    "2026-04-06T13:50:00.000Z",
    1775483400,
]


@pytest.mark.parametrize("tool", DATETIME_TOOLS)
@pytest.mark.parametrize("field", ["start_time", "end_time"])
def test_equivalent_datetimes_normalize_to_one_utc_representation(
    tool: ToolName, field: str
) -> None:
    base = MINIMAL_ARGUMENTS[tool]
    normalized = [
        normalize_arguments(tool, {**base, field: value})[field] for value in EQUIVALENT_DATETIMES
    ]
    fingerprints = {_fingerprint(tool, {**base, field: value}) for value in EQUIVALENT_DATETIMES}

    assert set(normalized) == {"2026-04-06T13:50:00Z"}
    assert len(fingerprints) == 1


def test_different_instants_fingerprint_differently() -> None:
    base: dict[str, JsonValue] = {"service": "checkout-api"}
    assert _fingerprint(
        "get_deployment_history", {**base, "start_time": "2026-04-06T13:50:00Z"}
    ) != _fingerprint("get_deployment_history", {**base, "start_time": "2026-04-06T13:51:00Z"})


def test_microseconds_are_preserved() -> None:
    normalized = normalize_arguments(
        "get_deployment_history",
        {"service": "checkout-api", "start_time": "2026-04-06T13:50:00.123456+09:00"},
    )
    assert normalized["start_time"] == "2026-04-06T04:50:00.123456Z"


def test_key_order_does_not_affect_fingerprint() -> None:
    forward: dict[str, JsonValue] = {
        "service": "checkout-api",
        "query": "timeout",
        "start_time": "2026-04-06T13:50:00Z",
        "limit": 10,
    }
    backward = dict(reversed(list(forward.items())))
    assert list(forward) != list(backward)
    assert _fingerprint("search_application_logs", forward) == _fingerprint(
        "search_application_logs", backward
    )
    assert compute_fingerprint("search_runbooks", {"query": "a", "limit": 1}) == (
        compute_fingerprint("search_runbooks", {"limit": 1, "query": "a"})
    )


def test_tool_name_is_part_of_identity() -> None:
    arguments: dict[str, JsonValue] = {"service": "checkout-api"}
    assert _fingerprint("get_deployment_history", arguments) != _fingerprint(
        "get_feature_flag_history", arguments
    )


@pytest.mark.parametrize(
    ("tool", "first", "second"),
    [
        ("search_application_logs", {"query": "timeout"}, {"query": "Timeout"}),
        ("search_application_logs", {"query": "timeout"}, {"query": " timeout "}),
        ("search_application_logs", {"query": ""}, {"query": None}),
        ("search_runbooks", {"query": "pool"}, {"query": "POOL"}),
        ("search_runbooks", {"query": "pool", "service": ""}, {"query": "pool"}),
        ("query_service_metrics", {"service": "checkout-api"}, {"service": "Checkout-API"}),
    ],
    ids=["query-case", "query-whitespace", "empty-vs-none", "runbook-case", "empty-service", "svc"],
)
def test_intentionally_non_normalized_arguments(
    tool: ToolName, first: dict[str, JsonValue], second: dict[str, JsonValue]
) -> None:
    base = {key: value for key, value in MINIMAL_ARGUMENTS[tool].items() if key not in first}
    assert _fingerprint(tool, {**base, **first}) != _fingerprint(tool, {**base, **second})


def test_fingerprint_is_deterministic_sha256_hex() -> None:
    first = _fingerprint("search_runbooks", {"query": "pool"})
    second = _fingerprint("search_runbooks", {"query": "pool"})
    assert first == second
    assert len(first) == 64
    assert int(first, 16) >= 0


def test_fingerprint_is_pinned() -> None:
    # Guards the canonical form: changing it would silently change identities.
    canonical = (
        '{"arguments":{"limit":20,"query":"connection pool","service":null},'
        '"tool":"search_runbooks"}'
    )
    assert _fingerprint("search_runbooks", {"query": "connection pool"}) == (
        hashlib.sha256(canonical.encode()).hexdigest()
    )


def test_policy_functions_do_not_mutate_inputs() -> None:
    arguments: dict[str, JsonValue] = {
        "service": "checkout-api",
        "start_time": "2026-04-06T22:50:00+09:00",
    }
    request = ToolRequest(tool="search_application_logs", arguments=arguments)
    before_arguments = copy.deepcopy(arguments)
    before_request = request.model_copy(deep=True)
    prior = {"0" * 64}

    normalize_arguments("search_application_logs", arguments)
    normalize_call("search_application_logs", arguments)
    decision = evaluate_request(request, prior)
    assert decision.call is not None
    call_arguments = copy.deepcopy(decision.call.arguments)
    execution_arguments(decision.call, "INC-001")

    assert arguments == before_arguments
    assert request == before_request
    assert prior == {"0" * 64}
    assert decision.call.arguments == call_arguments


# ---------------------------------------------------------------------------
# Duplicate policy
# ---------------------------------------------------------------------------


def _permitted_record(step: int, tool: ToolName, arguments: dict[str, JsonValue]) -> ToolCallRecord:
    call = normalize_call(tool, arguments)
    return ToolCallRecord(
        step=step,
        tool=tool,
        normalized_arguments=call.arguments,
        fingerprint=call.fingerprint,
        permitted=True,
        executed=True,
        observation=Observation.from_records(()),
    )


def test_identical_second_request_is_blocked_as_duplicate() -> None:
    first = _decide("search_application_logs", {"service": "checkout-api"})
    assert first.permitted and first.call is not None

    second = _decide(
        "search_application_logs",
        {"limit": 50, "service": "checkout-api", "query": None},
        frozenset({first.call.fingerprint}),
    )

    assert not second.permitted
    assert second.block_reason is BlockReason.DUPLICATE
    assert second.call is not None
    assert second.call.fingerprint == first.call.fingerprint


def test_non_identical_request_is_not_a_duplicate() -> None:
    first = normalize_call("search_application_logs", {"service": "checkout-api"})
    decision = _decide(
        "search_application_logs",
        {"service": "checkout-api", "limit": 10},
        frozenset({first.fingerprint}),
    )
    assert decision.permitted


def test_prior_fingerprints_come_from_permitted_records_only() -> None:
    permitted = _permitted_record(1, "search_runbooks", {"query": "pool"})
    records = (
        permitted,
        blocked(2, BlockReason.INVALID_ARGUMENTS),
        blocked(3, BlockReason.FORBIDDEN_ARGUMENT),
        blocked(4, BlockReason.UNKNOWN_TOOL, tool="rm"),
        blocked(5, BlockReason.DUPLICATE),
    )
    assert prior_fingerprints(records) == {permitted.fingerprint}


def test_blocked_invalid_request_does_not_seed_duplicate_set() -> None:
    invalid = _decide("search_application_logs", {"service": "checkout-api", "limit": None})
    assert invalid.block_reason is BlockReason.INVALID_ARGUMENTS

    record = ToolCallRecord(
        step=1,
        tool="search_application_logs",
        permitted=False,
        block_reason=invalid.block_reason,
        block_detail=invalid.detail,
    )
    corrected = _decide(
        "search_application_logs", {"service": "checkout-api"}, prior_fingerprints([record])
    )
    assert corrected.permitted


def test_permitted_call_with_mcp_error_still_seeds_duplicate_set() -> None:
    call = normalize_call("query_service_metrics", {"service": "no-such-service"})
    failed = ToolCallRecord(
        step=1,
        tool="query_service_metrics",
        normalized_arguments=call.arguments,
        fingerprint=call.fingerprint,
        permitted=True,
        executed=True,
        observation=Observation.from_error("Error executing tool query_service_metrics: ..."),
    )

    retry = _decide(
        "query_service_metrics", {"service": "no-such-service"}, prior_fingerprints([failed])
    )

    assert retry.block_reason is BlockReason.DUPLICATE


# ---------------------------------------------------------------------------
# Evaluation precedence
# ---------------------------------------------------------------------------


def test_unknown_tool_takes_precedence_over_case_id() -> None:
    decision = _decide("delete_database", {"case_id": "INC-002", "bogus": 1})
    assert decision.block_reason is BlockReason.UNKNOWN_TOOL


def test_case_id_takes_precedence_over_invalid_arguments() -> None:
    decision = _decide("search_application_logs", {"case_id": "INC-001", "limit": None})
    assert decision.block_reason is BlockReason.FORBIDDEN_ARGUMENT


def test_case_id_takes_precedence_over_duplicate() -> None:
    prior = frozenset({normalize_call("search_runbooks", {"query": "pool"}).fingerprint})
    decision = _decide("search_runbooks", {"query": "pool", "case_id": "INC-001"}, prior)
    assert decision.block_reason is BlockReason.FORBIDDEN_ARGUMENT


def test_invalid_arguments_take_precedence_over_duplicate() -> None:
    # Every fingerprint seen so far; an invalid request still reports INVALID.
    prior = frozenset(
        normalize_call(tool, MINIMAL_ARGUMENTS[tool]).fingerprint for tool in TOOL_NAMES
    )
    decision = _decide("search_runbooks", {"query": "connection pool", "limit": None}, prior)
    assert decision.block_reason is BlockReason.INVALID_ARGUMENTS


# ---------------------------------------------------------------------------
# Step budget and structural termination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent_steps", "remaining", "exhausted"),
    [(0, 15, False), (14, 1, False), (15, 0, True), (16, 0, True)],
)
def test_default_budget_boundaries(agent_steps: int, remaining: int, exhausted: bool) -> None:
    assert remaining_steps(agent_steps) == remaining
    assert is_budget_exhausted(agent_steps) is exhausted


@pytest.mark.parametrize(
    ("agent_steps", "remaining", "exhausted"),
    [(0, 3, False), (2, 1, False), (3, 0, True), (4, 0, True)],
)
def test_custom_budget(agent_steps: int, remaining: int, exhausted: bool) -> None:
    assert remaining_steps(agent_steps, max_steps=3) == remaining
    assert is_budget_exhausted(agent_steps, max_steps=3) is exhausted


@pytest.mark.parametrize(("agent_steps", "max_steps"), [(-1, 15), (0, 0), (0, -1)])
def test_budget_rejects_invalid_inputs(agent_steps: int, max_steps: int) -> None:
    with pytest.raises(ValueError):
        remaining_steps(agent_steps, max_steps)
    with pytest.raises(ValueError):
        is_budget_exhausted(agent_steps, max_steps)


def test_blocked_attempts_consume_budget() -> None:
    records = tuple(blocked(step) for step in range(1, 16))
    current = state(records=records)
    assert current.logical_tool_calls == 0
    assert is_budget_exhausted(current.step_count)


def test_plan_exhaustion() -> None:
    two_steps = plan("search_runbooks", "search_runbooks")
    assert not is_plan_exhausted(state(plan_version=0))
    assert is_plan_exhausted(state(investigation_plan=InvestigationPlan()))
    assert not is_plan_exhausted(state(investigation_plan=two_steps, current_step_index=1))
    assert is_plan_exhausted(state(investigation_plan=two_steps, current_step_index=2))


ALL_MODES = list(InvestigationMode)
NON_ADAPTIVE_MODES = [InvestigationMode.FIXED_PLAYBOOK, InvestigationMode.PLAN_ONCE]


def _executed_steps(count: int) -> tuple[ToolCallRecord, ...]:
    return tuple(executed(step, fingerprint=f"{step:064x}") for step in range(1, count + 1))


@pytest.mark.parametrize("mode", ALL_MODES)
def test_structural_termination_none_while_plan_and_budget_remain(
    mode: InvestigationMode,
) -> None:
    current = state(mode, records=_executed_steps(1), current_step_index=1)
    assert structural_termination(current) is None


@pytest.mark.parametrize("mode", ALL_MODES)
def test_structural_termination_none_before_any_plan(mode: InvestigationMode) -> None:
    assert structural_termination(state(mode, plan_version=0)) is None


@pytest.mark.parametrize("mode", ALL_MODES)
def test_budget_exhausted_with_plan_remaining(mode: InvestigationMode) -> None:
    current = state(mode, records=_executed_steps(15), current_step_index=1)
    assert structural_termination(current) is TerminationReason.BUDGET_EXHAUSTED


@pytest.mark.parametrize("mode", NON_ADAPTIVE_MODES)
def test_non_adaptive_plan_exhausted(mode: InvestigationMode) -> None:
    current = state(mode, records=_executed_steps(2), current_step_index=2)
    assert structural_termination(current) is TerminationReason.PLAN_EXHAUSTED


@pytest.mark.parametrize("mode", NON_ADAPTIVE_MODES)
def test_non_adaptive_terminal_plan_exhaustion_beats_budget(mode: InvestigationMode) -> None:
    # The last plan step is also the last budgeted step.
    current = state(mode, records=_executed_steps(3), current_step_index=2)
    assert structural_termination(current, max_steps=3) is TerminationReason.PLAN_EXHAUSTED


@pytest.mark.parametrize("mode", ALL_MODES)
def test_empty_plan_is_exhausted_immediately(mode: InvestigationMode) -> None:
    current = state(mode, investigation_plan=InvestigationPlan())
    assert structural_termination(current) is TerminationReason.PLAN_EXHAUSTED


def test_adaptive_exhausted_plan_with_fresh_observation_is_not_terminal() -> None:
    # executed action -> new observation -> plan exhausted -> review pending -> continue
    current = state(records=_executed_steps(2), current_step_index=2)
    assert is_plan_exhausted(current)
    assert plan_review_pending(current)
    assert structural_termination(current) is None


def test_adaptive_exhausted_plan_with_fresh_error_observation_is_not_terminal() -> None:
    current = state(records=(executed(1), executed(2, error=True)), current_step_index=2)
    assert structural_termination(current) is None


def test_adaptive_review_pending_but_budget_exhausted() -> None:
    current = state(records=_executed_steps(3), current_step_index=2)
    assert plan_review_pending(current)
    assert structural_termination(current, max_steps=3) is TerminationReason.BUDGET_EXHAUSTED
    current = state(records=_executed_steps(15), current_step_index=2)
    assert structural_termination(current) is TerminationReason.BUDGET_EXHAUSTED


def test_adaptive_exhausted_plan_without_fresh_observation_is_plan_exhausted() -> None:
    # The plan was decided at step 2 and nothing has executed since.
    revised = state(
        records=_executed_steps(2), plan_decision_at_step=2, plan_version=2, current_step_index=2
    )
    assert not plan_review_pending(revised)
    assert structural_termination(revised) is TerminationReason.PLAN_EXHAUSTED


def test_adaptive_exhausted_plan_after_only_blocked_attempts_is_plan_exhausted() -> None:
    # observation -> review -> blocked attempts exhaust the plan
    current = state(
        records=(executed(1), blocked(2), blocked(3, BlockReason.DUPLICATE)),
        plan_decision_at_step=1,
        plan_version=2,
        current_step_index=2,
    )
    assert not plan_review_pending(current)
    assert structural_termination(current) is TerminationReason.PLAN_EXHAUSTED


def test_adaptive_terminal_plan_exhaustion_beats_budget() -> None:
    current = state(
        records=_executed_steps(3), plan_decision_at_step=3, plan_version=2, current_step_index=2
    )
    assert structural_termination(current, max_steps=3) is TerminationReason.PLAN_EXHAUSTED


@pytest.mark.parametrize("reason", list(TerminationReason))
def test_recorded_termination_reason_is_final(reason: TerminationReason) -> None:
    # Facts alone would give None (Adaptive, review pending, budget left).
    current = state(records=_executed_steps(2), current_step_index=2).model_copy(
        update={"termination_reason": reason}
    )
    assert not plan_review_pending(current)
    assert structural_termination(current) is reason


# ---------------------------------------------------------------------------
# Replanning permission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [InvestigationMode.FIXED_PLAYBOOK, InvestigationMode.PLAN_ONCE])
def test_non_adaptive_modes_never_review(mode: InvestigationMode) -> None:
    assert not plan_review_pending(state(mode))
    assert not plan_review_pending(state(mode, records=(executed(1),)))
    assert not plan_review_pending(state(mode, records=(executed(1), executed(2, error=True))))


def test_adaptive_without_plan_has_no_pending_review() -> None:
    assert not plan_review_pending(state(plan_version=0))


def test_adaptive_has_no_pending_review_right_after_initial_plan() -> None:
    assert not plan_review_pending(state(plan_decision_at_step=0))


def test_adaptive_review_pending_after_an_executed_observation() -> None:
    assert plan_review_pending(state(records=(executed(1),), plan_decision_at_step=0))


def test_adaptive_one_observation_yields_one_review() -> None:
    revised = state(records=(executed(1),), plan_decision_at_step=1, plan_version=2)
    assert not plan_review_pending(revised)


def test_blocked_attempt_creates_no_plan_review() -> None:
    # observation -> review -> blocked -> (no review) -> blocked -> (no review)
    after_block = state(records=(executed(1), blocked(2)), plan_decision_at_step=1, plan_version=2)
    assert not plan_review_pending(after_block)

    for reason in BlockReason:
        current = state(
            records=(executed(1), blocked(2, reason), blocked(3, reason)),
            plan_decision_at_step=1,
            plan_version=2,
        )
        assert not plan_review_pending(current)


def test_blocked_attempts_alone_never_create_a_review() -> None:
    current = state(records=(blocked(1), blocked(2, BlockReason.DUPLICATE)))
    assert not plan_review_pending(current)


def test_new_executed_observation_after_block_creates_a_review() -> None:
    current = state(
        records=(executed(1), blocked(2), executed(3, fingerprint="0" * 64)),
        plan_decision_at_step=1,
        plan_version=2,
    )
    assert plan_review_pending(current)


def test_executed_error_observation_creates_a_review() -> None:
    assert plan_review_pending(state(records=(executed(1, error=True),)))


def test_permitted_but_unexecuted_attempt_creates_no_review() -> None:
    not_executed = ToolCallRecord(
        step=1,
        tool="search_runbooks",
        normalized_arguments={"query": "pool"},
        fingerprint="0" * 64,
        permitted=True,
    )
    assert not plan_review_pending(state(records=(not_executed,)))


@pytest.mark.parametrize("reason", list(TerminationReason))
def test_terminated_adaptive_has_no_pending_review(reason: TerminationReason) -> None:
    eligible = state(records=(executed(1),))
    assert plan_review_pending(eligible)
    terminated = eligible.model_copy(update={"termination_reason": reason})
    assert not plan_review_pending(terminated)
