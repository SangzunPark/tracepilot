"""Server construction and tool registration, as seen by an MCP client."""

from mcp.server import MCPServer

from tests.mcp.support import list_tools
from tracepilot.mcp.server import create_mcp_server

EXPECTED_PARAMETERS = {
    "query_service_metrics": ["case_id", "service", "metric", "start_time", "end_time"],
    "search_application_logs": [
        "case_id",
        "service",
        "query",
        "start_time",
        "end_time",
        "limit",
    ],
    "get_deployment_history": ["case_id", "service", "start_time", "end_time"],
    "get_feature_flag_history": ["case_id", "service", "flag_name", "start_time", "end_time"],
    "get_external_service_status": ["case_id", "provider", "start_time", "end_time"],
    "search_runbooks": ["query", "service", "limit"],
}

EXPECTED_REQUIRED = {
    "query_service_metrics": {"case_id", "service"},
    "search_application_logs": {"case_id", "service"},
    "get_deployment_history": {"case_id", "service"},
    "get_feature_flag_history": {"case_id", "service"},
    "get_external_service_status": {"case_id", "provider"},
    "search_runbooks": {"query"},
}


def test_create_mcp_server_returns_mcp_server() -> None:
    assert isinstance(create_mcp_server(), MCPServer)


def test_exactly_six_tools_are_registered() -> None:
    assert len(list_tools()) == 6


def test_tool_names_match_exactly() -> None:
    assert {tool.name for tool in list_tools()} == set(EXPECTED_PARAMETERS)


def test_exposed_parameters_match_approved_contracts() -> None:
    exposed = {tool.name: list(tool.input_schema["properties"]) for tool in list_tools()}

    assert exposed == EXPECTED_PARAMETERS


def test_required_parameters_match_approved_contracts() -> None:
    required = {tool.name: set(tool.input_schema.get("required", [])) for tool in list_tools()}

    assert required == EXPECTED_REQUIRED


def test_limit_defaults_are_exposed() -> None:
    schemas = {tool.name: tool.input_schema for tool in list_tools()}

    assert schemas["search_application_logs"]["properties"]["limit"]["default"] == 50
    assert schemas["search_runbooks"]["properties"]["limit"]["default"] == 20


def test_search_runbooks_has_no_case_id_parameter() -> None:
    runbooks = next(tool for tool in list_tools() if tool.name == "search_runbooks")

    assert "case_id" not in runbooks.input_schema["properties"]
