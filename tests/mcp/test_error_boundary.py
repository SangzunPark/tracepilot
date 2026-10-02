"""DiagnosticService validation failures surface through the MCP client as
tool execution errors (is_error=True), not as successful structured results.

The specific DiagnosticService exception classes are not required to cross
the client boundary unchanged; only the error signal is checked.
"""

from typing import Any

import pytest

from tests.mcp.support import call_tool

ERROR_CASES: list[tuple[str, str, dict[str, Any]]] = [
    (
        "unknown-service",
        "query_service_metrics",
        {"case_id": "INC-001", "service": "not-a-real-service"},
    ),
    (
        "unknown-provider",
        "get_external_service_status",
        {"case_id": "INC-005", "provider": "not-a-real-provider"},
    ),
    (
        "unknown-runbook-service",
        "search_runbooks",
        {"query": "database", "service": "not-a-real-service"},
    ),
    (
        "unknown-case-metrics",
        "query_service_metrics",
        {"case_id": "INC-DOES-NOT-EXIST", "service": "checkout-api"},
    ),
    (
        "unknown-case-flags",
        "get_feature_flag_history",
        {"case_id": "INC-DOES-NOT-EXIST", "service": "checkout-api"},
    ),
    (
        "invalid-time-range-metrics",
        "query_service_metrics",
        {
            "case_id": "INC-001",
            "service": "checkout-api",
            "start_time": "2026-01-12T15:00:00Z",
            "end_time": "2026-01-12T14:00:00Z",
        },
    ),
    (
        "invalid-time-range-deployments",
        "get_deployment_history",
        {
            "case_id": "INC-001",
            "service": "checkout-api",
            "start_time": "2026-01-12T15:00:00Z",
            "end_time": "2026-01-12T14:00:00Z",
        },
    ),
    (
        "log-limit-below-one",
        "search_application_logs",
        {"case_id": "INC-001", "service": "checkout-api", "limit": 0},
    ),
    (
        "runbook-limit-below-one",
        "search_runbooks",
        {"query": "database", "limit": 0},
    ),
]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [(tool, arguments) for _, tool, arguments in ERROR_CASES],
    ids=[case_id for case_id, _, _ in ERROR_CASES],
)
def test_invalid_call_is_a_tool_execution_error(tool: str, arguments: dict[str, Any]) -> None:
    result = call_tool(tool, arguments)

    assert result.is_error is True
    assert result.structured_content is None
    assert result.content, "a tool error should carry an error message"
