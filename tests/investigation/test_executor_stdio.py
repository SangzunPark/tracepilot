"""One stdio smoke test for the executor: the same policy-checked flow over a
real `python -m tracepilot.mcp` subprocess, so the injected case_id and
normalized arguments cross the actual JSON process boundary.

Detailed executor behavior is covered in-memory in test_executor.py; this
spawns exactly one subprocess, bounded by a timeout.
"""

import asyncio
import sys
from datetime import UTC, datetime

import anyio
from mcp import Client, StdioServerParameters, stdio_client
from pydantic import JsonValue

from tests.domain.support import EVALUATION_ROOT
from tests.investigation.executor_support import SpyClient, new_state
from tests.mcp.support import build_service, serialize
from tracepilot.investigation.executor import ToolExecutor, record_tool_call, verify_tool_set
from tracepilot.investigation.models import BlockReason, InvestigationState, ToolRequest

STDIO_SESSION_TIMEOUT_SECONDS = 20.0

# Forbidden case_id, then a permitted call, then the same call with an
# equivalent datetime representation (a duplicate).
ATTEMPTS: list[dict[str, JsonValue]] = [
    {"case_id": "INC-007", "service": "checkout-api"},
    {"service": "checkout-api", "start_time": "2026-01-01T09:00:00+09:00"},
    {"service": "checkout-api", "start_time": "2026-01-01T00:00:00Z"},
]


def test_executor_over_real_stdio() -> None:
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "tracepilot.mcp"], cwd=EVALUATION_ROOT.parent
    )

    async def main() -> tuple[InvestigationState, SpyClient]:
        with anyio.fail_after(STDIO_SESSION_TIMEOUT_SECONDS):
            async with Client(stdio_client(parameters)) as client:
                spy = SpyClient(client)
                await verify_tool_set(spy)
                executor = ToolExecutor(spy)
                state = new_state("INC-014")
                for arguments in ATTEMPTS:
                    request = ToolRequest(tool="search_application_logs", arguments=arguments)
                    state = record_tool_call(state, await executor.attempt(state, request))
                return state, spy

    state, spy = asyncio.run(main())

    forbidden, executed, duplicate = state.tool_calls
    assert forbidden.block_reason is BlockReason.FORBIDDEN_ARGUMENT
    assert duplicate.block_reason is BlockReason.DUPLICATE
    assert executed.observation is not None
    assert list(executed.observation.records) == serialize(
        build_service().search_application_logs(
            "INC-014", "checkout-api", start_time=datetime(2026, 1, 1, tzinfo=UTC)
        )
    )
    assert len(spy.calls) == 1
    [(_, arguments)] = spy.calls
    assert arguments is not None
    assert arguments["case_id"] == "INC-014"
    assert arguments["start_time"] == "2026-01-01T00:00:00Z"
