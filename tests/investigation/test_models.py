"""Week 4 investigation data contracts: enums, initial context, proposals,
observations, tool-call records, and derived state counters."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from tests.investigation.support import (
    FINGERPRINT,
    blocked,
    executed,
    inc013_context,
    inc015_context,
    plan,
    state,
)
from tracepilot.investigation.models import (
    MAX_ERROR_MESSAGE_CHARS,
    MAX_OBSERVATION_RECORDS,
    TOOL_NAMES,
    BlockReason,
    InitialInvestigationContext,
    InvestigationMode,
    InvestigationPlan,
    InvestigationState,
    Observation,
    PlanStep,
    TerminationReason,
    ToolCallRecord,
    ToolRequest,
    ToolTimeConstraint,
)

START = datetime(2026, 4, 6, 13, 50, tzinfo=UTC)
END = datetime(2026, 4, 6, 14, 15, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Enums and tool names
# ---------------------------------------------------------------------------


def test_investigation_mode_values() -> None:
    assert {mode.value for mode in InvestigationMode} == {
        "fixed_playbook",
        "plan_once",
        "adaptive",
    }


def test_termination_reason_has_exactly_two_structural_values() -> None:
    assert [reason.value for reason in TerminationReason] == [
        "plan_exhausted",
        "budget_exhausted",
    ]


def test_block_reason_values() -> None:
    assert {reason.value for reason in BlockReason} == {
        "unknown_tool",
        "forbidden_argument",
        "invalid_arguments",
        "duplicate",
    }


def test_tool_names_are_the_six_approved_tools() -> None:
    assert TOOL_NAMES == (
        "query_service_metrics",
        "search_application_logs",
        "get_deployment_history",
        "get_feature_flag_history",
        "get_external_service_status",
        "search_runbooks",
    )


# ---------------------------------------------------------------------------
# Initial investigation context
# ---------------------------------------------------------------------------


def test_inc013_style_context_scopes_its_range_to_one_tool() -> None:
    context = inc013_context()

    assert context.focus_services == ("checkout-api",)
    assert len(context.tool_constraints) == 1
    constraint = context.tool_constraints[0]
    assert constraint.tool == "get_deployment_history"
    assert constraint.service is None
    assert (constraint.start_time, constraint.end_time) == (START, END)


def test_inc015_style_context_has_no_time_constraint() -> None:
    context = inc015_context()

    assert context.focus_services == ("billing-api",)
    assert context.initial_question
    assert context.tool_constraints == ()


@pytest.mark.parametrize(
    "forbidden_field",
    ["case_id", "category", "root_cause", "time_window", "required_evidence_ids"],
)
def test_context_rejects_forbidden_fields(forbidden_field: str) -> None:
    with pytest.raises(ValidationError):
        InitialInvestigationContext.model_validate(
            {
                "focus_services": ["checkout-api"],
                "initial_question": "Why?",
                forbidden_field: "x",
            }
        )


def test_context_has_no_case_or_answer_fields() -> None:
    assert set(InitialInvestigationContext.model_fields) == {
        "focus_services",
        "initial_question",
        "tool_constraints",
    }


@pytest.mark.parametrize(
    "focus_services",
    [(), ("checkout-api", "checkout-api"), ("unknown-service",), ("Checkout-API",)],
    ids=["empty", "duplicate", "unknown", "wrong-case"],
)
def test_context_rejects_invalid_focus_services(focus_services: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        InitialInvestigationContext(focus_services=focus_services, initial_question="Why?")


def test_context_accepts_every_catalog_service() -> None:
    context = InitialInvestigationContext(
        focus_services=(
            "checkout-api",
            "billing-api",
            "authentication-service",
            "postgresql",
            "payment-gateway",
        ),
        initial_question="Why?",
    )
    assert len(context.focus_services) == 5


@pytest.mark.parametrize("question", ["", "   "])
def test_context_requires_non_empty_initial_question(question: str) -> None:
    with pytest.raises(ValidationError):
        InitialInvestigationContext(focus_services=("checkout-api",), initial_question=question)


def test_context_requires_initial_question() -> None:
    with pytest.raises(ValidationError):
        InitialInvestigationContext.model_validate({"focus_services": ["checkout-api"]})


def test_context_is_frozen() -> None:
    context = inc015_context()
    with pytest.raises(ValidationError):
        context.initial_question = "changed"


def test_time_constraint_accepts_service_and_equal_bounds() -> None:
    constraint = ToolTimeConstraint(
        tool="search_application_logs", service="checkout-api", start_time=START, end_time=START
    )
    assert constraint.service == "checkout-api"


def test_time_constraint_rejects_naive_datetimes() -> None:
    with pytest.raises(ValidationError):
        ToolTimeConstraint(
            tool="get_deployment_history",
            start_time=datetime(2026, 4, 6, 13, 50),
            end_time=END,
        )


def test_time_constraint_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError):
        ToolTimeConstraint(tool="get_deployment_history", start_time=END, end_time=START)


def test_time_constraint_rejects_unknown_tool() -> None:
    with pytest.raises(ValidationError):
        ToolTimeConstraint.model_validate(
            {"tool": "delete_database", "start_time": START, "end_time": END}
        )


def test_time_constraint_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ToolTimeConstraint.model_validate(
            {"tool": "get_deployment_history", "start_time": START, "end_time": END, "x": 1}
        )


# ---------------------------------------------------------------------------
# Tool requests and plans
# ---------------------------------------------------------------------------


def test_tool_request_accepts_unknown_tool() -> None:
    request = ToolRequest(tool="delete_database", arguments={"force": True})
    assert request.tool == "delete_database"


def test_tool_request_can_represent_provider_supplied_case_id() -> None:
    request = ToolRequest(
        tool="query_service_metrics",
        arguments={"case_id": "INC-002", "service": "checkout-api"},
    )
    assert request.arguments["case_id"] == "INC-002"


def test_tool_request_arguments_default_is_not_shared() -> None:
    first = ToolRequest(tool="search_runbooks")
    second = ToolRequest(tool="search_runbooks")
    assert first.arguments == {}
    assert first.arguments is not second.arguments


def test_tool_request_is_frozen_and_forbids_extra_fields() -> None:
    request = ToolRequest(tool="search_runbooks")
    with pytest.raises(ValidationError):
        request.tool = "other"
    with pytest.raises(ValidationError):
        ToolRequest.model_validate({"tool": "search_runbooks", "case_id": "INC-001"})


def test_plan_step_bounds_rationale() -> None:
    request = ToolRequest(tool="search_runbooks")
    assert PlanStep(request=request, rationale="r" * 300).rationale is not None
    with pytest.raises(ValidationError):
        PlanStep(request=request, rationale="r" * 301)


def test_empty_plan_is_allowed() -> None:
    assert InvestigationPlan().steps == ()


def test_plan_has_no_version_field() -> None:
    assert set(InvestigationPlan.model_fields) == {"steps"}


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


def test_observation_from_records_retains_at_most_fifty() -> None:
    records = tuple({"source_id": f"log-{index}"} for index in range(60))

    observation = Observation.from_records(records)

    assert observation.record_count == 60
    assert len(observation.records) == MAX_OBSERVATION_RECORDS
    assert observation.records == records[:MAX_OBSERVATION_RECORDS]
    assert observation.truncated is True


def test_observation_from_records_without_truncation() -> None:
    observation = Observation.from_records(({"source_id": "log-1"},))
    assert (observation.record_count, observation.truncated) == (1, False)


def test_empty_observation_is_not_an_error() -> None:
    observation = Observation.from_records(())
    assert (observation.is_error, observation.record_count, observation.records) == (
        False,
        0,
        (),
    )


def test_observation_from_error_truncates_message() -> None:
    observation = Observation.from_error("e" * 900)

    assert observation.is_error is True
    assert observation.error_message == "e" * MAX_ERROR_MESSAGE_CHARS
    assert (observation.record_count, observation.records) == (0, ())


@pytest.mark.parametrize(
    "fields",
    [
        {"is_error": True, "record_count": 0},
        {"is_error": True, "error_message": "x", "record_count": 1},
        {"is_error": False, "error_message": "x", "record_count": 0},
        {"is_error": False, "record_count": 0, "records": [{"a": 1}]},
        {"is_error": False, "record_count": 2, "records": [{"a": 1}]},
        {"is_error": False, "record_count": 1, "records": [{"a": 1}], "truncated": True},
        {"is_error": False, "record_count": 51, "records": [{"a": i} for i in range(51)]},
        {"is_error": True, "error_message": "e" * 501, "record_count": 0},
    ],
    ids=[
        "error-without-message",
        "error-with-records",
        "message-without-error",
        "records-exceed-count",
        "dropped-but-not-truncated",
        "truncated-without-drop",
        "too-many-records",
        "message-too-long",
    ],
)
def test_observation_rejects_inconsistent_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate(fields)


# ---------------------------------------------------------------------------
# Tool-call records
# ---------------------------------------------------------------------------


def test_valid_records() -> None:
    assert executed(1).permitted and executed(1).executed
    for reason in BlockReason:
        record = blocked(1, reason)
        assert not record.permitted and not record.executed
        assert record.block_reason is reason


def test_permitted_but_not_executed_record_is_representable() -> None:
    record = ToolCallRecord(
        step=1,
        tool="search_runbooks",
        normalized_arguments={"query": "pool", "service": None, "limit": 20},
        fingerprint=FINGERPRINT,
        permitted=True,
    )
    assert (record.executed, record.observation) == (False, None)


def test_blocked_records_do_not_store_arguments_except_duplicates() -> None:
    assert blocked(1, BlockReason.INVALID_ARGUMENTS).normalized_arguments is None
    assert blocked(1, BlockReason.FORBIDDEN_ARGUMENT).normalized_arguments is None
    assert blocked(1, BlockReason.UNKNOWN_TOOL, tool="rm").normalized_arguments is None
    assert blocked(1, BlockReason.DUPLICATE).fingerprint == FINGERPRINT


VALID_EXECUTED: dict[str, Any] = {
    "step": 1,
    "tool": "search_runbooks",
    "normalized_arguments": {"query": "pool"},
    "fingerprint": FINGERPRINT,
    "permitted": True,
    "executed": True,
    "observation": {"is_error": False, "record_count": 0},
}
VALID_BLOCKED: dict[str, Any] = {
    "step": 1,
    "tool": "search_runbooks",
    "permitted": False,
    "block_reason": "invalid_arguments",
    "block_detail": "blocked",
}


@pytest.mark.parametrize(
    "fields",
    [
        {**VALID_EXECUTED, "block_reason": "duplicate", "block_detail": "x"},
        {**VALID_BLOCKED, "block_reason": None},
        {**VALID_BLOCKED, "executed": True, "observation": {"is_error": False, "record_count": 0}},
        {**VALID_EXECUTED, "observation": None},
        {**VALID_EXECUTED, "executed": False},
        {**VALID_BLOCKED, "block_detail": None},
        {**VALID_EXECUTED, "block_detail": "x"},
        {**VALID_EXECUTED, "fingerprint": None},
        {**VALID_EXECUTED, "normalized_arguments": None},
        {**VALID_EXECUTED, "normalized_arguments": None, "fingerprint": None},
        {**VALID_BLOCKED, "normalized_arguments": {"query": "x"}, "fingerprint": FINGERPRINT},
        {**VALID_BLOCKED, "block_reason": "duplicate"},
        {**VALID_EXECUTED, "tool": "delete_database"},
        {**VALID_EXECUTED, "step": 0},
        {**VALID_BLOCKED, "block_detail": "d" * 301},
    ],
    ids=[
        "permitted-with-block-reason",
        "blocked-without-block-reason",
        "blocked-but-executed",
        "executed-without-observation",
        "observation-without-execution",
        "blocked-without-detail",
        "permitted-with-detail",
        "arguments-without-fingerprint",
        "fingerprint-without-arguments",
        "permitted-without-call",
        "invalid-block-storing-arguments",
        "duplicate-without-call",
        "permitted-unknown-tool",
        "zero-step",
        "detail-too-long",
    ],
)
def test_tool_call_record_rejects_inconsistent_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ToolCallRecord.model_validate(fields)


# ---------------------------------------------------------------------------
# Investigation state
# ---------------------------------------------------------------------------


def test_new_state_has_no_plan_and_zero_counts() -> None:
    new = InvestigationState(
        case_id="INC-015", mode=InvestigationMode.PLAN_ONCE, initial_context=inc015_context()
    )

    assert (new.plan, new.plan_version, new.current_step_index) == (None, 0, 0)
    assert (new.step_count, new.logical_tool_calls, new.execution_attempts) == (0, 0, 0)
    assert (new.open_questions, new.working_hypothesis, new.termination_reason) == ((), None, None)


def test_derived_counters_follow_counting_semantics() -> None:
    records = (
        executed(1),
        blocked(2, BlockReason.INVALID_ARGUMENTS),
        blocked(3, BlockReason.FORBIDDEN_ARGUMENT),
        blocked(4, BlockReason.DUPLICATE),
        blocked(5, BlockReason.UNKNOWN_TOOL, tool="rm"),
        executed(6, error=True),
    )

    current = state(records=records)

    assert current.step_count == 6
    assert current.logical_tool_calls == 2
    assert current.execution_attempts == 2


def test_state_does_not_store_counters() -> None:
    fields = set(InvestigationState.model_fields)
    assert fields.isdisjoint({"step_count", "logical_tool_calls", "execution_attempts"})
    assert "step_count" not in state().model_dump()


def test_state_fields_match_week4_contract() -> None:
    assert set(InvestigationState.model_fields) == {
        "case_id",
        "mode",
        "initial_context",
        "plan",
        "plan_version",
        "current_step_index",
        "plan_decision_at_step",
        "tool_calls",
        "open_questions",
        "working_hypothesis",
        "termination_reason",
    }


def test_state_requires_consecutive_steps() -> None:
    with pytest.raises(ValidationError):
        state(records=(executed(1), executed(3)))
    with pytest.raises(ValidationError):
        state(records=(executed(2),))


def test_state_ties_plan_version_to_plan() -> None:
    with pytest.raises(ValidationError):
        state(plan_version=0, investigation_plan=plan("search_runbooks"))
    with pytest.raises(ValidationError):
        InvestigationState(
            case_id="INC-001",
            mode=InvestigationMode.ADAPTIVE,
            initial_context=inc015_context(),
            plan_version=1,
        )


@pytest.mark.parametrize("mode", [InvestigationMode.FIXED_PLAYBOOK, InvestigationMode.PLAN_ONCE])
def test_non_adaptive_state_cannot_have_revised_plan(mode: InvestigationMode) -> None:
    with pytest.raises(ValidationError):
        state(mode, plan_version=2)


def test_adaptive_state_can_have_revised_plan() -> None:
    assert state(InvestigationMode.ADAPTIVE, plan_version=3).plan_version == 3


def test_state_bounds_cursor_and_plan_update_marker() -> None:
    two_steps = plan("search_runbooks", "search_runbooks")
    assert state(investigation_plan=two_steps, current_step_index=2).current_step_index == 2
    with pytest.raises(ValidationError):
        state(investigation_plan=two_steps, current_step_index=3)
    with pytest.raises(ValidationError):
        state(records=(executed(1),), plan_decision_at_step=2)


def test_state_bounds_open_questions_and_hypothesis() -> None:
    base = state()
    ok = base.model_copy(update={"open_questions": ("q" * 300,) * 10})
    InvestigationState.model_validate(ok.model_dump())

    for update in (
        {"open_questions": ["q"] * 11},
        {"open_questions": ["q" * 301]},
        {"open_questions": [""]},
        {"working_hypothesis": "h" * 1001},
    ):
        with pytest.raises(ValidationError):
            InvestigationState.model_validate({**base.model_dump(), **update})


def test_state_rejects_unknown_fields_and_empty_case_id() -> None:
    with pytest.raises(ValidationError):
        InvestigationState.model_validate({**state().model_dump(), "step_count": 3})
    with pytest.raises(ValidationError):
        InvestigationState.model_validate({**state().model_dump(), "case_id": ""})


def test_state_round_trips_through_json() -> None:
    original = state(
        records=(executed(1), blocked(2, BlockReason.DUPLICATE)),
        plan_decision_at_step=1,
        plan_version=2,
    )
    restored = InvestigationState.model_validate_json(original.model_dump_json())
    assert restored == original
