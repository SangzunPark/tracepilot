"""Week 4 Fixed Playbook runner.

    build_fixed_playbook(context) -> InvestigationState (plan_version=1)
    -> execute_static_plan (shared with Plan Once):
           repeat: structural termination check
               -> ToolExecutor.attempt(current step) -> record_tool_call
               -> advance current_step_index (blocked steps included)
           -> write termination_reason once
    -> Provider.synthesize() once

Fixed never plans or revises through the provider. ToolExecutionError and
provider failures propagate to the caller, outside InvestigationState; there
are no retries and no failure termination reason.
"""

# Playbook.py에서 생성된 체크리스트를 받아 한 칸(cursor)씩 실행하고 끝나면 결론 보고서 생성
# 이미 만든 많은 함수를 import 하여 조립하는 코드
# 칸 번호(step) 이동과 종료 이유 기록은 plan_execution.py(Plan Once와 공유)가 담당

from pydantic import BaseModel, ConfigDict

from tracepilot.investigation.executor import ToolExecutor
from tracepilot.investigation.models import (
    InitialInvestigationContext,
    InvestigationMode,
    InvestigationState,
)
from tracepilot.investigation.plan_execution import execute_static_plan
from tracepilot.investigation.playbook import build_fixed_playbook
from tracepilot.investigation.provider import (
    IncidentBrief,
    Provider,
    ProviderView,
    SynthesisProposal,
)


# 최종 결과, 실제 InvestigationState 는 frozen 이라 바뀐 새 state를 만들어 교체
class FixedRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: InvestigationState
    synthesis: SynthesisProposal


# MCP, LLM을 고려해 async(await) 사용
async def run_fixed_playbook(
    *,
    case_id: str,
    incident: IncidentBrief,
    context: InitialInvestigationContext,
    executor: ToolExecutor,
    provider: Provider,
) -> FixedRunResult:
    # 계획 준비
    plan = build_fixed_playbook(context)
    # state 준비
    state = InvestigationState(
        case_id=case_id,
        mode=InvestigationMode.FIXED_PLAYBOOK,
        initial_context=context,
        plan=plan,
        plan_version=1,
        current_step_index=0,
    )
    # 실행부터 종료 이유 기록까지 (Plan Once와 공유)
    state = await execute_static_plan(state, executor)
    # 결론 받기
    synthesis = await provider.synthesize(
        ProviderView.from_state(state, incident, executor.max_steps)
    )
    return FixedRunResult(state=state, synthesis=synthesis)
