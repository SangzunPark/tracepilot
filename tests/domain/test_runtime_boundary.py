"""Category 5 -- the runtime-facing domain layer must have no way to reach
evaluation/ground_truth/, so an agent driven by this code can never read the
answer key, even by accident.

The path-reference check is AST-based rather than a plain text search: a
docstring is allowed to *say* "this deliberately doesn't touch
evaluation/ground_truth" (repository.py does exactly that) without tripping
the check. Only string literals used in actual code -- e.g. building a path
or a dict key -- count as a dependency.
"""

import ast
from pathlib import Path

import tracepilot.domain
from tracepilot.domain.repository import CaseRepository

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


def _code_references_ground_truth(source_path: Path) -> bool:
    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    docstrings = _docstring_constants(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node in docstrings:
            continue
        if "ground_truth" in node.value:
            return True
    return False


def test_case_repository_has_no_ground_truth_loader() -> None:
    assert not hasattr(CaseRepository, "load_ground_truth")


def test_domain_package_does_not_reference_ground_truth_path() -> None:
    domain_file = tracepilot.domain.__file__
    assert domain_file is not None
    domain_dir = Path(domain_file).resolve().parent

    offending = [path for path in domain_dir.rglob("*.py") if _code_references_ground_truth(path)]
    assert not offending, f"domain code references ground_truth: {offending}"
