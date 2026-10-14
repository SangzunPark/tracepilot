"""Week 4 policy-checked MCP executor.

One investigation action attempt:

    ToolRequest -> deterministic policy -> (blocked record)
                                        -> normalized call -> runtime case_id
                                           -> MCP tools/call -> Observation
                                           -> executed record

The executor never chooses tools, plans, revises, concludes, or retries. It
does not own the MCP client lifecycle: the caller opens one client for the
investigation run and injects it. Attempting an action and recording it in
the state are separate steps (`ToolExecutor.attempt` and `record_tool_call`).

Week 4 error boundary:

A. policy block: no MCP call, no Observation, a blocked record.
B. MCP tool error (CallToolResult.is_error): executed, error Observation.
C. client exception:
   - MCPError with code REQUEST_TIMEOUT (the read timeout; the session stays
     usable): executed, error Observation with the deterministic text
     "MCP error <code>: <message>".
   - any other MCPError or exception (connection closed, client not open,
     malformed result, ...): raised as ToolExecutionError with the original
     exception as its cause. No record is produced, the state is untouched,
     and the Week 4 investigation aborts outside InvestigationState; there is
     no failure TerminationReason.
   - cancellation is never caught.

No error is retried or classified for recovery (Week 6).

Mode-loop contract (implemented by the later mode layer, not here): a plan
step is consumed once attempted, so a blocked step still advances
current_step_index; no mode replaces a blocked step with an ad-hoc action.
In Adaptive, every fresh executed Observation (while budget remains) gets
exactly one plan review (KEEP or REPLACE) before the runner proceeds or lets
plan exhaustion become terminal. That routing belongs to the mode loop; the
executor does not enforce it.
"""
# 위 docstirng은 이 파일에 대한 설명이며,
# ToolExecutor가 하지 않는 일 / 실패 종류 / Mode loop 규칙

# executor에서 지시하는 파일(상사)는 mode loop 파일이며 각 파일은
# fixed playbook, plan once, adaptive 가 있다
from dataclasses import dataclass
from typing import Any, Final, Protocol

from mcp.shared.exceptions import MCPError
from mcp.types import REQUEST_TIMEOUT, CallToolResult, ListToolsResult, TextContent
from pydantic import JsonValue

from tracepilot.investigation.models import (
    InvestigationState,
    Observation,
    TerminationReason,
    ToolCallRecord,
    ToolName,
    ToolRequest,
)
from tracepilot.investigation.policies import (
    ALLOWED_TOOLS,
    MAX_AGENT_STEPS,
    evaluate_request,
    execution_arguments,
    prior_fingerprints,
    structural_termination,
)

# 상수 등록
# Provisional Week 4 engineering default for the per-call MCP read timeout,
# shared by all modes. The evaluation value is recorded at preregistration.
DEFAULT_TOOL_TIMEOUT_SECONDS: Final = 10.0

MISSING_ERROR_TEXT: Final = "MCP tool returned an error without text"

# 진짜 MCP Client가 아니어도 call_tool과 list_tools만 있으면 executor에
# 꽃을 수 있다는 규격서 -> test에서 client를 사용 가능
# 이 기능을 위해 Protocol을 사용하고 이것을 통해 테스트에서 fakeclient 사용 가능
# 이와 같이 이름이 아닌 모양만 보는 코드를 duck typing 이라고 한다.


class ToolClient(Protocol):
    """The part of mcp.Client the executor uses. The caller owns its lifecycle."""

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> CallToolResult: ...

    async def list_tools(self, *, cursor: str | None = None) -> ListToolsResult: ...


# 에러 클래스 4종
# 서버 tool 대조
# 파이썬에서 에러는 "종류"이고 동작이 아니다. "종류"는 클래스로만 만들 수 있다.
# 언어로 치면 함수는 동사, 클래스는 명사
# 에러에 데이터를 담는 것도 가능하며 클래스 명으로 에러 파악 및 처리 가능
# 또 다른 이유로 에러 발생시 raise 뒤에는 클래스 또는 그 객체가 와야 한다.
class ToolSetMismatchError(RuntimeError):
    """The MCP server does not expose exactly the approved tool set."""


# 재요청 금지
class InvestigationTerminatedError(RuntimeError):
    """No new action may be attempted: the investigation is structurally
    terminated (for example, the step budget is exhausted)."""

    def __init__(self, reason: TerminationReason) -> None:
        super().__init__(f"investigation is terminated: {reason}")
        self.reason = reason


# 코드 버그
class ExecutorInvariantError(RuntimeError):
    """An internal invariant was violated (a bug, not an incident condition)."""


# 통신 오류
class ToolExecutionError(RuntimeError):
    """A transport/session failure that is not incident evidence. No record
    is produced; the Week 4 investigation aborts outside InvestigationState."""


