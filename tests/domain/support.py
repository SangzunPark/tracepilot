"""Shared discovery helpers for the Week 2 synthetic-case validation tests.

Pure functions and path constants only -- no test functions live here.
"""

import json
from datetime import datetime
from pathlib import Path

from tracepilot.domain.models import (
    DeploymentEvent,
    ExternalStatusEvent,
    FeatureFlagEvent,
    LogRecord,
    MetricPoint,
)
from tracepilot.domain.repository import CaseRepository

EVALUATION_ROOT = Path(__file__).resolve().parents[2] / "evaluation"
CASES_ROOT = EVALUATION_ROOT / "cases"
RUNBOOKS_ROOT = EVALUATION_ROOT / "runbooks"
GROUND_TRUTH_ROOT = EVALUATION_ROOT / "ground_truth"

# Keys that only ever belong in evaluation/ground_truth/ files. If any of
# these show up in case fixtures or shared runbooks, the answer has leaked
# into data an agent under evaluation can read.
FORBIDDEN_GROUND_TRUTH_KEYS = frozenset(
    {
        "root_cause",
        "required_evidence_ids",
        "temporal_evidence_ids",
        "mechanistic_evidence_ids",
        "acceptable_evidence_sets",
        "leakage_aliases",
    }
)

type ObservableRecord = (
    MetricPoint | LogRecord | DeploymentEvent | FeatureFlagEvent | ExternalStatusEvent
)


def discover_case_ids() -> list[str]:
    """Case ids are read from disk so INC-002..INC-012 are picked up
    automatically as they are added, without editing these tests."""
    return sorted(path.name for path in CASES_ROOT.iterdir() if path.is_dir())


def discover_ground_truth_paths() -> list[Path]:
    return sorted(GROUND_TRUTH_ROOT.glob("*.json"))


def discover_agent_visible_json_paths() -> list[Path]:
    """Every JSON file an agent under evaluation can read: case fixtures and
    shared runbooks. Deliberately excludes evaluation/ground_truth/."""
    return sorted(CASES_ROOT.rglob("*.json")) + sorted(RUNBOOKS_ROOT.glob("*.json"))


def load_json(path: Path) -> object:
    return json.loads(path.read_text())


def find_forbidden_keys(value: object, location: str = "$") -> list[str]:
    """Recursively locate ground-truth-only keys inside a decoded JSON value."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, sub_value in value.items():
            key_location = f"{location}.{key}"
            if key in FORBIDDEN_GROUND_TRUTH_KEYS:
                found.append(key_location)
            found.extend(find_forbidden_keys(sub_value, key_location))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_forbidden_keys(item, f"{location}[{index}]"))
    return found


def observable_records(repository: CaseRepository, case_id: str) -> list[ObservableRecord]:
    """Every timestamped, source_id-bearing record observable for a case."""
    records: list[ObservableRecord] = []
    records.extend(repository.load_metrics(case_id))
    records.extend(repository.load_logs(case_id))
    records.extend(repository.load_deployments(case_id))
    records.extend(repository.load_feature_flags(case_id))
    records.extend(repository.load_external_status(case_id))
    return records


def observable_source_id_list(repository: CaseRepository, case_id: str) -> list[str]:
    return [record.source_id for record in observable_records(repository, case_id)]


def observable_timestamps_by_source_id(
    repository: CaseRepository, case_id: str
) -> dict[str, datetime]:
    return {
        record.source_id: record.timestamp for record in observable_records(repository, case_id)
    }


def case_text_blob(case_id: str) -> str:
    """Lower-cased text of every JSON file in one case's directory, for
    verbatim alias checks."""
    case_dir = CASES_ROOT / case_id
    return "\n".join(path.read_text() for path in sorted(case_dir.glob("*.json"))).lower()
