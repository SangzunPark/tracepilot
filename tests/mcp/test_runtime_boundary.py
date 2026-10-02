"""Static runtime boundary of the tracepilot.mcp package.

Every module in the package (server.py, __main__.py, and any module added
later) must stay free of data access, ground-truth access, business logic,
and manual serialization: all of that belongs to DiagnosticService.

server.py additionally must be a set of thin keyword-forwarding wrappers and
must stay transport-independent; starting the server is __main__.py's job
(see test_layer_isolation.py).
"""

import ast

import pytest

import tracepilot.mcp
from tests.mcp.static_support import (
    called_names,
    code_string_literals,
    imported_names,
    package_trees,
)

REPOSITORY_LOADERS = {
    "load_manifest",
    "load_metrics",
    "load_logs",
    "load_deployments",
    "load_feature_flags",
    "load_external_status",
    "load_runbooks",
}
FILE_ACCESS_CALLS = {"open", "read_text", "read_bytes", "load", "loads"}
BUSINESS_LOGIC_CALLS = {"sorted", "sort", "filter", "casefold", "lower", "upper"}
SERIALIZATION_CALLS = {"model_dump", "model_dump_json", "dumps", "dump", "dict", "vars", "asdict"}
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

# server.py keeps the full Session 3.1 transport strictness: it may not start
# any transport (including a bare `.run`) or mention one, stdio included.
SERVER_TRANSPORT_CALLS = {
    "run",
    "run_stdio_async",
    "run_sse_async",
    "run_streamable_http_async",
    "sse_app",
    "streamable_http_app",
}
SERVER_TRANSPORT_LITERAL_FRAGMENTS = ("stdio", "streamable")
SERVER_TRANSPORT_LITERAL_EXACT = {"sse", "http", "__main__"}

MODULE_TREES = package_trees(tracepilot.mcp)
MODULE_NAMES = sorted(MODULE_TREES)


def _server_tree() -> ast.Module:
    return MODULE_TREES["server.py"]


def _is_mcp_tool_decorator(decorator: ast.expr) -> bool:
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "tool"
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "mcp"
    )


def _tool_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(_is_mcp_tool_decorator(decorator) for decorator in node.decorator_list)
    ]


def test_package_modules_are_inspected() -> None:
    assert {"__init__.py", "__main__.py", "server.py"} <= set(MODULE_NAMES)


@pytest.mark.parametrize("module", MODULE_NAMES)
def test_does_not_import_case_repository(module: str) -> None:
    assert "CaseRepository" not in imported_names(MODULE_TREES[module])


@pytest.mark.parametrize("module", MODULE_NAMES)
def test_does_not_call_repository_loaders_directly(module: str) -> None:
    assert not REPOSITORY_LOADERS & called_names(MODULE_TREES[module])


@pytest.mark.parametrize("module", MODULE_NAMES)
def test_does_not_reference_ground_truth(module: str) -> None:
    tree = MODULE_TREES[module]

    assert "GroundTruth" not in imported_names(tree)
    assert not any("ground_truth" in literal for literal in code_string_literals(tree))


@pytest.mark.parametrize("module", MODULE_NAMES)
def test_does_not_reference_evaluation_directory(module: str) -> None:
    assert not any(
        "evaluation" in literal for literal in code_string_literals(MODULE_TREES[module])
    )


@pytest.mark.parametrize("module", MODULE_NAMES)
def test_does_not_open_or_read_files_directly(module: str) -> None:
    tree = MODULE_TREES[module]

    assert "json" not in imported_names(tree)
    assert not FILE_ACCESS_CALLS & called_names(tree)


@pytest.mark.parametrize("module", MODULE_NAMES)
def test_does_not_implement_sorting_filtering_or_search_logic(module: str) -> None:
    tree = MODULE_TREES[module]

    assert not BUSINESS_LOGIC_CALLS & called_names(tree)
    assert not any(isinstance(node, COMPREHENSIONS) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.For | ast.While) for node in ast.walk(tree))


@pytest.mark.parametrize("module", MODULE_NAMES)
def test_does_not_manually_serialize_results(module: str) -> None:
    tree = MODULE_TREES[module]

    assert not SERIALIZATION_CALLS & called_names(tree)
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "__dict__" for node in ast.walk(tree)
    )


def test_server_module_is_transport_independent() -> None:
    tree = _server_tree()

    transport_calls = SERVER_TRANSPORT_CALLS & called_names(tree)
    transport_literals = [
        literal
        for literal in code_string_literals(tree)
        if literal.lower() in SERVER_TRANSPORT_LITERAL_EXACT
        or any(fragment in literal.lower() for fragment in SERVER_TRANSPORT_LITERAL_FRAGMENTS)
    ]

    assert not transport_calls, f"server.py starts a transport: {sorted(transport_calls)}"
    assert not transport_literals, f"server.py mentions a transport: {transport_literals}"


def test_every_tool_is_a_thin_keyword_forwarding_wrapper() -> None:
    """Each @mcp.tool() body is exactly one statement (after its docstring):
    `return service_impl.<same_name>(p=p, ...)`, forwarding every parameter by
    keyword, with no positional arguments and nothing else."""
    tools = _tool_functions(_server_tree())
    assert len(tools) == 6

    for tool in tools:
        body = tool.body
        if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        assert len(body) == 1, f"{tool.name} has more than a single return statement"

        statement = body[0]
        assert isinstance(statement, ast.Return), f"{tool.name} does not return directly"

        call = statement.value
        assert isinstance(call, ast.Call), f"{tool.name} does not return a call"
        assert isinstance(call.func, ast.Attribute)
        assert isinstance(call.func.value, ast.Name)
        assert call.func.value.id == "service_impl", f"{tool.name} bypasses DiagnosticService"
        assert call.func.attr == tool.name, f"{tool.name} delegates to a different method"
        assert call.args == [], f"{tool.name} forwards positional arguments"

        parameters = [argument.arg for argument in tool.args.args]
        forwarded = []
        for keyword in call.keywords:
            assert keyword.arg is not None, f"{tool.name} uses **kwargs forwarding"
            assert isinstance(keyword.value, ast.Name) and keyword.value.id == keyword.arg, (
                f"{tool.name} transforms {keyword.arg} before forwarding"
            )
            forwarded.append(keyword.arg)
        assert forwarded == parameters, f"{tool.name} does not forward every parameter"