# async def은 이 함수는 기다리는 동안 다른 일을 할 수 있는 함수 라는 문법
# 함수 안에 awaits를 통해 실제로 실행되며 보통 async와 awaits는 같이 사용
# 함수의 기능은 서버에 tool 목록을 요청하며 tool 6종의 이름이 같은지 검증
async def verify_tool_set(client: ToolClient) -> None:
    """Fail unless the server exposes exactly ALLOWED_TOOLS. The allowlist is
    authoritative and is never widened from list_tools()."""
    names: set[str] = set()
    cursor: str | None = None
    while True:
        listing = await client.list_tools(cursor=cursor)
        names.update(tool.name for tool in listing.tools)
        cursor = listing.next_cursor
        if cursor is None:
            break
    if names != ALLOWED_TOOLS:
        raise ToolSetMismatchError(
            f"MCP tool set mismatch: missing {sorted(ALLOWED_TOOLS - names)}, "
            f"unexpected {sorted(names - ALLOWED_TOOLS)}"
        )


# MCP 통신 결과 메모 내용으로 정리
# 아휴 attempt()가 메모 내용으로 TollCallRecord(메모 한 장)에 저장
def observation_from_result(result: CallToolResult) -> Observation:
    """Normalize a CallToolResult into a bounded Observation."""
    if result.is_error:
        text = "\n".join(item.text for item in result.content if isinstance(item, TextContent))
        return Observation.from_error(text or MISSING_ERROR_TEXT)
    structured = result.structured_content
    records = structured.get("result") if structured is not None else None
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ToolExecutionError("MCP tool result has no structured list of records")
    return Observation.from_records(records)


# attempts()의 메모 한 장을 업무일지(state)에 기록
def record_tool_call(state: InvestigationState, record: ToolCallRecord) -> InvestigationState:
    """Pure state transition: a new state with `record` appended as the next
    agent step. Does not move the plan cursor; that is the mode loop's job."""
    if state.termination_reason is not None:
        raise InvestigationTerminatedError(state.termination_reason)
    if record.step != state.step_count + 1:
        raise ValueError(f"expected step {state.step_count + 1}, got {record.step}")
    return InvestigationState(**{**dict(state), "tool_calls": (*state.tool_calls, record)})


# @dataclass는 클래스에 데이터를 담는 기능을 간편하게 만들어주는 도구
# 이 클래스에 데이터클래스 기능을 입혀라
# dataclass를 붙이면 __init__ -> self. 를 하지 않아도 자동으로 만들어준다
# frozen =True는 만든 뒤에는 값을 못 바꾼다는 옵션
@dataclass(frozen=True)
class ToolExecutor:
    """Executes policy-permitted actions over an injected, already-open MCP
    client. Holds no investigation state."""

    client: ToolClient
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS
    max_steps: int = MAX_AGENT_STEPS

    # dataclass가 _init__ 값을 넣은 후 바로 자동으로 호출되는 함수
    # 비 정상적인 설정으로는 executor 생성 불가하게 고정
    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")

    # state(일지)와 request(요청서)를 받아 ToolCallRecord(메모 한장)으로 반환
    async def attempt(self, state: InvestigationState, request: ToolRequest) -> ToolCallRecord:
        """Attempt one action as agent step `state.step_count + 1`.

        Raises InvestigationTerminatedError, without evaluating the request,
        when the state is structurally terminated (e.g. budget exhausted).
        """
        # 조사가 끝났는지(self.max_Steps)확인
        reason = structural_termination(state, self.max_steps)
        if reason is not None:
            raise InvestigationTerminatedError(reason)
        step = state.step_count + 1

        # policies 참고하여 허용/차단 결정
        decision = evaluate_request(request, prior_fingerprints(state.tool_calls))
        call = decision.call
        # 차단이이면 call 하지 않고 종료(정보와 함께)
        if not decision.permitted:
            return ToolCallRecord(
                step=step,
                tool=request.tool,
                normalized_arguments=call.arguments if call is not None else None,
                fingerprint=call.fingerprint if call is not None else None,
                permitted=False,
                block_reason=decision.block_reason,
                block_detail=decision.detail,
            )
        if call is None:
            raise ExecutorInvariantError("a permitted policy decision carried no normalized call")

        # 인시던트 번호(INC) 붙여서 call -> 메모 반환
        observation = await self._call(call.tool, execution_arguments(call, state.case_id))
        return ToolCallRecord(
            step=step,
            tool=call.tool,
            normalized_arguments=call.arguments,
            fingerprint=call.fingerprint,
            permitted=True,
            executed=True,
            observation=observation,
        )

    # 내부 함수 _call -> tool 이름 -> 전화내용withINC -> 정상이면 내용 반환
    async def _call(self, tool: ToolName, arguments: dict[str, JsonValue]) -> Observation:
        try:
            result = await self.client.call_tool(
                tool, arguments, read_timeout_seconds=self.timeout_seconds
            )
        except MCPError as error:
            if error.code != REQUEST_TIMEOUT:
                raise ToolExecutionError(f"MCP session failed calling {tool}") from error
            return Observation.from_error(f"MCP error {error.code}: {error.error.message}")
        except Exception as error:
            raise ToolExecutionError(f"MCP client failed calling {tool}") from error
        return observation_from_result(result)
