"""The local provider-facing argument models must stay aligned with the live
MCP tool schemas, minus the runtime-owned case_id.

This is the only investigation test that needs MCP. Runtime policy does not
derive its models from list_tools; this test is what keeps the two in sync.
"""

from typing import Any

import pytest
from mcp.types import Tool

from tests.mcp.support import list_tools
from tracepilot.investigation.models import TOOL_ARGUMENT_MODELS, TOOL_NAMES, ToolName
from tracepilot.investigation.policies import ALLOWED_TOOLS, CASE_SCOPED_TOOLS

LIVE_TOOLS: dict[str, Tool] = {tool.name: tool for tool in list_tools()}


def _live_properties(tool: ToolName) -> dict[str, Any]:
    properties: dict[str, Any] = LIVE_TOOLS[tool].input_schema["properties"]
    return {name: schema for name, schema in properties.items() if name != "case_id"}


def _live_required(tool: ToolName) -> set[str]:
    return set(LIVE_TOOLS[tool].input_schema.get("required", [])) - {"case_id"}


def test_live_tool_set_is_the_local_allowlist() -> None:
    assert set(LIVE_TOOLS) == ALLOWED_TOOLS == set(TOOL_NAMES)
    assert set(TOOL_ARGUMENT_MODELS) == ALLOWED_TOOLS


def test_case_scoped_tools_are_exactly_those_with_live_case_id() -> None:
    with_case_id = {
        name
        for name, tool in LIVE_TOOLS.items()
        if "case_id" in tool.input_schema["properties"]
        and "case_id" in tool.input_schema.get("required", [])
    }
    assert with_case_id == CASE_SCOPED_TOOLS


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_local_model_has_no_case_id(tool: ToolName) -> None:
    assert "case_id" not in TOOL_ARGUMENT_MODELS[tool].model_fields


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_field_names_match(tool: ToolName) -> None:
    assert set(TOOL_ARGUMENT_MODELS[tool].model_fields) == set(_live_properties(tool))


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_required_fields_match(tool: ToolName) -> None:
    fields = TOOL_ARGUMENT_MODELS[tool].model_fields
    local_required = {name for name, field in fields.items() if field.is_required()}
    assert local_required == _live_required(tool)


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_defaults_match(tool: ToolName) -> None:
    fields = TOOL_ARGUMENT_MODELS[tool].model_fields
    local_defaults = {
        name: field.default for name, field in fields.items() if not field.is_required()
    }
    live_defaults = {
        name: schema["default"]
        for name, schema in _live_properties(tool).items()
        if "default" in schema
    }
    assert local_defaults == live_defaults


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_property_schemas_match(tool: ToolName) -> None:
    # Types and nullability too, not just names and defaults.
    local = TOOL_ARGUMENT_MODELS[tool].model_json_schema()["properties"]
    assert local == _live_properties(tool)


def test_known_defaults() -> None:
    assert _live_properties("search_application_logs")["limit"]["default"] == 50
    assert _live_properties("search_runbooks")["limit"]["default"] == 20
