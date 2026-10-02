"""The tracepilot.mcp package contains no orchestration, web framework, LLM,
or agent-planning concerns, and its transport scope is exactly Week 3's:
stdio only, started only from __main__.py.

server.py's own transport independence is checked in test_runtime_boundary.py.
"""

import ast

import tracepilot.mcp
from tests.mcp.static_support import (
    calls,
    code_string_literals,
    import_roots,
    package_trees,
)

FORBIDDEN_IMPORT_ROOTS = {
    "langgraph",
    "langchain",
    "langchain_core",
    "fastapi",
    "starlette",
    "uvicorn",
    "openai",
    "anthropic",
}
AGENT_IDENTIFIER_FRAGMENTS = ("agent", "planner", "llm", "prompt")

# Week 3 is stdio only: these start or expose a non-stdio transport.
NON_STDIO_TRANSPORT_CALLS = {
    "run_sse_async",
    "run_streamable_http_async",
    "sse_app",
    "streamable_http_app",
}
NON_STDIO_LITERAL_FRAGMENTS = ("streamable",)
NON_STDIO_LITERAL_EXACT = {"sse", "http"}

# Calls that would start the MCP server. `.run` on these well-known modules is
# unrelated to the server (e.g. asyncio.run), so it isn't treated as a start.
SERVER_START_CALLS = {"run", "run_stdio_async"}
NON_SERVER_RUN_MODULES = {"asyncio", "anyio", "trio", "subprocess"}

ENTRYPOINT = "__main__.py"
MODULE_TREES = package_trees(tracepilot.mcp)


def _defined_and_imported_identifiers(tree: ast.Module) -> set[str]:
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            identifiers.add(node.name)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            identifiers.update(alias.asname or alias.name for alias in node.names)
    return identifiers


def _server_start_calls(tree: ast.Module) -> list[ast.Call]:
    return [
        call
        for call in calls(tree)
        if isinstance(call.func, ast.Attribute)
        and call.func.attr in SERVER_START_CALLS
        and not (
            isinstance(call.func.value, ast.Name) and call.func.value.id in NON_SERVER_RUN_MODULES
        )
    ]


def _transport_keyword(call: ast.Call) -> object:
    for keyword in call.keywords:
        if keyword.arg == "transport":
            return keyword.value.value if isinstance(keyword.value, ast.Constant) else keyword
    return None


def test_package_is_inspected() -> None:
    assert {"server.py", ENTRYPOINT} <= set(MODULE_TREES)


def test_no_framework_or_llm_imports() -> None:
    for name, tree in MODULE_TREES.items():
        forbidden = FORBIDDEN_IMPORT_ROOTS & import_roots(tree)
        assert not forbidden, f"{name} imports {sorted(forbidden)}"


def test_no_agent_planning_identifiers() -> None:
    for name, tree in MODULE_TREES.items():
        offending = [
            identifier
            for identifier in _defined_and_imported_identifiers(tree)
            if any(fragment in identifier.lower() for fragment in AGENT_IDENTIFIER_FRAGMENTS)
        ]
        assert not offending, f"{name} defines or imports {offending}"


def test_no_http_sse_or_streamable_http_transport() -> None:
    for name, tree in MODULE_TREES.items():
        non_stdio_calls = [
            call.func.attr
            for call in calls(tree)
            if isinstance(call.func, ast.Attribute) and call.func.attr in NON_STDIO_TRANSPORT_CALLS
        ]
        non_stdio_run = [
            transport
            for call in _server_start_calls(tree)
            if (transport := _transport_keyword(call)) not in (None, "stdio")
        ]
        non_stdio_literals = [
            literal
            for literal in code_string_literals(tree)
            if literal.lower() in NON_STDIO_LITERAL_EXACT
            or any(fragment in literal.lower() for fragment in NON_STDIO_LITERAL_FRAGMENTS)
        ]
        assert not non_stdio_calls, f"{name} exposes a non-stdio transport: {non_stdio_calls}"
        assert not non_stdio_run, f"{name} runs a non-stdio transport: {non_stdio_run}"
        assert not non_stdio_literals, (
            f"{name} mentions a non-stdio transport: {non_stdio_literals}"
        )


def test_only_the_entrypoint_starts_the_server() -> None:
    for name, tree in MODULE_TREES.items():
        if name == ENTRYPOINT:
            continue
        starts = [ast.unparse(call) for call in _server_start_calls(tree)]
        assert not starts, f"{name} starts the server outside {ENTRYPOINT}: {starts}"
        assert "__main__" not in code_string_literals(tree), f"{name} has a __main__ entrypoint"


def test_entrypoint_only_creates_and_runs_the_server_over_stdio() -> None:
    """__main__.py may import create_mcp_server, build the server, and call
    server.run() (stdio, the SDK default) -- nothing else."""
    tree = MODULE_TREES[ENTRYPOINT]

    imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)]
    assert len(imports) == 1
    only_import = imports[0]
    assert isinstance(only_import, ast.ImportFrom)
    assert only_import.module == "tracepilot.mcp.server"
    assert [alias.name for alias in only_import.names] == ["create_mcp_server"]

    server_variables = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "create_mcp_server"
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert server_variables, "__main__.py does not build the server with create_mcp_server()"

    starts = _server_start_calls(tree)
    assert len(starts) == 1, "__main__.py should start the server exactly once"
    start = starts[0]
    assert isinstance(start.func, ast.Attribute)
    assert start.func.attr == "run"
    assert isinstance(start.func.value, ast.Name) and start.func.value.id in server_variables
    assert start.args == []
    assert _transport_keyword(start) in (None, "stdio")
    assert all(keyword.arg == "transport" for keyword in start.keywords)

    called = {
        call.func.id if isinstance(call.func, ast.Name) else ast.unparse(call.func)
        for call in calls(tree)
    }
    allowed = {"create_mcp_server", "main", f"{start.func.value.id}.run"}
    assert called <= allowed, f"__main__.py makes unexpected calls: {sorted(called - allowed)}"


def test_entrypoint_runs_main_only_under_dunder_main_guard() -> None:
    tree = MODULE_TREES[ENTRYPOINT]

    guarded_main_calls: list[ast.Expr] = []

    for node in tree.body:
        if not isinstance(node, ast.If):
            continue

        condition = node.test

        is_dunder_main_guard = (
            isinstance(condition, ast.Compare)
            and isinstance(condition.left, ast.Name)
            and condition.left.id == "__name__"
            and len(condition.ops) == 1
            and isinstance(condition.ops[0], ast.Eq)
            and len(condition.comparators) == 1
            and isinstance(condition.comparators[0], ast.Constant)
            and condition.comparators[0].value == "__main__"
        )

        if not is_dunder_main_guard:
            continue

        guarded_main_calls.extend(
            statement
            for statement in node.body
            if isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
            and statement.value.func.id == "main"
        )

    assert len(guarded_main_calls) == 1
