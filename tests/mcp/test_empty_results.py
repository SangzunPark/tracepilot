"""A valid query with no matching records is a successful call returning an
empty list -- never a tool error."""

from typing import Any

import pytest

from tests.mcp.support import call_tool, structured_result

EMPTY_CASES: list[tuple[str, str, dict[str, Any]]] = [
    (
        "no-matching-metric",
        "query_service_metrics",
        {"case_id": "INC-001", "service": "checkout-api", "metric": "nonexistent_metric"},
    ),
    (
        "case-with-no-deployments",
        "get_deployment_history",
        {"case_id": "INC-004", "service": "checkout-api"},
    ),
    (
        "no-matching-runbook",
        "search_runbooks",
        {"query": "no-such-term-anywhere"},
    ),
]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [(tool, arguments) for _, tool, arguments in EMPTY_CASES],
    ids=[case_id for case_id, _, _ in EMPTY_CASES],
)
def test_valid_no_match_query_returns_empty_list(tool: str, arguments: dict[str, Any]) -> None:
    result = call_tool(tool, arguments)

    assert result.is_error is False
    assert structured_result(result) == []
