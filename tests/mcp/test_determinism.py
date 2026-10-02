"""Calling a tool twice with identical arguments returns equal structured
results -- both within one client session and across fresh sessions."""

import pytest

from tests.mcp.support import SUCCESS_CASES, ToolCase, call_tool, call_tools


@pytest.mark.parametrize("case", SUCCESS_CASES, ids=[case.tool for case in SUCCESS_CASES])
def test_repeated_call_in_one_session_is_deterministic(case: ToolCase) -> None:
    first, second = call_tools([(case.tool, case.arguments), (case.tool, case.arguments)])

    assert first.is_error is False
    assert first.structured_content == second.structured_content


def test_repeated_call_across_sessions_is_deterministic() -> None:
    arguments = {"case_id": "INC-001", "service": "checkout-api"}

    first = call_tool("query_service_metrics", arguments)
    second = call_tool("query_service_metrics", arguments)

    assert first.is_error is False
    assert first.structured_content == second.structured_content
