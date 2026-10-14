"""Test-only evaluation inputs for the Week 4 fairness checks.

Builds runtime-safe inputs from the frozen case data and the preregistration:
frozen case IDs, the IncidentBrief (started_at, affected_services, symptom
only), and a fresh in-memory MCP runtime per run. Nothing here reads
evaluation answers; a reusable harness belongs to later evaluation work.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache
from typing import Any

from mcp import Client
from mcp.types import CallToolResult, ListToolsResult

from tests.domain.support import EVALUATION_ROOT, discover_case_ids
from tests.mcp.support import build_service
from tracepilot.domain.repository import CaseRepository
from tracepilot.evaluation.preregistration import Preregistration, load_preregistration
from tracepilot.investigation.executor import ToolClient, ToolExecutor, verify_tool_set
from tracepilot.investigation.provider import IncidentBrief
from tracepilot.mcp.server import create_mcp_server

PREREGISTRATION_PATH = EVALUATION_ROOT / "preregistration" / "eval-preregistration-v1.yaml"

type RecordedCall = tuple[str, dict[str, Any] | None, float | None]


def frozen_case_ids() -> list[str]:
    return discover_case_ids()


@cache
def preregistration() -> Preregistration:
    return load_preregistration(PREREGISTRATION_PATH, expected_case_ids=frozen_case_ids())


def incident_brief(case_id: str) -> IncidentBrief:
    """The runtime-safe brief: only started_at, affected_services, symptom."""
    manifest = CaseRepository(EVALUATION_ROOT).load_manifest(case_id)
    return IncidentBrief(
        started_at=manifest.started_at,
        affected_services=tuple(manifest.affected_services),
        symptom=manifest.symptom,
    )


class RecordingClient:
    """Delegates to a real MCP client, recording each tool call's name,
    arguments, and read timeout."""

    def __init__(self, client: ToolClient) -> None:
        self.client = client
        self.calls: list[RecordedCall] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> CallToolResult:
        self.calls.append((name, arguments, read_timeout_seconds))
        return await self.client.call_tool(name, arguments, read_timeout_seconds)

    async def list_tools(self, *, cursor: str | None = None) -> ListToolsResult:
        return await self.client.list_tools(cursor=cursor)


@asynccontextmanager
async def fresh_runtime() -> AsyncIterator[tuple[RecordingClient, ToolExecutor]]:
    """A fresh in-memory MCP server over the frozen case data, a verified
    six-tool client, and an executor built from the preregistered limits."""
    shared = preregistration().shared
    async with Client(create_mcp_server(build_service())) as client:
        recording = RecordingClient(client)
        await verify_tool_set(recording)
        yield (
            recording,
            ToolExecutor(
                recording,
                max_steps=shared.max_agent_steps,
                timeout_seconds=shared.tool_timeout_seconds,
            ),
        )
