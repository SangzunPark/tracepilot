"""Week 4 shared static-plan execution for Fixed Playbook and Plan Once.

Contract-critical behavior both modes must share exactly, so the controlled
comparison differs only in how the plan is obtained:

    while there is no structural termination:
        attempt the current plan step -> record it -> advance the cursor
        (blocked attempts are recorded and advance the cursor too)
    record the structural termination reason exactly once

It never acquires, modifies, or revises a plan, calls a provider,
synthesizes, retries, or handles completion, persistence, or approval. It is
not the Adaptive loop: Adaptive requires plan-review routing and is rejected.
"""

from tracepilot.investigation.executor import ToolExecutor, record_tool_call
from tracepilot.investigation.models import (
    InvestigationMode,
    InvestigationState,
    TerminationReason,
)
from tracepilot.investigation.policies import structural_termination


# step 변경 부분
def advance_cursor(state: InvestigationState) -> InvestigationState:
    """A new state with current_step_index advanced by one (re-validated)."""
    return InvestigationState(**{**dict(state), "current_step_index": state.current_step_index + 1})


def record_termination(state: InvestigationState, reason: TerminationReason) -> InvestigationState:
    """A new state with the termination reason recorded. Recording is final."""
    if state.termination_reason is not None:
        raise RuntimeError("termination_reason is already recorded")
    return InvestigationState(**{**dict(state), "termination_reason": reason})


async def execute_static_plan(
    state: InvestigationState, executor: ToolExecutor
) -> InvestigationState:
    """Run the state's existing plan to structural termination and return the
    terminated state. ToolExecutionError propagates; nothing is retried."""
    if state.mode is InvestigationMode.ADAPTIVE:
        raise ValueError("execute_static_plan is for Fixed Playbook and Plan Once only")
    plan = state.plan
    if plan is None:
        raise ValueError("execute_static_plan requires a plan")
    if state.termination_reason is not None:
        raise RuntimeError("termination_reason is already recorded")

    # 종료 확인
    while (reason := structural_termination(state, executor.max_steps)) is None:
        # 현재 칸 꺼내기
        step = plan.steps[state.current_step_index]
        # 실행
        record = await executor.attempt(state, step.request)
        # 결과 기록 + 칸 번호 1 증가
        state = advance_cursor(record_tool_call(state, record))
    # 종료 이유 적기
    return record_termination(state, reason)
