"""Week 4 Plan Once runner.

    pre-planning InvestigationState (plan=None, plan_version=0)
    -> Provider.propose_initial_plan() once, plan used exactly as returned
    -> install the plan (plan_version=1, current_step_index=0)
    -> execute_static_plan (shared with Fixed Playbook)
    -> Provider.synthesize() once

The plan never changes after installation: no revision, no re-planning, and
no runner-side sanitizing, reordering, or truncation. Invalid, forbidden, and
duplicate steps are handled by the shared policy/executor boundary.
Provider failures and ToolExecutionError propagate to the caller, outside
InvestigationState; there are no retries and no failure termination reason.
"""

# LLM에게 한 번 계획을 받고 그 계획을 공용 실행 함수에 넘기는 파일
# 빈 state를(무계획) 만들고 -> LLM에게 계획을 받고 -> state에 계획을 넣고
# 공용 함수(execute_static_plan)로 실행 -> LLM에게 결론 받기

from pydantic import BaseModel, ConfigDict

from tracepilot.investigation.executor import ToolExecutor
from tracepilot.investigation.models import (
    InitialInvestigationContext,
    InvestigationMode,
    InvestigationPlan,
    InvestigationState,
)
from tracepilot.investigation.plan_execution import execute_static_plan
from tracepilot.investigation.provider import (
    IncidentBrief,
    Provider,
    ProviderView,
    SynthesisProposal,
)


class PlanOnceRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: InvestigationState
    synthesis: SynthesisProposal


def _install_plan(state: InvestigationState, plan: InvestigationPlan) -> InvestigationState:
    return InvestigationState(**{**dict(state), "plan": plan, "plan_version": 1})


async def run_plan_once(
    *,
    case_id: str,
    incident: IncidentBrief,
    context: InitialInvestigationContext,
    executor: ToolExecutor,
    provider: Provider,
) -> PlanOnceRunResult:
    # 빈 state 만들기
    state = InvestigationState(
        case_id=case_id,
        mode=InvestigationMode.PLAN_ONCE,
        initial_context=context,
        plan=None,
        plan_version=0,
        current_step_index=0,
    )

    # LLM에게 계획 받기
    plan = await provider.propose_initial_plan(
        ProviderView.from_state(state, incident, executor.max_steps)
    )
    # 계획 넣기
    state = _install_plan(state, plan)

    # Fixed와 동일한 반복문 실행
    state = await execute_static_plan(state, executor)

    synthesis = await provider.synthesize(
        ProviderView.from_state(state, incident, executor.max_steps)
    )
    return PlanOnceRunResult(state=state, synthesis=synthesis)
