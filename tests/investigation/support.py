"""Builders shared by the investigation model and policy tests."""

from datetime import UTC, datetime

from pydantic import JsonValue

from tracepilot.investigation.models import (
    BlockReason,
    InitialInvestigationContext,
    InvestigationMode,
    InvestigationPlan,
    InvestigationState,
    Observation,
    PlanStep,
    ToolCallRecord,
    ToolRequest,
    ToolTimeConstraint,
)

FINGERPRINT = "f" * 64


def inc013_context() -> InitialInvestigationContext:
    """INC-013 style: a time range scoped to the initial deployment-history
    search only."""
    return InitialInvestigationContext(
        focus_services=("checkout-api",),
        initial_question="What explains the checkout-api errors?",
        tool_constraints=(
            ToolTimeConstraint(
                tool="get_deployment_history",
                start_time=datetime(2026, 4, 6, 13, 50, tzinfo=UTC),
                end_time=datetime(2026, 4, 6, 14, 15, tzinfo=UTC),
            ),
        ),
    )


def inc015_context() -> InitialInvestigationContext:
    """INC-015 style: focus services and a question, no time constraint."""
    return InitialInvestigationContext(
        focus_services=("billing-api",),
        initial_question="What explains the billing-api slowdown and invoice failures?",
    )


def plan(*tools: str) -> InvestigationPlan:
    return InvestigationPlan(
        steps=tuple(
            PlanStep(request=ToolRequest(tool=tool, arguments={"service": "checkout-api"}))
            for tool in tools
        )
    )


def executed(step: int, *, error: bool = False, fingerprint: str = FINGERPRINT) -> ToolCallRecord:
    observation = (
        Observation.from_error("Error executing tool: boom")
        if error
        else Observation.from_records(({"source_id": f"log-{step}"},))
    )
    return ToolCallRecord(
        step=step,
        tool="search_application_logs",
        normalized_arguments={"service": "checkout-api"},
        fingerprint=fingerprint,
        permitted=True,
        executed=True,
        observation=observation,
    )


def blocked(
    step: int, reason: BlockReason = BlockReason.INVALID_ARGUMENTS, tool: str = "search_runbooks"
) -> ToolCallRecord:
    carries_call = reason is BlockReason.DUPLICATE
    arguments: dict[str, JsonValue] | None = {"query": "pool"} if carries_call else None
    return ToolCallRecord(
        step=step,
        tool=tool,
        normalized_arguments=arguments,
        fingerprint=FINGERPRINT if carries_call else None,
        permitted=False,
        block_reason=reason,
        block_detail="blocked by policy",
    )


def state(
    mode: InvestigationMode = InvestigationMode.ADAPTIVE,
    *,
    records: tuple[ToolCallRecord, ...] = (),
    plan_decision_at_step: int = 0,
    plan_version: int = 1,
    investigation_plan: InvestigationPlan | None = None,
    current_step_index: int = 0,
) -> InvestigationState:
    if investigation_plan is None and plan_version > 0:
        investigation_plan = plan("search_application_logs", "get_deployment_history")
    return InvestigationState(
        case_id="INC-001",
        mode=mode,
        initial_context=inc015_context(),
        plan=investigation_plan,
        plan_version=plan_version,
        current_step_index=current_step_index,
        plan_decision_at_step=plan_decision_at_step,
        tool_calls=records,
    )
