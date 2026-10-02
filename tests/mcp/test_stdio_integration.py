"""Real stdio/subprocess integration tests for the TracePilot MCP server.

Every test here spawns `python -m tracepilot.mcp` with the current interpreter
(sys.executable) and talks to it over the real stdio transport
(StdioServerParameters + stdio_client + Client). None of them uses the
in-memory Client(create_mcp_server(...)) path.

The suite is deliberately small: detailed tool contracts stay covered by the
fast in-memory tests, and this suite proves the process/transport boundary.
Subprocess spawns are kept to a minimum:

- one shared real session (module fixture) runs a scripted set of calls that
  the discovery, success, equivalence, empty-result, error, and determinism
  tests then assert on;
- the non-root-cwd and lifecycle tests each start their own subprocess,
  because they depend on process isolation.

Every session is bounded by STDIO_SESSION_TIMEOUT_SECONDS, so a subprocess
startup or protocol failure fails the test instead of hanging pytest or CI.
"""

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters, stdio_client
from mcp.types import CallToolResult, Tool

from tests.domain.support import EVALUATION_ROOT
from tests.mcp.support import (
    SUCCESS_CASES,
    ToolCall,
    ToolCase,
    build_service,
    serialize,
    structured_result,
)

REPO_ROOT = EVALUATION_ROOT.parent
SERVER_MODULE = "tracepilot.mcp"
STDIO_SESSION_TIMEOUT_SECONDS = 20.0

EXPECTED_TOOL_NAMES = {
    "query_service_metrics",
    "search_application_logs",
    "get_deployment_history",
    "get_feature_flag_history",
    "get_external_service_status",
    "search_runbooks",
}

KNOWN_INC_001_QUERY = SUCCESS_CASES[0]
EMPTY_CALL: ToolCall = (
    "query_service_metrics",
    {"case_id": "INC-001", "service": "checkout-api", "metric": "nonexistent_metric"},
)

NAIVE_TIME_CALL: ToolCall = (
    "query_service_metrics",
    {
        "case_id": "INC-001",
        "service": "checkout-api",
        "metric": "db_connection_pool_usage",
        "start_time": "2026-01-12T14:00:00",
        "end_time": "2026-01-12T14:00:00",
    },
)

UTC_TIME_CALL: ToolCall = (
    "query_service_metrics",
    {
        "case_id": "INC-001",
        "service": "checkout-api",
        "metric": "db_connection_pool_usage",
        "start_time": "2026-01-12T14:00:00Z",
        "end_time": "2026-01-12T14:00:00Z",
    },
)

ERROR_CALLS: dict[str, ToolCall] = {
    "unknown-case": (
        "query_service_metrics",
        {
            "case_id": "INC-DOES-NOT-EXIST",
            "service": "checkout-api",
        },
    ),
    "unknown-service": (
        "query_service_metrics",
        {
            "case_id": "INC-001",
            "service": "not-a-real-service",
        },
    ),
    "invalid-time-range": (
        "get_deployment_history",
        {
            "case_id": "INC-001",
            "service": "checkout-api",
            "start_time": "2026-01-12T15:00:00Z",
            "end_time": "2026-01-12T14:00:00Z",
        },
    ),
    "invalid-limit": (
        "search_application_logs",
        {
            "case_id": "INC-001",
            "service": "checkout-api",
            "limit": 0,
        },
    ),
    "path-traversal-case-id": (
        "query_service_metrics",
        {
            "case_id": "../ground_truth",
            "service": "checkout-api",
        },
    ),
}

REPEATED_CASE = SUCCESS_CASES[1]


def stdio_server_parameters(cwd: Path) -> StdioServerParameters:
    """`python -m tracepilot.mcp` with the current interpreter, over real stdio."""
    return StdioServerParameters(command=sys.executable, args=["-m", SERVER_MODULE], cwd=cwd)


async def _within_real_stdio_session[T](
    cwd: Path, interaction: Callable[[Client], Awaitable[T]]
) -> T:
    async with Client(stdio_client(stdio_server_parameters(cwd))) as client:
        return await interaction(client)


def run_over_real_stdio[T](
    interaction: Callable[[Client], Awaitable[T]], cwd: Path = REPO_ROOT
) -> T:
    """Spawn the server subprocess in `cwd`, run `interaction` against it over
    real stdio, and shut it down -- all within the session timeout."""
    return asyncio.run(
        asyncio.wait_for(
            _within_real_stdio_session(cwd, interaction), STDIO_SESSION_TIMEOUT_SECONDS
        )
    )


def direct_result(case: ToolCase) -> list[dict[str, object]]:
    """The same call made directly on DiagnosticService, serialized normally."""
    return serialize(getattr(build_service(), case.tool)(**case.arguments))


