"""stdout is the stdio MCP protocol wire: anything written to it by code the
server can reach corrupts the JSON-RPC stream. Production modules reachable
from the stdio runtime must not print to stdout; use logging (stderr) instead.
"""

import ast
from types import ModuleType

import pytest

import tracepilot.diagnostics
import tracepilot.domain
import tracepilot.mcp
from tests.mcp.static_support import calls, package_trees

RUNTIME_PACKAGES: list[ModuleType] = [tracepilot.mcp, tracepilot.diagnostics, tracepilot.domain]


def _is_sys_stdout(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "stdout"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _stdout_writes(tree: ast.Module) -> list[str]:
    found: list[str] = []
    for call in calls(tree):
        if isinstance(call.func, ast.Name) and call.func.id == "print":
            target = next((kw.value for kw in call.keywords if kw.arg == "file"), None)
            if target is None or _is_sys_stdout(target):
                found.append(ast.unparse(call))
    found.extend(
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and _is_sys_stdout(node)
    )
    return found


@pytest.mark.parametrize(
    "package", RUNTIME_PACKAGES, ids=[package.__name__ for package in RUNTIME_PACKAGES]
)
def test_runtime_package_does_not_write_to_stdout(package: ModuleType) -> None:
    trees = package_trees(package)
    assert trees, f"{package.__name__} has no modules to inspect"

    offending = {name: writes for name, tree in trees.items() if (writes := _stdout_writes(tree))}

    assert not offending, f"{package.__name__} writes to stdout: {offending}"
