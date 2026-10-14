"""Week 4 Adaptive runner.

    pre-planning InvestigationState (plan=None, plan_version=0)
    -> Provider.propose_initial_plan() once, installed as returned (version 1)
    -> loop, in this order on every iteration:
         A. structural termination      -> record the reason once, stop
         B. pending plan review         -> Provider.review_plan() once,
                                           apply KEEP or REPLACE, record it
         C. concrete action             -> ToolExecutor.attempt(current step),
                                           record it, advance the cursor
    -> Provider.synthesize() once

A plan review is pending only after a fresh executed Observation (an executed
MCP error counts; a blocked attempt does not). KEEP and REPLACE both set
plan_decision_at_step = step_count, so a review can never trigger another
review: after a review the next event is an action or structural termination.
Every action attempt, blocked or not, consumes one agent step; provider calls
consume none; the shared step budget therefore bounds the loop.

Replacement plans hold future actions only and are installed exactly as
returned; the shared policy/executor boundary handles every action, and
duplicate detection spans all plan versions through state.tool_calls.
Provider failures and ToolExecutionError propagate to the caller, outside
InvestigationState; there are no retries and no failure termination reason.
"""

# 빈 state를 만들고 LMM에게 첫 계획을 한 번 받아 넣고 조회를 하나 실행할 때 마다
# 계획을 유지하면서 가설과 질문만 고치는 KEEP / 계획을 버리고 새 계획으로 바꾸는
# REPLACE 중 선택한다. 예산 15회를 다 쓰면 최종 LLM 결론 보고서 생성
# review가 일어날 때마다 무엇이 무엇으로 바뀌었는지 기록으로 남긴다.

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tracepilot.investigation.executor import ToolExecutor, record_tool_call
from tracepilot.investigation.models import (
    InitialInvestigationContext,
    InvestigationMode,
    InvestigationPlan,
    InvestigationState,
    OpenQuestion,
)
from tracepilot.investigation.plan_execution import advance_cursor, record_termination
from tracepilot.investigation.policies import plan_review_pending, structural_termination
from tracepilot.investigation.provider import (
    IncidentBrief,
    KeepPlanDecision,
    PlanReviewDecision,
    Provider,
    ProviderView,
    RevisionProposal,
    SynthesisProposal,
)

# 별칭 지정
type PlanReviewTrigger = Literal["mid_plan", "plan_exhausted"]