@dataclass(frozen=True)
class ScriptedSession:
    tools: list[Tool]
    successes: dict[str, CallToolResult]
    empty: CallToolResult
    errors: dict[str, CallToolResult]
    repeated: tuple[CallToolResult, CallToolResult]
    naive_time_pair: tuple[CallToolResult, CallToolResult]


async def _run_scripted_session(client: Client) -> ScriptedSession:
    tools = (await client.list_tools()).tools

    successes: dict[str, CallToolResult] = {}
    for case in SUCCESS_CASES:
        successes[case.tool] = await client.call_tool(case.tool, case.arguments)

    empty = await client.call_tool(*EMPTY_CALL)

    errors: dict[str, CallToolResult] = {}
    for label, (tool, arguments) in ERROR_CALLS.items():
        errors[label] = await client.call_tool(tool, arguments)

    first = await client.call_tool(REPEATED_CASE.tool, REPEATED_CASE.arguments)
    second = await client.call_tool(REPEATED_CASE.tool, REPEATED_CASE.arguments)

    naive_time = await client.call_tool(*NAIVE_TIME_CALL)
    utc_time = await client.call_tool(*UTC_TIME_CALL)

    return ScriptedSession(
        tools,
        successes,
        empty,
        errors,
        (first, second),
        (naive_time, utc_time),
    )


@pytest.fixture(scope="module")
def scripted_session() -> ScriptedSession:
    """One real stdio subprocess serving the whole scripted check."""
    return run_over_real_stdio(_run_scripted_session)


def test_discovers_exactly_the_six_tools_over_stdio(scripted_session: ScriptedSession) -> None:
    names = [tool.name for tool in scripted_session.tools]

    assert len(names) == 6
    assert set(names) == EXPECTED_TOOL_NAMES


@pytest.mark.parametrize("case", SUCCESS_CASES, ids=[case.tool for case in SUCCESS_CASES])
def test_tool_call_over_stdio_equals_direct_service_result(
    scripted_session: ScriptedSession, case: ToolCase
) -> None:
    result = scripted_session.successes[case.tool]

    assert result.is_error is False
    records = structured_result(result)
    assert isinstance(records, list) and records
    assert records == direct_result(case)


def test_valid_empty_query_over_stdio_returns_empty_list(
    scripted_session: ScriptedSession,
) -> None:
    result = scripted_session.empty

    assert result.is_error is False
    assert structured_result(result) == []


@pytest.mark.parametrize("label", sorted(ERROR_CALLS))
def test_invalid_call_over_stdio_is_a_tool_error(
    scripted_session: ScriptedSession, label: str
) -> None:
    result = scripted_session.errors[label]

    assert result.is_error is True
    assert result.structured_content is None
    assert result.content, "a tool error should carry an error message"


def test_repeated_call_over_stdio_is_deterministic(scripted_session: ScriptedSession) -> None:
    first, second = scripted_session.repeated

    assert first.is_error is False
    assert first.structured_content == second.structured_content


def test_naive_datetime_over_stdio_is_interpreted_as_utc(
    scripted_session: ScriptedSession,
) -> None:
    naive_result, utc_result = scripted_session.naive_time_pair

    assert naive_result.is_error is False
    assert utc_result.is_error is False

    naive_records = structured_result(naive_result)
    utc_records = structured_result(utc_result)

    assert naive_records != []
    assert naive_records == utc_records


def test_server_subprocess_works_from_a_non_root_cwd(tmp_path: Path) -> None:
    assert not (tmp_path / "evaluation").exists()

    async def query_known_case(client: Client) -> CallToolResult:
        return await client.call_tool(KNOWN_INC_001_QUERY.tool, KNOWN_INC_001_QUERY.arguments)

    result = run_over_real_stdio(query_known_case, cwd=tmp_path)

    assert result.is_error is False
    assert structured_result(result) == direct_result(KNOWN_INC_001_QUERY)


def _running_server_children() -> list[int]:
    """PIDs of live (non-zombie) `python -m tracepilot.mcp` children of this
    test process, read from /proc."""
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        state, parent_pid = stat[stat.rindex(")") + 2 :].split()[:2]
        is_server = b"-m" in argv and SERVER_MODULE.encode() in argv
        if int(parent_pid) == os.getpid() and is_server and state != "Z":
            pids.append(int(entry.name))
    return pids


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="inspects /proc")
def test_server_subprocess_stops_when_the_client_exits() -> None:
    assert _running_server_children() == []

    async def observe_running_server(client: Client) -> list[int]:
        await client.list_tools()
        return _running_server_children()

    during_session = run_over_real_stdio(observe_running_server)

    assert len(during_session) == 1, "expected exactly one live server subprocess"
    assert _running_server_children() == [], "server subprocess outlived its client session"
