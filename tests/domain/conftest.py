import pytest

from tests.domain.support import (
    EVALUATION_ROOT,
    discover_agent_visible_json_paths,
    discover_case_ids,
    discover_ground_truth_paths,
)
from tracepilot.domain.repository import CaseRepository

CASE_IDS = discover_case_ids()
GROUND_TRUTH_PATHS = discover_ground_truth_paths()
AGENT_VISIBLE_JSON_PATHS = discover_agent_visible_json_paths()


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Dynamically parametrize tests over whatever cases/ground-truth files
    currently exist on disk, so INC-002..INC-012 are covered automatically."""
    if "case_id" in metafunc.fixturenames:
        metafunc.parametrize("case_id", CASE_IDS)
    if "ground_truth_path" in metafunc.fixturenames:
        metafunc.parametrize(
            "ground_truth_path",
            GROUND_TRUTH_PATHS,
            ids=[path.stem for path in GROUND_TRUTH_PATHS],
        )
    if "agent_visible_json_path" in metafunc.fixturenames:
        metafunc.parametrize(
            "agent_visible_json_path",
            AGENT_VISIBLE_JSON_PATHS,
            ids=[str(path.relative_to(EVALUATION_ROOT)) for path in AGENT_VISIBLE_JSON_PATHS],
        )


@pytest.fixture(scope="session")
def repository() -> CaseRepository:
    return CaseRepository(EVALUATION_ROOT)
