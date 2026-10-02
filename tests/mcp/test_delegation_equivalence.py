"""MCP result == DiagnosticService result.

Each MCP tool's structured result is compared with the direct
DiagnosticService result for the same arguments, after normal JSON-mode
serialization. The same service instance backs both sides.
"""

from datetime import datetime

import pytest

from tests.mcp.support import (
    SUCCESS_CASES,
    ToolCase,
    build_service,
    call_tool,
    serialize,
    structured_result,
)


@pytest.mark.parametrize("case", SUCCESS_CASES, ids=[case.tool for case in SUCCESS_CASES])
def test_mcp_result_equals_direct_service_result(case: ToolCase) -> None:
    service = build_service()

    mcp_result = call_tool(case.tool, case.arguments, service)
    direct_result = getattr(service, case.tool)(**case.arguments)

    assert structured_result(mcp_result) == serialize(direct_result)


def test_time_range_and_limit_arguments_are_forwarded() -> None:
    service = build_service()

    mcp_result = call_tool(
        "search_application_logs",
        {
            "case_id": "INC-001",
            "service": "checkout-api",
            "start_time": "2026-01-12T14:06:00Z",
            "end_time": "2026-01-12T14:14:00Z",
            "limit": 2,
        },
        service,
    )
    direct_result = service.search_application_logs(
        case_id="INC-001",
        service="checkout-api",
        start_time=datetime.fromisoformat("2026-01-12T14:06:00+00:00"),
        end_time=datetime.fromisoformat("2026-01-12T14:14:00+00:00"),
        limit=2,
    )

    assert structured_result(mcp_result) == serialize(direct_result)