# review 한 번에 대한 기록지, 클래스 + 자기검사
class PlanDecisionRecord(BaseModel):
    """One Adaptive plan-review decision, with the values before and after
    it. Append-only history; not part of InvestigationState or ProviderView."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 몇 번째 실행 뒤의 review인가
    after_step: int = Field(ge=1)
    # 계획 중간이었나, 끝이었나
    trigger: PlanReviewTrigger
    # keep 또는 replace
    decision: Literal["keep", "replace"]
    # 계획 버전 전,후
    previous_plan_version: int = Field(ge=1)
    new_plan_version: int = Field(ge=1)
    previous_plan: InvestigationPlan
    previous_cursor: int = Field(ge=0)
    # 새 계획
    replacement_plan: InvestigationPlan | None
    # 가설 전,후
    previous_working_hypothesis: str | None
    new_working_hypothesis: str | None
    # 미해결 질문 전,후
    previous_open_questions: tuple[OpenQuestion, ...]
    new_open_questions: tuple[OpenQuestion, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.decision == "keep":
            if self.replacement_plan is not None:
                raise ValueError("a KEEP decision has no replacement plan")
            if self.new_plan_version != self.previous_plan_version:
                raise ValueError("a KEEP decision does not change the plan version")
        else:
            if self.replacement_plan is None:
                raise ValueError("a REPLACE decision requires a replacement plan")
            if self.new_plan_version != self.previous_plan_version + 1:
                raise ValueError("a REPLACE decision increments the plan version by one")
        if self.trigger == "mid_plan" and self.previous_cursor >= len(self.previous_plan.steps):
            raise ValueError("mid_plan requires unconsumed steps in the previous plan")
        if self.trigger == "plan_exhausted" and self.previous_cursor < len(
            self.previous_plan.steps
        ):
            raise ValueError("plan_exhausted requires the previous plan to be consumed")
        return self


# 최종 결과
class AdaptiveRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: InvestigationState
    synthesis: SynthesisProposal
    initial_plan: InvestigationPlan
    decisions: tuple[PlanDecisionRecord, ...]


# 반복문 뒤에는 계획이 반드시 있어야 한다는 확인
def _current_plan(state: InvestigationState) -> InvestigationPlan:
    if state.plan is None:
        raise RuntimeError("the Adaptive loop requires an installed plan")
    return state.plan


# KEEP/REPLACE를 state에 반영
def _apply_review(
    state: InvestigationState, decision: PlanReviewDecision
) -> tuple[InvestigationState, PlanDecisionRecord]:
    """Apply one KEEP or REPLACE decision and describe it. Both consume the
    pending review by setting plan_decision_at_step = step_count."""
    if not isinstance(decision, KeepPlanDecision | RevisionProposal):
        raise TypeError(f"review_plan returned {type(decision).__name__}, not KEEP or REPLACE")
    plan = _current_plan(state)
    trigger: PlanReviewTrigger = (
        "mid_plan" if state.current_step_index < len(plan.steps) else "plan_exhausted"
    )
    common = {
        "working_hypothesis": decision.working_hypothesis,
        "open_questions": decision.open_questions,
        "plan_decision_at_step": state.step_count,
    }
    # isinstance, 특정 개체가 어떤 클래스 또는 데이터타입의 인스턴스인지 확인할때 사용
    if isinstance(decision, KeepPlanDecision):
        replacement: InvestigationPlan | None = None
        updated = InvestigationState(**{**dict(state), **common})
    else:
        replacement = decision.plan
        updated = InvestigationState(
            **{
                **dict(state),
                **common,
                "plan": decision.plan,
                "plan_version": state.plan_version + 1,
                "current_step_index": 0,
            }
        )

    record = PlanDecisionRecord(
        after_step=state.step_count,
        trigger=trigger,
        decision=decision.decision,
        previous_plan_version=state.plan_version,
        new_plan_version=updated.plan_version,
        previous_plan=plan,
        previous_cursor=state.current_step_index,
        replacement_plan=replacement,
        previous_working_hypothesis=state.working_hypothesis,
        new_working_hypothesis=updated.working_hypothesis,
        previous_open_questions=state.open_questions,
        new_open_questions=updated.open_questions,
    )
    return updated, record


# run_adaptive의 본체
# 준비는 plan_once와 동일
async def run_adaptive(
    *,
    case_id: str,
    incident: IncidentBrief,
    context: InitialInvestigationContext,
    executor: ToolExecutor,
    provider: Provider,
) -> AdaptiveRunResult:
    state = InvestigationState(
        case_id=case_id,
        mode=InvestigationMode.ADAPTIVE,
        initial_context=context,
        plan=None,
        plan_version=0,
        current_step_index=0,
        plan_decision_at_step=0,
    )

    initial_plan = await provider.propose_initial_plan(
        ProviderView.from_state(state, incident, executor.max_steps)
    )
    state = InvestigationState(**{**dict(state), "plan": initial_plan, "plan_version": 1})

    decisions: list[PlanDecisionRecord] = []
    while True:
        # A. structural termination
        # 끝났는지 확인
        reason = structural_termination(state, executor.max_steps)
        if reason is not None:
            state = record_termination(state, reason)
            break
        # B. pending plan review for a fresh executed Observation
        # review안한 새 관찰이 있나?
        if plan_review_pending(state):
            decision = await provider.review_plan(
                ProviderView.from_state(state, incident, executor.max_steps)
            )
            state, decision_record = _apply_review(state, decision)
            decisions.append(decision_record)
            continue
        # C. concrete action from the current plan
        # 현재 계획 다음 칸 실행
        step = _current_plan(state).steps[state.current_step_index]
        record = await executor.attempt(state, step.request)
        state = advance_cursor(record_tool_call(state, record))

    synthesis = await provider.synthesize(
        ProviderView.from_state(state, incident, executor.max_steps)
    )
    return AdaptiveRunResult(
        state=state,
        synthesis=synthesis,
        initial_plan=initial_plan,
        decisions=tuple(decisions),
    )
