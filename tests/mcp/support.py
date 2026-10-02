"""Shared helpers for driving the TracePilot MCP server through the in-memory
MCP Client.

Async calls are driven with asyncio.run() inside ordinary synchronous tests,
so no async pytest plugin is required.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from mcp import Client
from mcp.types import CallToolResult, Tool
from pydantic import BaseModel

from tests.domain.support import EVALUATION_ROOT
from tracepilot.diagnostics.service import DiagnosticService
from tracepilot.domain.repository import CaseRepository
from tracepilot.mcp.server import create_mcp_server

type ToolCall = tuple[str, dict[str, Any]]


def build_service() -> DiagnosticService:
    """A DiagnosticService over the real evaluation/ fixtures, independent of
    the current working directory."""
    return DiagnosticService(CaseRepository(EVALUATION_ROOT))


async def _call_tools(
    service: DiagnosticService, calls: Sequence[ToolCall]
) -> list[CallToolResult]:
    results: list[CallToolResult] = []
    async with Client(create_mcp_server(service)) as client:
        for name, arguments in calls:
            results.append(await client.call_tool(name, arguments))
    return results


def call_tools(
    calls: Sequence[ToolCall], service: DiagnosticService | None = None
) -> list[CallToolResult]:
    """Run several tool calls, in order, within a single client session."""
    return asyncio.run(_call_tools(service if service is not None else build_service(), calls))


def call_tool(
    name: str, arguments: dict[str, Any], service: DiagnosticService | None = None
) -> CallToolResult:
    return call_tools([(name, arguments)], service)[0]


async def _list_tools() -> list[Tool]:
    async with Client(create_mcp_server(build_service())) as client:
        return (await client.list_tools()).tools


def list_tools() -> list[Tool]:
    return asyncio.run(_list_tools())


def structured_result(result: CallToolResult) -> object:
    """The tool's return value as exposed in structured_content. The MCP SDK
    wraps list return values under a "result" key."""
    assert result.structured_content is not None
    return result.structured_content["result"]


def serialize(records: Sequence[BaseModel]) -> list[dict[str, Any]]:
    """Normal JSON-mode serialization of direct DiagnosticService results, for
    comparison against what the MCP layer exposes."""
    return [record.model_dump(mode="json") for record in records]


@dataclass(frozen=True)
class ToolCase:
    tool: str
    arguments: dict[str, Any]


# One representative successful call per tool, against real fixtures. The
# arguments use the DiagnosticService keyword names, so the same dict can be
# passed to the MCP tool and to the direct DiagnosticService method.
SUCCESS_CASES = [
    ToolCase(
        "query_service_metrics",
        {"case_id": "INC-001", "service": "checkout-api", "metric": "db_connection_pool_usage"},
    ),
    ToolCase(
        "search_application_logs",
        {"case_id": "INC-001", "service": "checkout-api", "query": "database"},
    ),
    ToolCase(
        "get_deployment_history",
        {"case_id": "INC-001", "service": "checkout-api"},
    ),
    ToolCase(
        "get_feature_flag_history",
        {"case_id": "INC-003", "service": "checkout-api", "flag_name": "checkout_express_path"},
    ),
    ToolCase(
        "get_external_service_status",
        {"case_id": "INC-005", "provider": "payment-gateway"},
    ),
    ToolCase(
        "search_runbooks",
        {"query": "connection pool"},
    ),
]
