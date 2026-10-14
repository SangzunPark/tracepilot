"""Client doubles and state builders shared by the executor tests."""

from typing import Any

from mcp.types import CallToolResult, ListToolsResult

from tests.investigation.support import inc015_context, plan
from tracepilot.investigation.executor import ToolClient
from tracepilot.investigation.models import (
    InvestigationMode,
    InvestigationState,
    ToolCallRecord,
)

type Recorded = list[tuple[str, dict[str, Any] | None]]


class SpyClient:
    """Delegates to a real client and records every tools/call."""

    def __init__(self, client: ToolClient) -> None:
        self.client = client
        self.calls: Recorded = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> CallToolResult:
        self.calls.append((name, arguments))
        return await self.client.call_tool(name, arguments, read_timeout_seconds)

    async def list_tools(self, *, cursor: str | None = None) -> ListToolsResult:
        return await self.client.list_tools(cursor=cursor)


class StubClient:
    """Returns a fixed result or raises a fixed exception for every call."""

    def __init__(
        self,
        result: CallToolResult | None = None,
        error: Exception | None = None,
        listings: list[ListToolsResult] | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.listings = listings or []
        self.calls: Recorded = []
        self.cursors: list[str | None] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> CallToolResult:
        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result

    async def list_tools(self, *, cursor: str | None = None) -> ListToolsResult:
        self.cursors.append(cursor)
        return self.listings[len(self.cursors) - 1]


def new_state(
    case_id: str = "INC-014",
    mode: InvestigationMode = InvestigationMode.ADAPTIVE,
    records: tuple[ToolCallRecord, ...] = (),
) -> InvestigationState:
    return InvestigationState(
        case_id=case_id,
        mode=mode,
        initial_context=inc015_context(),
        plan=plan("search_application_logs"),
        plan_version=1,
        tool_calls=records,
    )
