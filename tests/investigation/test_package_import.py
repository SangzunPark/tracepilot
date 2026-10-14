"""tracepilot/investigation/__init__.py stays lightweight: importing the pure
models/policies/provider path must not pull MCP, LangGraph, or a provider SDK
in through the package.
"""

import ast
import subprocess
import sys
from pathlib import Path

import tracepilot.investigation

INIT_FILE = Path(tracepilot.investigation.__file__)
HEAVY_MODULES = (
    "mcp",
    "langgraph",
    "fastapi",
    "psycopg",
    "openai",
    "anthropic",
    "tracepilot.investigation.executor",
)
IMPORT_TIMEOUT_SECONDS = 20.0


def test_package_init_has_no_imports() -> None:
    tree = ast.parse(INIT_FILE.read_text())
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)]
    assert imports == []


def test_pure_modules_import_without_heavy_dependencies() -> None:
    # A fresh interpreter: this test process has already imported mcp.
    probe = (
        "import sys\n"
        "import tracepilot.investigation.models, tracepilot.investigation.policies\n"
        "import tracepilot.investigation.provider, tracepilot.investigation.playbook\n"
        f"heavy = {HEAVY_MODULES!r}\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in heavy or m in heavy))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        timeout=IMPORT_TIMEOUT_SECONDS,
    )
    assert completed.stdout.strip() == "[]"
