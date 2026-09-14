from pathlib import Path

import pytest

from tests.diagnostics.support import build_ordering_case_root, build_runbook_root
from tests.domain.support import EVALUATION_ROOT
from tracepilot.diagnostics.service import DiagnosticService
from tracepilot.domain.repository import CaseRepository


@pytest.fixture(scope="session")
def repository() -> CaseRepository:
    """A repository over the real evaluation/ fixtures."""
    return CaseRepository(EVALUATION_ROOT)


@pytest.fixture(scope="session")
def service(repository: CaseRepository) -> DiagnosticService:
    return DiagnosticService(repository)


@pytest.fixture
def ordering_service(tmp_path: Path) -> DiagnosticService:
    """A DiagnosticService over an isolated, synthetic case with 2-3 records
    per category written out of chronological order -- for deterministic-
    ordering and multi-value filter checks the real fixtures can't exercise
    with only one record per category."""
    root = build_ordering_case_root(tmp_path)
    return DiagnosticService(CaseRepository(root))


@pytest.fixture
def runbook_service(tmp_path: Path) -> DiagnosticService:
    """A DiagnosticService over isolated, synthetic runbooks -- for search,
    filter, and limit behavior the single real runbook can't exercise."""
    root = build_runbook_root(tmp_path)
    return DiagnosticService(CaseRepository(root))
