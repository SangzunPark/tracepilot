"""Week 4 shared LangGraph runtime: a shallow dispatch graph.

    START -> route by InvestigationMode -> fixed_playbook | plan_once | adaptive -> END

LangGraph is the application-level runtime boundary: the public entry point,
deterministic mode routing, and the invocation lifecycle. The existing mode
runners remain the only semantic execution kernels; each mode node awaits its
runner unchanged and returns the runner's result. Graph state holds only the
request and that result, never a second copy of InvestigationState.

ToolExecutor and Provider arrive per invocation through LangGraph's runtime
context, never through graph state, input, or output. The router uses only
the requested InvestigationMode; the provider never chooses or sees the mode.
The graph is compiled without a checkpointer, has no retry policy, and lets
runner exceptions propagate unchanged.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from tracepilot.investigation.adaptive_runner import AdaptiveRunResult, run_adaptive
from tracepilot.investigation.executor import ToolExecutor
from tracepilot.investigation.fixed_runner import FixedRunResult, run_fixed_playbook
from tracepilot.investigation.models import InitialInvestigationContext, InvestigationMode
from tracepilot.investigation.plan_once_runner import PlanOnceRunResult, run_plan_once
from tracepilot.investigation.provider import IncidentBrief, Provider

# 3개의 mode를 하나의 입구로 모으는 역할 1차 적으로는 thin wrapper
# 랭그래프가 요구하는 형식(if문을 그대로 받지 않음) 을 충족시키기 위해
# 클래스와 함수로 그래프에 들어오고 나가고 실행을 받고 갈림길을 정하는 등
# 모든 프로세스를 구현한다
# 양식4 / 갈림길1 / 노드3 / 조립 1

type InvestigationRunResult = FixedRunResult | PlanOnceRunResult | AdaptiveRunResult

# 노드 이름 상수, MODE_NODES 표
FIXED_PLAYBOOK_NODE: Final = "fixed_playbook"
PLAN_ONCE_NODE: Final = "plan_once"
ADAPTIVE_NODE: Final = "adaptive"
MODE_NODES: Final[Mapping[InvestigationMode, str]] = {
    InvestigationMode.FIXED_PLAYBOOK: FIXED_PLAYBOOK_NODE,
    InvestigationMode.PLAN_ONCE: PLAN_ONCE_NODE,
    InvestigationMode.ADAPTIVE: ADAPTIVE_NODE,
}


# 그래프에 들어가는 요청
class InvestigationRequest(TypedDict):
    """Graph input: what to investigate and under which mode."""

    case_id: str
    mode: InvestigationMode
    incident: IncidentBrief
    context: InitialInvestigationContext


# 그래프가 들고 있는 부분
class InvestigationGraphState(InvestigationRequest, total=False):
    """The request plus the mode runner's result. Investigation semantics
    (plan, cursor, tool calls, hypothesis, termination) live only inside the
    runner and its result."""

    result: InvestigationRunResult


# result 담기 (나가는)
class InvestigationGraphOutput(TypedDict):
    result: InvestigationRunResult


# 도구묶음 담기
@dataclass(frozen=True)
class InvestigationDependencies:
    """Per-invocation runtime dependencies; never part of graph state."""

    executor: ToolExecutor
    provider: Provider


# 모드를 보고 노드 이름을 돌려주는 함수
def route_investigation(state: InvestigationRequest) -> str:
    """Deterministic routing on the requested mode only."""
    return MODE_NODES[state["mode"]]


async def _fixed_playbook_node(
    state: InvestigationGraphState, runtime: Runtime[InvestigationDependencies]
) -> InvestigationGraphOutput:
    result = await run_fixed_playbook(
        case_id=state["case_id"],
        incident=state["incident"],
        context=state["context"],
        executor=runtime.context.executor,
        provider=runtime.context.provider,
    )
    return {"result": result}


async def _plan_once_node(
    state: InvestigationGraphState, runtime: Runtime[InvestigationDependencies]
) -> InvestigationGraphOutput:
    result = await run_plan_once(
        case_id=state["case_id"],
        incident=state["incident"],
        context=state["context"],
        executor=runtime.context.executor,
        provider=runtime.context.provider,
    )
    return {"result": result}


async def _adaptive_node(
    state: InvestigationGraphState, runtime: Runtime[InvestigationDependencies]
) -> InvestigationGraphOutput:
    result = await run_adaptive(
        case_id=state["case_id"],
        incident=state["incident"],
        context=state["context"],
        executor=runtime.context.executor,
        provider=runtime.context.provider,
    )
    return {"result": result}


type InvestigationGraph = CompiledStateGraph[
    InvestigationGraphState,
    InvestigationDependencies,
    InvestigationRequest,
    InvestigationGraphOutput,
]


# 그래프 조립
def build_investigation_graph() -> InvestigationGraph:
    """Build and compile the dispatch graph (no checkpointer, no retries)."""
    builder = StateGraph(
        InvestigationGraphState,
        context_schema=InvestigationDependencies,
        input_schema=InvestigationRequest,
        output_schema=InvestigationGraphOutput,
    )
    builder.add_node(FIXED_PLAYBOOK_NODE, _fixed_playbook_node)
    builder.add_node(PLAN_ONCE_NODE, _plan_once_node)
    builder.add_node(ADAPTIVE_NODE, _adaptive_node)
    builder.add_conditional_edges(START, route_investigation, list(MODE_NODES.values()))
    for node in MODE_NODES.values():
        builder.add_edge(node, END)
    return builder.compile()


# Holds no per-run dependency; those arrive through each invocation's context.
_INVESTIGATION_GRAPH: Final = build_investigation_graph()


# 실행 함수 ainvoke로 그래프 실행, 앞의 a는 async를 의미
async def run_investigation(
    *,
    case_id: str,
    mode: InvestigationMode,
    incident: IncidentBrief,
    context: InitialInvestigationContext,
    executor: ToolExecutor,
    provider: Provider,
) -> InvestigationRunResult:
    """Run one investigation through the shared runtime. Returns the mode
    runner's own result; runner exceptions propagate unchanged."""
    if not isinstance(mode, InvestigationMode):
        raise ValueError(f"mode must be an InvestigationMode, got {mode!r}")
    request: InvestigationRequest = {
        "case_id": case_id,
        "mode": mode,
        "incident": incident,
        "context": context,
    }
    output = await _INVESTIGATION_GRAPH.ainvoke(
        request, context=InvestigationDependencies(executor=executor, provider=provider)
    )
    result: InvestigationRunResult = output["result"]
    return result
