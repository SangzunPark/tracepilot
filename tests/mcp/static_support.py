"""AST helpers shared by the static-inspection tests of the MCP layer and the
runtime packages it reaches.

Docstrings are excluded from string-literal scans, so prose describing a
boundary can't trip the check that enforces it. Comments never reach the AST.
"""

import ast
from pathlib import Path
from types import ModuleType

DocNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module


def package_trees(package: ModuleType) -> dict[str, ast.Module]:
    """Every .py file in a package, keyed by its path relative to the package."""
    package_file = package.__file__
    assert package_file is not None
    package_dir = Path(package_file).parent
    return {
        str(path.relative_to(package_dir)): ast.parse(path.read_text())
        for path in sorted(package_dir.rglob("*.py"))
    }


def docstring_constants(tree: ast.Module) -> set[ast.Constant]:
    constants: set[ast.Constant] = set()
    for node in [tree, *ast.walk(tree)]:
        if isinstance(node, DocNode) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                constants.add(first.value)
    return constants


def code_string_literals(tree: ast.Module) -> list[str]:
    """String literals used in code, excluding docstrings."""
    docstrings = docstring_constants(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docstrings
    ]


def imported_names(tree: ast.Module) -> set[str]:
    """Imported names plus the root package of every import."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
            if node.module is not None:
                names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
    return names


def import_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
    return roots


def calls(tree: ast.Module) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def called_names(tree: ast.Module) -> set[str]:
    """Bare function names and method/attribute names of every call."""
    names: set[str] = set()
    for call in calls(tree):
        if isinstance(call.func, ast.Name):
            names.add(call.func.id)
        elif isinstance(call.func, ast.Attribute):
            names.add(call.func.attr)
    return names
