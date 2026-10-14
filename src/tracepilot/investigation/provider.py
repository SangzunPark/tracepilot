"""Week 4 provider contract: the interface between LLM reasoning and the
investigation runtime.

The provider proposes; the runtime decides. Providers see only a ProviderView
built from runtime-visible investigation state plus an IncidentBrief the
runner supplies: no case_id, no repository, no evaluation data. Concrete
actions always come from the current plan, so there is no next-action
operation. Synthesis is a proposal, not a completion decision; deterministic
completion evaluation comes later (Week 5). Provider failures are raised to
the caller and stay outside InvestigationState in Week 4.

No provider SDK is used here. Real providers are a later, explicit decision.
"""
# 이 파일은 LLM과 tracepilot이 주고 받는 서류양식과 규칙을 정해둔 파일
# LLM이 받는 서류는 IncidentBrief / ProviderView / from_state
# LLM이 제출하는 서류는 InvestigationPlan / RevisionProposal / SyntehsisProposal
# 위 3개의 파일이 있는 경우에만 LLM 인정
# 즉 LLM이 볼수 있는 것과 낼 수 있는것을 제한하며 LLM 컴포넌트를 테스트로 교체할 수 있다

from typing import Annotated, Literal, Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from tracepilot.investigation.models import (
    MAX_OPEN_QUESTIONS,
    MAX_WORKING_HYPOTHESIS_CHARS,
    TOOL_NAMES,
    InitialInvestigationContext,
    InvestigationPlan,
    InvestigationState,
    OpenQuestion,
    TerminationReason,
    ToolCallRecord,
    ToolName,
)
from tracepilot.investigation.policies import MAX_AGENT_STEPS, remaining_steps

# LLM 과잉 텍스트 생산을 통제
# Provisional engineering bounds for synthesis proposals.
MAX_ROOT_CAUSE_CLAIM_CHARS = 500
MAX_EXPLANATION_CHARS = 1000
MAX_CITED_EVIDENCE_IDS = 20
MAX_EVIDENCE_ID_CHARS = 100


# Incident 개요문서
# BaseModel을 적용하면 Pydantic이 이름과 타입만으로 자동으로 검사
class IncidentBrief(BaseModel):
    """Provider-visible incident facts. Deliberately omits case_id, the
    manifest title, and anything from evaluation data. The runner builds it
    from an already-loaded manifest; this module has no domain-model access."""

    # extra="forbid" 는 정의 안된 간이 들어오면 에러는 내는 옵션
    model_config = ConfigDict(frozen=True, extra="forbid")

    started_at: AwareDatetime
    affected_services: tuple[str, ...] = Field(min_length=1)
    symptom: str = Field(min_length=1)


# LLM이 볼 수 있는 자료 목록 묶음
class ProviderView(BaseModel):
    """What a provider may see: runtime-visible investigation information
    only. Deliberately omits case_id (the runtime owns case identity) and the
    investigation mode (modes differ only in control policy, so a provider must
    not condition planning or synthesis on the experimental condition)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 조사 조건
    incident: IncidentBrief
    initial_context: InitialInvestigationContext
    available_tools: tuple[ToolName, ...]
    # 계획 상황
    plan: InvestigationPlan | None
    plan_version: int
    current_step_index: int
    # 지금까지 알아낸 것
    tool_calls: tuple[ToolCallRecord, ...]
    open_questions: tuple[OpenQuestion, ...]
    working_hypothesis: str | None
    # 남은 steps
    remaining_steps: int
    termination_reason: TerminationReason | None

    # 이 함수가 없다면 runner는 plan once는 두번, adaptive는 여러 번 이 12항목을 생성해야하는데
    # 이 과정에서 실수로 인해 mode 비교의 일관성이 떨어질 수 있다.
    # 브리핑 자료를 새로 만드는 함수이며 classmethod는 아직 객체가 없는 상태에서
    # 정보가 든 객체를 만들어야 할때 쓰인다.
    # 일반적으로 class 안에 함수가 있을때 두 종류가 있는데 하나가 일반 메서드(self) 이며
    # 다른 하나가 classmethod를 쓰는 방식(객체가 없을때)이다.
    # cls는 함수가 속한 클래스를 cls 자리에 대입해 주는 classmethod 방식
    # 이 함수를 클래스 안에 두는 이유는 함수르 호출할때 자연스럽게
    # ProviderView.from_state(state, incident)라고 입력하기 때문에
    # 구분 또는 확인이 용이하기 때문이다.

    # 쉽게 말해 cls는 providerView 클래스를 나타내며 @classmethod를 사용하면
    # 점(.) 앞에 객체가 아닌 클래스가 오게 된다.
    # 예 ProviderView.from_state

    @classmethod
    def from_state(
        cls,
        state: InvestigationState,
        incident: IncidentBrief,
        max_steps: int = MAX_AGENT_STEPS,
    ) -> Self:
        """The runner supplies `incident`; this module has no data access."""
        return cls(
            incident=incident,
            initial_context=state.initial_context,
            available_tools=TOOL_NAMES,
            plan=state.plan,
            plan_version=state.plan_version,
            current_step_index=state.current_step_index,
            tool_calls=state.tool_calls,
            open_questions=state.open_questions,
            working_hypothesis=state.working_hypothesis,
            remaining_steps=remaining_steps(state.step_count, max_steps),
            termination_reason=state.termination_reason,
        )


# 조사 계획 수정 시 제안 내용, 새 계획 / 새 가설 / 새 미해결 질문 목록
# Adaptive의 핵심
class KeepPlanDecision(BaseModel):
    """Adaptive plan-review KEEP: the current plan, plan_version, and cursor
    stay unchanged; working_hypothesis and open_questions are replaced by
    these values. Carries no plan. Valid even when the current plan is
    exhausted (the investigation then ends as PLAN_EXHAUSTED)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Literal["keep"] = "keep"
    working_hypothesis: str | None = Field(max_length=MAX_WORKING_HYPOTHESIS_CHARS)
    open_questions: tuple[OpenQuestion, ...] = Field(max_length=MAX_OPEN_QUESTIONS)


