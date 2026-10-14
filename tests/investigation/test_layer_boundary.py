"""The Week 4 data/policy/provider-contract/playbook modules
(investigation/models.py, policies.py, provider.py, and playbook.py) are pure:
no MCP, orchestration, web, database, or provider SDK imports; no route to the
answer key or case files; no I/O, clock, or randomness. The test-side
ScriptedProvider obeys the same rule.

The rule is scoped to those modules, not the whole package: future
siblings such as an MCP executor or a LangGraph graph legitimately use those
dependencies.

Docstrings are excluded from string-literal scans, so prose describing the
boundary can't trip the check that enforces it.
"""

import ast
from pathlib import Path

import pytest

import tracepilot.investigation
from tests.mcp.static_support import (
    called_names,
    code_string_literals,
    import_roots,
    package_trees,
)

FORBIDDEN_IMPORT_ROOTS = {
    "mcp",
    "langgraph",
    "fastapi",
    "psycopg",
    "openai",
    "litellm",
    "langchain",
    "langchain_core",
    "google",
    "mistralai",
    "cohere",
    "anthropic",
    # No I/O, clock, or randomness.
    "os",
    "pathlib",
    "io",
    "shutil",
    "subprocess",
    "socket",
    "random",
    "secrets",
    "time",
    "uuid",
}
FORBIDDEN_MODULES = {
    "tracepilot.mcp",
    "tracepilot.diagnostics",
    "tracepilot.domain.repository",
}
FORBIDDEN_IDENTIFIERS = {
    "GroundTruth",
    "CaseRepository",
    "DEFAULT_EVALUATION_ROOT",
    "load_ground_truth",
}
FORBIDDEN_LITERAL_FRAGMENTS = ("ground_truth", "evaluation", "cases/", ".json")
FORBIDDEN_CALLS = {"open", "now", "utcnow", "today", "time", "read_text", "read_bytes"}

PURE_MODULES = ("models.py", "policies.py", "provider.py", "playbook.py")
MODULE_TREES = package_trees(tracepilot.investigation)
SCRIPTED_PROVIDER = Path(__file__).with_name("scripted_provider.py")


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _identifiers(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.split(".")[-1])
    return names


def _violations(tree: ast.Module) -> list[str]:
    found: list[str] = []
    found += [f"import {root}" for root in import_roots(tree) & FORBIDDEN_IMPORT_ROOTS]
    found += [
        f"import {module}"
        for module in _imported_modules(tree)
        if any(module == banned or module.startswith(f"{banned}.") for banned in FORBIDDEN_MODULES)
    ]
    found += [f"identifier {name}" for name in _identifiers(tree) & FORBIDDEN_IDENTIFIERS]
    found += [
        f"literal {literal!r}"
        for literal in code_string_literals(tree)
        if any(fragment in literal for fragment in FORBIDDEN_LITERAL_FRAGMENTS)
    ]
    found += [f"call {name}" for name in called_names(tree) & FORBIDDEN_CALLS]
    return found


def test_pure_modules_exist() -> None:
    assert set(PURE_MODULES) <= set(MODULE_TREES)


@pytest.mark.parametrize("module", PURE_MODULES)
def test_pure_module_stays_pure(module: str) -> None:
    assert _violations(MODULE_TREES[module]) == []


def test_scripted_provider_stays_pure() -> None:
    assert _violations(ast.parse(SCRIPTED_PROVIDER.read_text())) == []


@pytest.mark.parametrize(
    "source",
    [
        "import mcp",
        "from mcp.types import Tool",
        "import langgraph.graph",
        "from fastapi import FastAPI",
        "import psycopg",
        "from openai import OpenAI",
        "import anthropic",
        "from pathlib import Path",
        "import random",
        "from tracepilot.mcp.server import create_mcp_server",
        "from tracepilot.diagnostics.service import DiagnosticService",
        "from tracepilot.domain.repository import CaseRepository",
        "from tracepilot.domain import repository",
        "from tracepilot.domain.models import GroundTruth",
        "x: GroundTruth",
        "path = 'evaluation/ground_truth/INC-001.json'",
        "key = 'ground_truth'",
        "data = open(name).read()",
        "stamp = datetime.now(UTC)",
    ],
)
def test_checker_detects_planted_violation(source: str) -> None:
    # Guards against a vacuous boundary check.
    assert _violations(ast.parse(source)) != []


def test_docstring_mentioning_boundary_is_allowed() -> None:
    source = '"""Never reads evaluation/ground_truth or GroundTruth files."""\n'
    assert _violations(ast.parse(source)) == []
