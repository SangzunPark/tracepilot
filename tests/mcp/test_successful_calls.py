"""Each tool succeeds through the in-memory MCP Client and exposes its
result as structured content.

These tests deliberately don't re-derive which records should match --
result correctness is checked against DiagnosticService itself in
test_delegation_equivalence.py.
"""

import pytest

from tests.mcp.support import SUCCESS_CASES, ToolCase, call_tool, structured_result


@pytest.mark.parametrize("case", SUCCESS_CASES, ids=[case.tool for case in SUCCESS_CASES])
def test_tool_call_succeeds_with_structured_result(case: ToolCase) -> None:
    result = call_tool(case.tool, case.arguments)

    assert result.is_error is False
    assert result.structured_content is not None
    assert "result" in result.structured_content

    records = structured_result(result)
    assert isinstance(records, list)
    assert records, f"{case.tool} fixture call unexpectedly returned no records"
    assert all(isinstance(record, dict) and "source_id" in record for record in records)