class RevisionProposal(BaseModel):
    """Adaptive plan-review REPLACE: complete replacement values for the plan,
    the working hypothesis, and the open questions.

    `plan` contains FUTURE actions only, executed from its first step
    (current_step_index = 0). Already attempted actions remain in tool_calls,
    are not part of the replacement plan, and should not be re-emitted: a
    repeated call is blocked as a duplicate and still consumes an agent step.
    An empty plan is valid. The runtime does not sanitize, reorder, truncate,
    or rewrite it; deterministic policy handles every action. No confidence
    or completion field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Literal["replace"] = "replace"
    plan: InvestigationPlan
    working_hypothesis: str | None = Field(max_length=MAX_WORKING_HYPOTHESIS_CHARS)
    open_questions: tuple[OpenQuestion, ...] = Field(max_length=MAX_OPEN_QUESTIONS)


# The result of one Adaptive plan review: KEEP or REPLACE, by `decision`.
PlanReviewDecision = Annotated[KeepPlanDecision | RevisionProposal, Field(discriminator="decision")]


# 증거 ID 타입
EvidenceId = Annotated[str, Field(min_length=1, max_length=MAX_EVIDENCE_ID_CHARS)]


# 조사 종료 후 Runner(상사)가 LLM에게 받게 되는 결론 보고서
# 원인 / 근거로 든 증거 ID / 이유
class SynthesisProposal(BaseModel):
    """The provider's proposed diagnosis. Not a completion decision: it
    carries no completed/sufficient/pass-fail field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root_cause_claim: str = Field(min_length=1, max_length=MAX_ROOT_CAUSE_CLAIM_CHARS)
    cited_evidence_ids: tuple[EvidenceId, ...] = Field(max_length=MAX_CITED_EVIDENCE_IDS)
    explanation: str = Field(min_length=1, max_length=MAX_EXPLANATION_CHARS)

    @model_validator(mode="after")
    def _unique_citations(self) -> Self:
        if len(set(self.cited_evidence_ids)) != len(self.cited_evidence_ids):
            raise ValueError("cited_evidence_ids must not contain duplicates")
        return self


# LLM 자격 규격, protocol이며 아래 3개의 async 함수를 갖고 있으면 runner가 Provider로 인정
class Provider(Protocol):
    """Async provider operations. None of them consumes the step budget."""

    async def propose_initial_plan(self, view: ProviderView) -> InvestigationPlan:
        """The single initial plan (Plan Once, Adaptive). Fixed Playbook does
        not call the provider for planning."""
        ...

    async def review_plan(self, view: ProviderView) -> PlanReviewDecision:
        """Adaptive only. Called exactly once for every fresh executed
        Observation while budget remains, whether or not the current plan has
        steps left. Returns KEEP or REPLACE; it has no authority over
        execution or termination."""
        ...

    async def synthesize(self, view: ProviderView) -> SynthesisProposal:
        """The shared final synthesis after structural termination."""
        ...
