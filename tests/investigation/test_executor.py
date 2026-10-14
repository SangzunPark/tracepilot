"""Week 4 policy-checked MCP executor over the in-memory MCP server.

Primary flows run against the real create_mcp_server(...) through an injected
mcp.Client wrapped in a call-recording spy. Error-boundary cases that the real
server cannot produce on demand use small stub clients.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CONNECTION_CLOSED,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    REQUEST_TIMEOUT,
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

from tests.investigation.executor_support import Recorded, SpyClient, StubClient, new_state
from tests.investigation.support import blocked, executed, inc015_context, plan
from tests.mcp.support import build_service, serialize
from tracepilot.diagnostics.service import DiagnosticService
from tracepilot.investigation.executor import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    MISSING_ERROR_TEXT,
    ExecutorInvariantError,
    InvestigationTerminatedError,
    ToolExecutionError,
    ToolExecutor,
    ToolSetMismatchError,
    observation_from_result,
    record_tool_call,
    verify_tool_set,
)
from tracepilot.investigation.models import (
    MAX_ERROR_MESSAGE_CHARS,
    MAX_OBSERVATION_RECORDS,
    BlockReason,
    InvestigationMode,
    InvestigationPlan,
    InvestigationState,
    TerminationReason,
    ToolCallRecord,
    ToolRequest,
)
from tracepilot.investigation.policies import ALLOWED_TOOLS, PolicyDecision, plan_review_pending
from tracepilot.mcp.server import create_mcp_server


def run_in_memory[T](
    scenario: Callable[[ToolExecutor, SpyClient], Awaitable[T]],
    service: DiagnosticService | None = None,
    **executor_options: Any,
) -> T:
    async def main() -> T:
        server = create_mcp_server(service if service is not None else build_service())
        async with Client(server) as client:
            spy = SpyClient(client)
            return await scenario(ToolExecutor(spy, **executor_options), spy)

    return asyncio.run(main())


async def attempt_and_record(
    executor: ToolExecutor, state: InvestigationState, tool: str, arguments: dict[str, Any]
) -> tuple[InvestigationState, ToolCallRecord]:
    record = await executor.attempt(state, ToolRequest(tool=tool, arguments=arguments))
    return record_tool_call(state, record), record


def text_result(text: str, *, is_error: bool) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=is_error)


def records_result(count: int) -> CallToolResult:
    records = [{"source_id": f"log-{index:03d}"} for index in range(count)]
    return CallToolResult(content=[], structured_content={"result": records})


def tool_listing(names: list[str], next_cursor: str | None = None) -> ListToolsResult:
    tools = [Tool(name=name, input_schema={"type": "object"}) for name in names]
    return ListToolsResult(tools=tools, next_cursor=next_cursor)


# ---------------------------------------------------------------------------
# Setup: tool set verification and executor configuration
# ---------------------------------------------------------------------------


def test_real_server_exposes_exactly_the_allowlist() -> None:
    async def scenario(executor: ToolExecutor, spy: SpyClient) -> None:
        await verify_tool_set(spy)

    run_in_memory(scenario)


@pytest.mark.parametrize(
    "names",
    [
        sorted(ALLOWED_TOOLS - {"search_runbooks"}),
        [*sorted(ALLOWED_TOOLS), "delete_database"],
        [*sorted(ALLOWED_TOOLS - {"search_runbooks"}), "delete_database"],
        [],
    ],
    ids=["missing-tool", "unexpected-tool", "same-count-substituted", "empty"],
)
def test_tool_set_mismatch_fails(names: list[str]) -> None:
    client = StubClient(listings=[tool_listing(names)])
    with pytest.raises(ToolSetMismatchError):
        asyncio.run(verify_tool_set(client))


def test_tool_set_verification_follows_pagination() -> None:
    names = sorted(ALLOWED_TOOLS)
    client = StubClient(listings=[tool_listing(names[:3], "page-2"), tool_listing(names[3:])])

    asyncio.run(verify_tool_set(client))

    assert client.cursors == [None, "page-2"]


def test_executor_defaults_and_rejects_bad_configuration() -> None:
    executor = ToolExecutor(StubClient())
    assert executor.timeout_seconds == DEFAULT_TOOL_TIMEOUT_SECONDS
    assert executor.max_steps == 15
    for options in ({"timeout_seconds": 0}, {"timeout_seconds": -1}, {"max_steps": 0}):
        with pytest.raises(ValueError):
            ToolExecutor(StubClient(), **options)


# ---------------------------------------------------------------------------
# Blocked requests: no MCP call, no observation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments", "reason"),
    [
        ("delete_database", {"force": True}, BlockReason.UNKNOWN_TOOL),
        (
            "search_runbooks",
            {"query": "pool", "case_id": "INC-001"},
            BlockReason.FORBIDDEN_ARGUMENT,
        ),
        (
            "search_application_logs",
            {"service": "checkout-api", "limit": None},
            BlockReason.INVALID_ARGUMENTS,
        ),
        ("query_service_metrics", {"metric": "latency"}, BlockReason.INVALID_ARGUMENTS),
    ],
    ids=["unknown-tool", "case-id", "limit-none", "missing-service"],
)
def test_blocked_request_does_not_reach_mcp(
    tool: str, arguments: dict[str, Any], reason: BlockReason
) -> None:
    async def scenario(
        executor: ToolExecutor, spy: SpyClient
    ) -> tuple[InvestigationState, ToolCallRecord]:
        return await attempt_and_record(executor, new_state(), tool, arguments)

    state, record = run_in_memory(scenario)

    assert record.step == 1
    assert record.tool == tool
    assert (record.permitted, record.executed, record.observation) == (False, False, None)
    assert record.block_reason is reason
    assert record.block_detail
    assert (record.normalized_arguments, record.fingerprint) == (None, None)
    assert (state.step_count, state.logical_tool_calls, state.execution_attempts) == (1, 0, 0)


def test_blocked_request_never_calls_the_client() -> None:
    client = StubClient(error=AssertionError("must not be called"))
    executor = ToolExecutor(client)

    record = asyncio.run(
        executor.attempt(new_state(), ToolRequest(tool="search_runbooks", arguments={}))
    )

    assert record.block_reason is BlockReason.INVALID_ARGUMENTS
    assert client.calls == []


def test_block_detail_is_deterministic() -> None:
    executor = ToolExecutor(StubClient())
    request = ToolRequest(tool="search_application_logs", arguments={"limit": "x"})

    first = asyncio.run(executor.attempt(new_state(), request))
    second = asyncio.run(executor.attempt(new_state(), request))

    assert first == second


def test_blocked_attempt_creates_no_plan_review() -> None:
    async def scenario(executor: ToolExecutor, spy: SpyClient) -> InvestigationState:
        state, _ = await attempt_and_record(executor, new_state(), "search_runbooks", {})
        return state

    assert not plan_review_pending(run_in_memory(scenario))


# ---------------------------------------------------------------------------
# Successful execution
# ---------------------------------------------------------------------------


def test_successful_metrics_call() -> None:
    async def scenario(executor: ToolExecutor, spy: SpyClient) -> ToolCallRecord:
        return await executor.attempt(
            new_state("INC-001"),
            ToolRequest(tool="query_service_metrics", arguments={"service": "checkout-api"}),
        )

    record = run_in_memory(scenario)

    expected = serialize(build_service().query_service_metrics("INC-001", "checkout-api"))
    assert expected
    assert (record.permitted, record.executed, record.block_reason) == (True, True, None)
    assert record.observation is not None
    assert record.observation.is_error is False
    assert list(record.observation.records) == expected
    assert record.observation.record_count == len(expected)
    assert record.observation.truncated is False


def test_successful_logs_call_with_case_id_injected() -> None:
    async def scenario(executor: ToolExecutor, spy: SpyClient) -> tuple[ToolCallRecord, Recorded]:
        record = await executor.attempt(
            new_state("INC-001"),
            ToolRequest(
                tool="search_application_logs",
                arguments={"service": "checkout-api", "limit": 3},
            ),
        )
        return record, spy.calls

    record, calls = run_in_memory(scenario)

    expected = serialize(
        build_service().search_application_logs("INC-001", "checkout-api", limit=3)
    )
    assert record.observation is not None
    assert list(record.observation.records) == expected
    assert calls == [
        (
            "search_application_logs",
            {
                "case_id": "INC-001",
                "service": "checkout-api",
                "query": None,
                "start_time": None,
                "end_time": None,
                "limit": 3,
            },
        )
    ]
    assert record.normalized_arguments is not None
    assert "case_id" not in record.normalized_arguments


def test_successful_runbook_call_without_case_id() -> None:
    async def scenario(executor: ToolExecutor, spy: SpyClient) -> tuple[ToolCallRecord, Recorded]:
        record = await executor.attempt(
            new_state(), ToolRequest(tool="search_runbooks", arguments={"query": "connection"})
        )
        return record, spy.calls

    record, calls = run_in_memory(scenario)

    expected = serialize(build_service().search_runbooks("connection"))
    assert expected
    assert record.observation is not None
    assert list(record.observation.records) == expected
    assert calls == [("search_runbooks", {"query": "connection", "service": None, "limit": 20})]


@pytest.mark.parametrize(
    "tool",
    [
        "query_service_metrics",
        "search_application_logs",
        "get_deployment_history",
        "get_feature_flag_history",
    ],
)
def test_runtime_case_id_is_injected_for_case_scoped_tools(tool: str) -> None:
    async def scenario(executor: ToolExecutor, spy: SpyClient) -> Recorded:
        await executor.attempt(
            new_state("INC-014"), ToolRequest(tool=tool, arguments={"service": "checkout-api"})
        )
        return spy.calls

    [(name, arguments)] = run_in_memory(scenario)
    assert name == tool
    assert arguments is not None and arguments["case_id"] == "INC-014"


def test_runtime_case_id_is_injected_for_external_status() -> None:
    async def scenario(executor: ToolExecutor, spy: SpyClient) -> Recorded:
        await executor.attempt(
            new_state("INC-014"),
            ToolRequest(
                tool="get_external_service_status", arguments={"provider": "payment-gateway"}
            ),
        )
        return spy.calls

    [(_, arguments)] = run_in_memory(scenario)
    assert arguments is not None and arguments["case_id"] == "INC-014"


def test_attempt_does_not_mutate_request_or_state() -> None:
    arguments: dict[str, Any] = {
        "service": "checkout-api",
        "start_time": "2026-04-06T22:50:00+09:00",
    }
    request = ToolRequest(tool="get_deployment_history", arguments=arguments)
    state = new_state()
    before_request = request.model_copy(deep=True)
    before_state = state.model_copy(deep=True)

    async def scenario(executor: ToolExecutor, spy: SpyClient) -> ToolCallRecord:
        return await executor.attempt(state, request)

    record = run_in_memory(scenario)

    assert request == before_request
    assert state == before_state
    assert state.step_count == 0
    assert record.normalized_arguments is not None
    assert record.normalized_arguments["start_time"] == "2026-04-06T13:50:00Z"


# ---------------------------------------------------------------------------
# Case isolation
# ---------------------------------------------------------------------------


def test_inc014_run_cannot_query_inc007_through_provider_arguments() -> None:
    async def scenario(
        executor: ToolExecutor, spy: SpyClient
    ) -> tuple[InvestigationState, Recorded]:
        state, record = await attempt_and_record(
            executor,
            new_state("INC-014"),
            "search_application_logs",
            {"case_id": "INC-007", "service": "checkout-api"},
        )
        assert record.block_reason is BlockReason.FORBIDDEN_ARGUMENT
        assert spy.calls == []
        state, _ = await attempt_and_record(
            executor, state, "search_application_logs", {"service": "checkout-api"}
        )
        return state, spy.calls

    state, calls = run_in_memory(scenario)

    service = build_service()
    inc014 = serialize(service.search_application_logs("INC-014", "checkout-api"))
    inc007 = serialize(service.search_application_logs("INC-007", "checkout-api"))
    assert inc014 != inc007
    observation = state.tool_calls[1].observation
    assert observation is not None
    assert list(observation.records) == inc014
    assert [arguments["case_id"] for _, arguments in calls if arguments] == ["INC-014"]


# ---------------------------------------------------------------------------
# MCP errors, timeouts, and client failures
# ---------------------------------------------------------------------------


def test_returned_domain_error_is_an_executed_error_observation() -> None:
    async def scenario(executor: ToolExecutor, spy: SpyClient) -> InvestigationState:
        state, _ = await attempt_and_record(
            executor, new_state(), "query_service_metrics", {"service": "no-such-service"}
        )
        return state

    state = run_in_memory(scenario)

    [record] = state.tool_calls
    assert (record.permitted, record.executed) == (True, True)
    assert record.observation is not None
    assert record.observation.is_error is True
    assert record.observation.error_message is not None
    assert record.observation.error_message.startswith("Error executing tool query_service_metrics")
    assert (record.observation.record_count, record.observation.records) == (0, ())
    assert (state.step_count, state.logical_tool_calls, state.execution_attempts) == (1, 1, 1)


def test_executed_error_creates_a_plan_review() -> None:
    async def scenario(executor: ToolExecutor, spy: SpyClient) -> InvestigationState:
        state, _ = await attempt_and_record(
            executor,
            new_state(),
            "search_application_logs",
            {"service": "checkout-api", "limit": 0},
        )
        return state

    state = run_in_memory(scenario)

    assert state.tool_calls[0].observation is not None
    assert state.tool_calls[0].observation.is_error
    assert plan_review_pending(state)


def test_long_error_text_is_bounded() -> None:
    executor = ToolExecutor(StubClient(result=text_result("e" * 2000, is_error=True)))

    record = asyncio.run(
        executor.attempt(new_state(), ToolRequest(tool="search_runbooks", arguments={"query": "q"}))
    )

    assert record.observation is not None
    assert record.observation.error_message == "e" * MAX_ERROR_MESSAGE_CHARS


def test_error_result_without_text() -> None:
    observation = observation_from_result(CallToolResult(content=[], is_error=True))
    assert observation.error_message == MISSING_ERROR_TEXT


def test_large_result_is_truncated() -> None:
    executor = ToolExecutor(StubClient(result=records_result(60)))

    record = asyncio.run(
        executor.attempt(new_state(), ToolRequest(tool="search_runbooks", arguments={"query": "q"}))
    )

    observation = record.observation
    assert observation is not None
    assert observation.record_count == 60
    assert len(observation.records) == MAX_OBSERVATION_RECORDS
    assert observation.records[-1] == {"source_id": "log-049"}
    assert observation.truncated is True


def test_empty_result_is_a_successful_observation() -> None:
    observation = observation_from_result(records_result(0))
    assert (observation.is_error, observation.record_count, observation.truncated) == (
        False,
        0,
        False,
    )


@pytest.mark.parametrize(
    "result",
    [
        CallToolResult(content=[TextContent(type="text", text="[]")]),
        CallToolResult(content=[], structured_content={"other": []}),
        CallToolResult(content=[], structured_content={"result": "not-a-list"}),
        CallToolResult(content=[], structured_content={"result": [1, 2]}),
    ],
    ids=["no-structured-content", "no-result-key", "not-a-list", "non-object-records"],
)
def test_malformed_success_result_raises(result: CallToolResult) -> None:
    with pytest.raises(ToolExecutionError):
        observation_from_result(result)


def _slow_server() -> MCPServer:
    server = MCPServer("slow-diagnostics")

    @server.tool()
    async def search_runbooks(query: str) -> list[dict[str, str]]:
        await anyio.sleep(30)
        return []

    @server.tool()
    def get_deployment_history(case_id: str, service: str) -> list[dict[str, str]]:
        return [{"source_id": "dep-001"}]

    return server


def test_read_timeout_becomes_an_executed_error_observation() -> None:
    async def main() -> tuple[ToolCallRecord, ToolCallRecord]:
        async with Client(_slow_server()) as client:
            executor = ToolExecutor(client, timeout_seconds=0.05)
            with anyio.fail_after(5):
                timed_out = await executor.attempt(
                    new_state(), ToolRequest(tool="search_runbooks", arguments={"query": "q"})
                )
            # The session stays usable after a timeout.
            after = await executor.attempt(
                new_state(),
                ToolRequest(tool="get_deployment_history", arguments={"service": "checkout-api"}),
            )
            return timed_out, after

    timed_out, after = asyncio.run(main())

    assert (timed_out.permitted, timed_out.executed) == (True, True)
    assert timed_out.observation is not None
    assert timed_out.observation.is_error
    assert timed_out.observation.error_message == (
        "MCP error -32001: Timed out after 0.05s waiting for 'tools/call'"
    )
    assert after.observation is not None
    assert after.observation.records == ({"source_id": "dep-001"},)


def test_stubbed_timeout_code_becomes_an_executed_error_observation() -> None:
    error = MCPError(code=REQUEST_TIMEOUT, message="Timed out after 10.0s waiting for 'tools/call'")
    executor = ToolExecutor(StubClient(error=error))

    record = asyncio.run(
        executor.attempt(new_state(), ToolRequest(tool="search_runbooks", arguments={"query": "q"}))
    )

    assert (record.permitted, record.executed) == (True, True)
    assert record.observation is not None
    assert record.observation.error_message == (
        "MCP error -32001: Timed out after 10.0s waiting for 'tools/call'"
    )


NON_TIMEOUT_MCP_ERRORS = [
    MCPError(code=CONNECTION_CLOSED, message="Connection closed"),
    MCPError(code=INTERNAL_ERROR, message="Internal error"),
    MCPError(code=INVALID_PARAMS, message="Invalid params"),
    MCPError(code=METHOD_NOT_FOUND, message="Method not found"),
    # Classified by code, never by message text.
    MCPError(code=CONNECTION_CLOSED, message="Timed out waiting for 'tools/call'"),
]


@pytest.mark.parametrize(
    "error",
    NON_TIMEOUT_MCP_ERRORS,
    ids=["connection-closed", "internal", "invalid-params", "method-not-found", "timeout-text"],
)
def test_non_timeout_mcp_error_raises_tool_execution_error(error: MCPError) -> None:
    executor = ToolExecutor(StubClient(error=error))

    with pytest.raises(ToolExecutionError) as raised:
        asyncio.run(
            executor.attempt(
                new_state(), ToolRequest(tool="search_runbooks", arguments={"query": "q"})
            )
        )

    assert raised.value.__cause__ is error


@pytest.mark.parametrize(
    "error",
    [
        MCPError(code=CONNECTION_CLOSED, message="Connection closed"),
        RuntimeError("client gone"),
        ValueError("bad result"),
        OSError("pipe closed"),
    ],
    ids=["mcp-connection-closed", "runtime", "value", "os"],
)
def test_client_failures_raise_and_leave_state_untouched(error: Exception) -> None:
    state = new_state(records=(blocked(1),))
    before = state.model_copy(deep=True)
    executor = ToolExecutor(StubClient(error=error))

    with pytest.raises(ToolExecutionError) as raised:
        asyncio.run(
            executor.attempt(state, ToolRequest(tool="search_runbooks", arguments={"query": "q"}))
        )

    assert raised.value.__cause__ is error
    assert state == before
    assert (state.step_count, state.logical_tool_calls, state.execution_attempts) == (1, 0, 0)


def test_cancellation_is_not_caught() -> None:
    executor = ToolExecutor(StubClient(error=asyncio.CancelledError()))  # type: ignore[arg-type]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            executor.attempt(
                new_state(), ToolRequest(tool="search_runbooks", arguments={"query": "q"})
            )
        )


def test_unopened_client_raises_tool_execution_error() -> None:
    executor = ToolExecutor(Client(create_mcp_server(build_service())))

    with pytest.raises(ToolExecutionError):
        asyncio.run(
            executor.attempt(
                new_state(), ToolRequest(tool="search_runbooks", arguments={"query": "q"})
            )
        )


@pytest.mark.parametrize(
    "error",
    [
        MCPError(code=REQUEST_TIMEOUT, message="Timed out"),
        MCPError(code=CONNECTION_CLOSED, message="Connection closed"),
        OSError("pipe closed"),
    ],
    ids=["timeout", "connection-closed", "os"],
)
def test_executor_does_not_retry(error: Exception) -> None:
    client = StubClient(error=error)
    executor = ToolExecutor(client)

    try:
        asyncio.run(
            executor.attempt(
                new_state(), ToolRequest(tool="search_runbooks", arguments={"query": "q"})
            )
        )
    except ToolExecutionError:
        pass

    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


def test_second_identical_call_is_blocked_without_calling_mcp() -> None:
    async def scenario(
        executor: ToolExecutor, spy: SpyClient
    ) -> tuple[InvestigationState, Recorded]:
        state, first = await attempt_and_record(
            executor, new_state(), "search_application_logs", {"service": "checkout-api"}
        )
        assert first.executed
        state, second = await attempt_and_record(
            executor,
            state,
            "search_application_logs",
            {"limit": 50, "query": None, "service": "checkout-api"},
        )
        assert second.block_reason is BlockReason.DUPLICATE
        assert second.fingerprint == first.fingerprint
        assert second.normalized_arguments == first.normalized_arguments
        assert second.observation is None
        return state, spy.calls

    state, calls = run_in_memory(scenario)

    assert len(calls) == 1
    assert (state.step_count, state.logical_tool_calls, state.execution_attempts) == (2, 1, 1)


def test_permitted_error_call_still_seeds_duplicate_blocking() -> None:
    async def scenario(executor: ToolExecutor, spy: SpyClient) -> tuple[ToolCallRecord, Recorded]:
        state, first = await attempt_and_record(
            executor, new_state(), "query_service_metrics", {"service": "no-such-service"}
        )
        assert first.observation is not None and first.observation.is_error
        _, second = await attempt_and_record(
            executor, state, "query_service_metrics", {"service": "no-such-service"}
        )
        return second, spy.calls

    second, calls = run_in_memory(scenario)

    assert second.block_reason is BlockReason.DUPLICATE
    assert len(calls) == 1


def test_blocked_invalid_call_does_not_seed_duplicate_blocking() -> None:
    async def scenario(executor: ToolExecutor, spy: SpyClient) -> ToolCallRecord:
        state, first = await attempt_and_record(
            executor, new_state(), "search_runbooks", {"query": "pool", "limit": None}
        )
        assert first.block_reason is BlockReason.INVALID_ARGUMENTS
        _, second = await attempt_and_record(executor, state, "search_runbooks", {"query": "pool"})
        return second

    assert run_in_memory(scenario).executed


# ---------------------------------------------------------------------------
# Step numbering, budget, and termination
# ---------------------------------------------------------------------------


def test_steps_are_numbered_from_existing_records() -> None:
    async def scenario(executor: ToolExecutor, spy: SpyClient) -> list[int]:
        state = new_state()
        steps: list[int] = []
        for arguments in ({}, {"query": "a"}, {"query": "a"}, {"query": "b"}):
            state, record = await attempt_and_record(executor, state, "search_runbooks", arguments)
            steps.append(record.step)
        return steps

    assert run_in_memory(scenario) == [1, 2, 3, 4]


def test_attempt_is_pure_with_respect_to_state() -> None:
    executor = ToolExecutor(StubClient(result=records_result(1)))
    state = new_state()
    request = ToolRequest(tool="search_runbooks", arguments={"query": "q"})

    first = asyncio.run(executor.attempt(state, request))
    second = asyncio.run(executor.attempt(state, request))

    assert first.step == second.step == 1
    with pytest.raises(ValueError):
        record_tool_call(record_tool_call(state, first), second)


def test_step_fifteen_runs_and_step_sixteen_is_refused() -> None:
    fourteen = tuple(blocked(step) for step in range(1, 15))
    client = StubClient(result=records_result(1))
    executor = ToolExecutor(client)
    state = new_state(records=fourteen)

    record = asyncio.run(
        executor.attempt(state, ToolRequest(tool="search_runbooks", arguments={"query": "q"}))
    )
    state = record_tool_call(state, record)
    assert (record.step, record.executed, state.step_count) == (15, True, 15)

    with pytest.raises(InvestigationTerminatedError) as raised:
        asyncio.run(
            executor.attempt(state, ToolRequest(tool="search_runbooks", arguments={"query": "r"}))
        )
    assert raised.value.reason is TerminationReason.BUDGET_EXHAUSTED
    assert len(client.calls) == 1


def test_exhausted_budget_refuses_even_requests_policy_would_block() -> None:
    client = StubClient(error=AssertionError("must not be called"))
    state = new_state(records=tuple(blocked(step) for step in range(1, 16)))

    with pytest.raises(InvestigationTerminatedError):
        asyncio.run(ToolExecutor(client).attempt(state, ToolRequest(tool="delete_database")))
    assert client.calls == []


def test_custom_budget() -> None:
    executor = ToolExecutor(StubClient(result=records_result(1)), max_steps=2)
    state = new_state()
    for query in ("a", "b"):
        record = asyncio.run(
            executor.attempt(state, ToolRequest(tool="search_runbooks", arguments={"query": query}))
        )
        state = record_tool_call(state, record)

    with pytest.raises(InvestigationTerminatedError):
        asyncio.run(
            executor.attempt(state, ToolRequest(tool="search_runbooks", arguments={"query": "c"}))
        )


@pytest.mark.parametrize("reason", list(TerminationReason))
def test_recorded_termination_refuses_new_actions(reason: TerminationReason) -> None:
    state = new_state().model_copy(update={"termination_reason": reason})
    client = StubClient(error=AssertionError("must not be called"))

    with pytest.raises(InvestigationTerminatedError) as raised:
        asyncio.run(
            ToolExecutor(client).attempt(
                state, ToolRequest(tool="search_runbooks", arguments={"query": "q"})
            )
        )
    assert raised.value.reason is reason


@pytest.mark.parametrize("mode", [InvestigationMode.FIXED_PLAYBOOK, InvestigationMode.PLAN_ONCE])
def test_terminally_exhausted_plan_refuses_new_actions(mode: InvestigationMode) -> None:
    state = InvestigationState(
        case_id="INC-014",
        mode=mode,
        initial_context=inc015_context(),
        plan=InvestigationPlan(),
        plan_version=1,
    )
    with pytest.raises(InvestigationTerminatedError) as raised:
        asyncio.run(
            ToolExecutor(StubClient()).attempt(
                state, ToolRequest(tool="search_runbooks", arguments={"query": "q"})
            )
        )
    assert raised.value.reason is TerminationReason.PLAN_EXHAUSTED


# ---------------------------------------------------------------------------
# record_tool_call
# ---------------------------------------------------------------------------


def test_record_tool_call_appends_without_moving_the_plan_cursor() -> None:
    state = new_state()
    record = blocked(1)

    updated = record_tool_call(state, record)

    assert updated.tool_calls == (record,)
    assert updated.current_step_index == state.current_step_index == 0
    assert state.tool_calls == ()


def test_record_tool_call_rejects_wrong_step_and_terminated_state() -> None:
    with pytest.raises(ValueError):
        record_tool_call(new_state(), blocked(2))
    terminated = new_state().model_copy(
        update={"termination_reason": TerminationReason.PLAN_EXHAUSTED}
    )
    with pytest.raises(InvestigationTerminatedError):
        record_tool_call(terminated, blocked(1))


# ---------------------------------------------------------------------------
# Exhausted Adaptive plans at the executor (plan-review routing is the mode loop's)
# ---------------------------------------------------------------------------


def _exhausted_adaptive_state(
    records: tuple[ToolCallRecord, ...], plan_decision_at_step: int = 0
) -> InvestigationState:
    return InvestigationState(
        case_id="INC-014",
        mode=InvestigationMode.ADAPTIVE,
        initial_context=inc015_context(),
        plan=plan("search_application_logs"),
        plan_version=1,
        current_step_index=1,
        plan_decision_at_step=plan_decision_at_step,
        tool_calls=records,
    )


def test_revised_adaptive_plan_allows_the_next_action() -> None:
    revised = _exhausted_adaptive_state((executed(1),), plan_decision_at_step=1).model_copy(
        update={"plan_version": 2, "current_step_index": 0}
    )
    executor = ToolExecutor(StubClient(result=records_result(1)))

    record = asyncio.run(
        executor.attempt(revised, ToolRequest(tool="search_runbooks", arguments={"query": "q"}))
    )

    assert (record.step, record.executed) == (2, True)


def test_exhausted_adaptive_plan_with_budget_exhausted_is_terminated() -> None:
    records = tuple(executed(step, fingerprint=f"{step:064x}") for step in range(1, 16))
    state = _exhausted_adaptive_state(records)

    with pytest.raises(InvestigationTerminatedError) as raised:
        asyncio.run(
            ToolExecutor(StubClient()).attempt(
                state, ToolRequest(tool="search_runbooks", arguments={"query": "q"})
            )
        )
    assert raised.value.reason is TerminationReason.BUDGET_EXHAUSTED


def test_exhausted_adaptive_plan_after_blocked_step_is_plan_exhausted() -> None:
    state = _exhausted_adaptive_state((blocked(1),))

    with pytest.raises(InvestigationTerminatedError) as raised:
        asyncio.run(
            ToolExecutor(StubClient()).attempt(
                state, ToolRequest(tool="search_runbooks", arguments={"query": "q"})
            )
        )
    assert raised.value.reason is TerminationReason.PLAN_EXHAUSTED


def test_permitted_decision_without_call_raises_invariant_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracepilot.investigation.executor.evaluate_request",
        lambda request, prior: PolicyDecision(permitted=True),
    )
    client = StubClient(error=AssertionError("must not be called"))

    with pytest.raises(ExecutorInvariantError):
        asyncio.run(
            ToolExecutor(client).attempt(
                new_state(), ToolRequest(tool="search_runbooks", arguments={"query": "q"})
            )
        )
    assert client.calls == []
