"""Week 4 global Fixed Playbook: one deterministic plan built only from the
InitialInvestigationContext and the service catalog.

Pure: no MCP, no data access, no provider, no observations. It never varies
by incident category or root cause, and supplies no metric names, log
queries, flag names, or global time window.

Rules:

- scope: focus_services in order, expanded transitively breadth-first through
  catalog dependencies in declared order, keeping each service's first
  occurrence.
- per service type, in scope order:
    application -> metrics, logs, deployment history, feature flag history
    database    -> metrics, logs
    external    -> external service status
- then one unfiltered runbook search (query="").
- schema defaults (e.g. log limit) stay implicit.
- a ToolTimeConstraint sets start_time/end_time only on steps of its tool
  whose service/provider matches (any, when constraint.service is None).
  A runbook constraint, a constraint matching no step, or a step matched by
  more than one constraint is a configuration error.
- the plan is never truncated; the shared step budget bounds execution.
"""

# 이 파일은 Fixed_runner(상사)의 체크리스트를 만드는 역할
# 시작 서비스에서 의존 서비스까지 모은 뒤 정해지 조회를 붙이고 마지막에 runbook 읽기를 추가해
# 고정 체크리스트를 만든다.
# 초기 조건은 맞는 단계에만(예: 특정 시간 조회) 붙이고 이상한 조건은 에러로 막는다.
# 실행은 없고 할 일 목록(InvestigationPlan)만 생성
# 입력: 시작 서비스 목록(예:checkout-api) -> 볼 서비스 목록 만들기(fixed_playbook_scope) ->
# 서비스마다 할 일 붙이기(build_fixed_playbook) -> runbook 1개 추가 ->
# 출력 계획표(InvestigationPlan)

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

from pydantic import JsonValue

from tracepilot.domain.catalog import SERVICE_CATALOG
from tracepilot.investigation.models import (
    InitialInvestigationContext,
    InvestigationPlan,
    PlanStep,
    ToolName,
    ToolRequest,
    ToolTimeConstraint,
)

# mypy용 설명이며 type을 사용해 별칭을 지정한다.
type ServiceCatalog = Mapping[str, Mapping[str, object]]

# 서비스 종류별 사용 tool, 즉 봐야할 목록
TOOLS_BY_SERVICE_TYPE: Final[Mapping[str, tuple[ToolName, ...]]] = {
    "application": (
        "query_service_metrics",
        "search_application_logs",
        "get_deployment_history",
        "get_feature_flag_history",
    ),
    "database": ("query_service_metrics", "search_application_logs"),
    "external": ("get_external_service_status",),
}
# PlanStep 클래스로 RUNBOOK 객체(상수) 생성
RUNBOOK_STEP: Final = PlanStep(
    request=ToolRequest(tool="search_runbooks", arguments={"query": ""}),
    rationale="fixed playbook: all runbooks",
)


class PlaybookConfigurationError(ValueError):
    """The context or catalog cannot produce a valid Fixed Playbook."""


def _entry(catalog: ServiceCatalog, service: str) -> Mapping[str, object]:
    entry = catalog.get(service)
    if entry is None:
        raise PlaybookConfigurationError(f"service not in catalog: {service}")
    return entry


def _dependencies(catalog: ServiceCatalog, service: str) -> tuple[str, ...]:
    dependencies = _entry(catalog, service).get("dependencies", [])
    if not isinstance(dependencies, list | tuple) or not all(
        isinstance(dependency, str) for dependency in dependencies
    ):
        raise PlaybookConfigurationError(f"invalid dependencies for {service}")
    return tuple(dependencies)


def _service_tools(catalog: ServiceCatalog, service: str) -> tuple[ToolName, ...]:
    service_type = _entry(catalog, service).get("type")
    if not isinstance(service_type, str) or service_type not in TOOLS_BY_SERVICE_TYPE:
        raise PlaybookConfigurationError(f"unsupported service type for {service}")
    return TOOLS_BY_SERVICE_TYPE[service_type]


def fixed_playbook_scope(
    focus_services: Sequence[str], catalog: ServiceCatalog = SERVICE_CATALOG
) -> tuple[str, ...]:
    """Focus services plus transitive dependencies, breadth-first, deduplicated
    on first occurrence."""
    scope: list[str] = []
    for service in focus_services:
        if service not in scope:
            _entry(catalog, service)
            scope.append(service)
    index = 0
    while index < len(scope):
        for dependency in _dependencies(catalog, scope[index]):
            if dependency not in scope:
                _entry(catalog, dependency)
                scope.append(dependency)
        index += 1
    return tuple(scope)


def _target_key(tool: ToolName) -> str:
    return "provider" if tool == "get_external_service_status" else "service"


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _matches(constraint: ToolTimeConstraint, tool: ToolName, target: str) -> bool:
    return constraint.tool == tool and constraint.service in (None, target)


def build_fixed_playbook(
    context: InitialInvestigationContext, catalog: ServiceCatalog = SERVICE_CATALOG
) -> InvestigationPlan:
    """The global Fixed Playbook for this context. Deterministic and pure."""
    for constraint in context.tool_constraints:
        if constraint.tool == "search_runbooks":
            raise PlaybookConfigurationError("search_runbooks takes no time constraint")

    used: set[int] = set()
    steps: list[PlanStep] = []
    for service in fixed_playbook_scope(context.focus_services, catalog):
        for tool in _service_tools(catalog, service):
            arguments: dict[str, JsonValue] = {_target_key(tool): service}
            matching = [
                index
                for index, constraint in enumerate(context.tool_constraints)
                if _matches(constraint, tool, service)
            ]
            if len(matching) > 1:
                raise PlaybookConfigurationError(
                    f"more than one time constraint matches {tool} for {service}"
                )
            if matching:
                constraint = context.tool_constraints[matching[0]]
                arguments["start_time"] = _utc_text(constraint.start_time)
                arguments["end_time"] = _utc_text(constraint.end_time)
                used.update(matching)
            steps.append(
                PlanStep(
                    request=ToolRequest(tool=tool, arguments=arguments),
                    rationale=f"fixed playbook: {tool} for {service}",
                )
            )
    steps.append(RUNBOOK_STEP)

    unused = [
        constraint.tool
        for index, constraint in enumerate(context.tool_constraints)
        if index not in used
    ]
    if unused:
        raise PlaybookConfigurationError(f"time constraints match no playbook step: {unused}")
    return InvestigationPlan(steps=tuple(steps))
