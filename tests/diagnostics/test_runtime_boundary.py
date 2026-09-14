"""Category 9 -- the diagnostic service layer must have no way to reach
evaluation/ground_truth/, and must reach evaluation data only through
CaseRepository, not by opening files itself.

The checks are AST-based (matching tests/domain/test_runtime_boundary.py's
approach) rather than a plain text search, so that a docstring or comment
explaining the boundary can't trip its own check.
"""

import ast
from pathlib import Path

from tracepilot.diagnostics import service as service_module
from tracepilot.diagnostics.service import DiagnosticService

DocNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module


def _docstring_constants(tree: ast.Module) -> set[ast.Constant]:
    """The Constant node holding each module/class/function docstring, if any."""
    constants: set[ast.Constant] = set()
    for node in [tree, *ast.walk(tree)]:
        if not isinstance(node, DocNode):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            constants.add(first.value)
    return constants


def _references_ground_truth(tree: ast.Module) -> bool:
    docstrings = _docstring_constants(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node in docstrings:
            continue
        if "ground_truth" in node.value:
            return True
    return False


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
    return names


def _direct_file_access_names(tree: ast.Module) -> list[str]:
    """Names of any direct file-opening calls (open(), Path.read_text/bytes,
    json.load/loads) made in the module -- these would bypass CaseRepository."""
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            found.append(func.id)
        elif isinstance(func, ast.Attribute) and func.attr in {
            "read_text",
            "read_bytes",
            "load",
            "loads",
        }:
            found.append(func.attr)
    return found


def _service_module_source() -> ast.Module:
    module_file = service_module.__file__
    assert module_file is not None
    return ast.parse(Path(module_file).resolve().read_text())


def test_service_does_not_import_ground_truth() -> None:
    assert "GroundTruth" not in _imported_names(_service_module_source())


def test_service_does_not_reference_ground_truth_path() -> None:
    assert not _references_ground_truth(_service_module_source())


def test_service_does_not_directly_open_files() -> None:
    assert _direct_file_access_names(_service_module_source()) == []


def test_service_routes_data_access_through_case_repository() -> None:
    assert "CaseRepository" in _imported_names(_service_module_source())


def test_diagnostic_service_has_no_ground_truth_loader() -> None:
    assert not hasattr(DiagnosticService, "load_ground_truth")
