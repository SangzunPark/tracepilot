"""Week 4 investigation data contracts.

Pure data: no I/O, no MCP, no provider, no execution loop. The provider-facing
tool argument models mirror the live MCP tool schemas minus the runtime-owned
case_id; tests/investigation/test_tool_spec_drift.py keeps them aligned.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Self, get_args

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from tracepilot.domain.catalog import SERVICE_CATALOG

# Provisional engineering bounds (human-approved for Week 4).
MAX_OBSERVATION_RECORDS: Final = 50
MAX_ERROR_MESSAGE_CHARS: Final = 500
MAX_OPEN_QUESTIONS: Final = 10
MAX_OPEN_QUESTION_CHARS: Final = 300
MAX_WORKING_HYPOTHESIS_CHARS: Final = 1000
MAX_RATIONALE_CHARS: Final = 300
MAX_BLOCK_DETAIL_CHARS: Final = 300


class InvestigationMode(StrEnum):
    FIXED_PLAYBOOK = "fixed_playbook"
    PLAN_ONCE = "plan_once"
    ADAPTIVE = "adaptive"


class TerminationReason(StrEnum):
    """Structural termination only. There is no provider "conclude" authority;
    completion semantics belong to the later completion evaluator."""

    PLAN_EXHAUSTED = "plan_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"


class BlockReason(StrEnum):
    """Why policy blocked an action attempt. Budget exhaustion is structural
    termination, not a block reason."""

    UNKNOWN_TOOL = "unknown_tool"
    FORBIDDEN_ARGUMENT = "forbidden_argument"
    INVALID_ARGUMENTS = "invalid_arguments"
    DUPLICATE = "duplicate"


ToolName = Literal[
    "query_service_metrics",
    "search_application_logs",
    "get_deployment_history",
    "get_feature_flag_history",
    "get_external_service_status",
    "search_runbooks",
]
TOOL_NAMES: Final[tuple[ToolName, ...]] = get_args(ToolName)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Initial investigation context
# ---------------------------------------------------------------------------


class ToolTimeConstraint(_FrozenModel):
    """A time range for one tool's initial search. Input to initial planning or
    playbook construction, not a permanent restriction on later calls."""

    tool: ToolName
    service: str | None = None
    start_time: AwareDatetime
    end_time: AwareDatetime

    @model_validator(mode="after")
    def _ordered_range(self) -> Self:
        if self.start_time > self.end_time:
            raise ValueError("start_time must not be after end_time")
        return self


class InitialInvestigationContext(_FrozenModel):
    """Shared starting information, identical across the three modes for the
    same case. Deliberately has no case_id, category, root cause, or global
    time window."""

    focus_services: tuple[str, ...] = Field(min_length=1)
    initial_question: str = Field(min_length=1)
    tool_constraints: tuple[ToolTimeConstraint, ...] = ()

    @field_validator("focus_services")
    @classmethod
    def _known_unique_services(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("focus_services must not contain duplicates")
        unknown = [service for service in value if service not in SERVICE_CATALOG]
        if unknown:
            raise ValueError(f"focus_services not in SERVICE_CATALOG: {unknown}")
        return value

    @field_validator("initial_question")
    @classmethod
    def _non_blank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("initial_question must not be blank")
        return value


# ---------------------------------------------------------------------------
# Provider proposals and plans
# ---------------------------------------------------------------------------


class ToolRequest(_FrozenModel):
    """A raw provider proposal. `tool` stays a plain str and `arguments` may
    contain anything JSON-shaped (including case_id), so that deterministic
    policy, not model construction, blocks and counts bad proposals."""

    tool: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class PlanStep(_FrozenModel):
    request: ToolRequest
    rationale: str | None = Field(default=None, max_length=MAX_RATIONALE_CHARS)


class InvestigationPlan(_FrozenModel):
    """An ordered plan. May be empty. Its version lives in InvestigationState."""

    steps: tuple[PlanStep, ...] = ()


# ---------------------------------------------------------------------------
# Provider-facing tool argument models (case_id excluded: runtime-owned)
# ---------------------------------------------------------------------------


def _as_utc(value: datetime) -> datetime:
    # Same rule as DiagnosticService: a naive datetime means UTC.
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]


class ToolArguments(_FrozenModel):
    """Schema-level validation only, in Pydantic's default (lax) mode to match
    the MCP server. Domain checks (unknown service, inverted range, limit < 1)
    are intentionally left to the diagnostic service."""


class QueryServiceMetricsArguments(ToolArguments):
    service: str
    metric: str | None = None
    start_time: UtcDatetime | None = None
    end_time: UtcDatetime | None = None


class SearchApplicationLogsArguments(ToolArguments):
    service: str
    query: str | None = None
    start_time: UtcDatetime | None = None
    end_time: UtcDatetime | None = None
    limit: int = 50


class GetDeploymentHistoryArguments(ToolArguments):
    service: str
    start_time: UtcDatetime | None = None
    end_time: UtcDatetime | None = None


class GetFeatureFlagHistoryArguments(ToolArguments):
    service: str
    flag_name: str | None = None
    start_time: UtcDatetime | None = None
    end_time: UtcDatetime | None = None


class GetExternalServiceStatusArguments(ToolArguments):
    provider: str
    start_time: UtcDatetime | None = None
    end_time: UtcDatetime | None = None


class SearchRunbooksArguments(ToolArguments):
    query: str
    service: str | None = None
    limit: int = 20


TOOL_ARGUMENT_MODELS: Final[Mapping[ToolName, type[ToolArguments]]] = {
    "query_service_metrics": QueryServiceMetricsArguments,
    "search_application_logs": SearchApplicationLogsArguments,
    "get_deployment_history": GetDeploymentHistoryArguments,
    "get_feature_flag_history": GetFeatureFlagHistoryArguments,
    "get_external_service_status": GetExternalServiceStatusArguments,
    "search_runbooks": SearchRunbooksArguments,
}


# ---------------------------------------------------------------------------
# Observations and tool-call records
# ---------------------------------------------------------------------------


class Observation(_FrozenModel):
    """Compact, bounded result of one executed tool call. Not the Week 5
    Evidence model."""

    is_error: bool
    error_message: str | None = Field(default=None, max_length=MAX_ERROR_MESSAGE_CHARS)
    record_count: int = Field(ge=0)
    records: tuple[dict[str, JsonValue], ...] = Field(
        default=(), max_length=MAX_OBSERVATION_RECORDS
    )
    truncated: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.is_error:
            if self.error_message is None:
                raise ValueError("an error observation requires error_message")
            if self.record_count or self.records or self.truncated:
                raise ValueError("an error observation carries no records")
        elif self.error_message is not None:
            raise ValueError("error_message is only allowed on an error observation")
        if len(self.records) > self.record_count:
            raise ValueError("records exceed record_count")
        if self.truncated != (self.record_count > len(self.records)):
            raise ValueError("truncated must be true exactly when records were dropped")
        return self

    @classmethod
    def from_records(cls, records: Sequence[Mapping[str, JsonValue]]) -> Self:
        retained = tuple(dict(record) for record in records[:MAX_OBSERVATION_RECORDS])
        return cls(
            is_error=False,
            record_count=len(records),
            records=retained,
            truncated=len(retained) < len(records),
        )

    @classmethod
    def from_error(cls, message: str) -> Self:
        return cls(
            is_error=True,
            error_message=message[:MAX_ERROR_MESSAGE_CHARS],
            record_count=0,
        )


class ToolCallRecord(_FrozenModel):
    """One agent step: a single control-policy action attempt.

    Raw arguments of blocked proposals are not stored. Normalized arguments
    and the fingerprint are present for permitted calls and duplicates only.
    """

    step: int = Field(ge=1)
    tool: str
    normalized_arguments: dict[str, JsonValue] | None = None
    fingerprint: str | None = None
    permitted: bool
    executed: bool = False
    block_reason: BlockReason | None = None
    block_detail: str | None = Field(default=None, max_length=MAX_BLOCK_DETAIL_CHARS)
    observation: Observation | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.permitted == (self.block_reason is not None):
            raise ValueError("permitted must hold exactly when there is no block_reason")
        if self.executed and not self.permitted:
            raise ValueError("a blocked attempt cannot be executed")
        if self.executed != (self.observation is not None):
            raise ValueError("an observation must exist exactly when execution occurred")
        if self.permitted != (self.block_detail is None):
            raise ValueError("block_detail must be present exactly when blocked")
        if (self.normalized_arguments is None) != (self.fingerprint is None):
            raise ValueError("normalized_arguments and fingerprint go together")
        carries_call = self.permitted or self.block_reason is BlockReason.DUPLICATE
        if carries_call != (self.fingerprint is not None):
            raise ValueError(
                "normalized_arguments/fingerprint are present exactly for permitted "
                "calls and duplicates"
            )
        if carries_call and self.tool not in TOOL_NAMES:
            raise ValueError("a normalized call must name an approved tool")
        return self


# ---------------------------------------------------------------------------
# Investigation state
# ---------------------------------------------------------------------------

OpenQuestion = Annotated[str, Field(min_length=1, max_length=MAX_OPEN_QUESTION_CHARS)]


class InvestigationState(_FrozenModel):
    """Compact Week 4 interim state. Counters are derived from tool_calls so
    they cannot drift.

    plan_decision_at_step is the latest agent step whose fresh executed
    Observation has already been consumed by an Adaptive plan-review decision
    (KEEP or REPLACE). It stays 0 for Fixed Playbook and Plan Once."""

    case_id: str = Field(min_length=1)
    mode: InvestigationMode
    initial_context: InitialInvestigationContext
    plan: InvestigationPlan | None = None
    plan_version: int = Field(default=0, ge=0)
    current_step_index: int = Field(default=0, ge=0)
    plan_decision_at_step: int = Field(default=0, ge=0)
    tool_calls: tuple[ToolCallRecord, ...] = ()
    open_questions: tuple[OpenQuestion, ...] = Field(default=(), max_length=MAX_OPEN_QUESTIONS)
    working_hypothesis: str | None = Field(default=None, max_length=MAX_WORKING_HYPOTHESIS_CHARS)
    termination_reason: TerminationReason | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        steps = [record.step for record in self.tool_calls]
        if steps != list(range(1, len(steps) + 1)):
            raise ValueError("tool_calls steps must be 1, 2, ... in order")
        if (self.plan is None) != (self.plan_version == 0):
            raise ValueError("plan_version is 0 exactly when there is no plan")
        if self.mode is not InvestigationMode.ADAPTIVE and self.plan_version > 1:
            raise ValueError(f"{self.mode} does not revise its plan")
        if self.mode is not InvestigationMode.ADAPTIVE and self.plan_decision_at_step != 0:
            raise ValueError(f"{self.mode} makes no plan-review decisions")
        plan_length = len(self.plan.steps) if self.plan is not None else 0
        if self.current_step_index > plan_length:
            raise ValueError("current_step_index is past the end of the plan")
        if self.plan_decision_at_step > len(self.tool_calls):
            raise ValueError("plan_decision_at_step is past the last agent step")
        return self

    @property
    def step_count(self) -> int:
        """Agent steps: every action attempt, permitted or blocked."""
        return len(self.tool_calls)

    @property
    def logical_tool_calls(self) -> int:
        """Policy-permitted actions that reached execution."""
        return sum(1 for record in self.tool_calls if record.executed)

    @property
    def execution_attempts(self) -> int:
        """Equal to logical_tool_calls in Week 4: there are no retries yet."""
        return sum(1 for record in self.tool_calls if record.executed)
