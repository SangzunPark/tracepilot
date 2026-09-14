"""Category 2 -- ground-truth evidence ids must resolve against records an
agent working the case could actually observe, and each case's identity
must line up between its ground truth and its fixture directory."""

from collections import Counter
from pathlib import Path

from tests.domain.support import CASES_ROOT, load_json, observable_source_id_list
from tracepilot.domain.models import GroundTruth
from tracepilot.domain.repository import CaseRepository


def _load_ground_truth(path: Path) -> GroundTruth:
    return GroundTruth.model_validate(load_json(path))


def test_required_evidence_ids_exist_in_observable_data(
    repository: CaseRepository, ground_truth_path: Path
) -> None:
    ground_truth = _load_ground_truth(ground_truth_path)
    observable_ids = set(observable_source_id_list(repository, ground_truth.case_id))
    missing = set(ground_truth.required_evidence_ids) - observable_ids
    assert not missing, f"required evidence ids not observable: {sorted(missing)}"


def test_temporal_evidence_ids_exist_in_observable_data(
    repository: CaseRepository, ground_truth_path: Path
) -> None:
    ground_truth = _load_ground_truth(ground_truth_path)
    observable_ids = set(observable_source_id_list(repository, ground_truth.case_id))
    missing = set(ground_truth.temporal_evidence_ids) - observable_ids
    assert not missing, f"temporal evidence ids not observable: {sorted(missing)}"


def test_mechanistic_evidence_ids_exist_in_observable_data(
    repository: CaseRepository, ground_truth_path: Path
) -> None:
    ground_truth = _load_ground_truth(ground_truth_path)
    observable_ids = set(observable_source_id_list(repository, ground_truth.case_id))
    missing = set(ground_truth.mechanistic_evidence_ids) - observable_ids
    assert not missing, f"mechanistic evidence ids not observable: {sorted(missing)}"


def test_source_ids_are_unique_within_case(repository: CaseRepository, case_id: str) -> None:
    counts = Counter(observable_source_id_list(repository, case_id))
    duplicates = {source_id for source_id, count in counts.items() if count > 1}
    assert not duplicates, f"duplicate source_id values in {case_id}: {sorted(duplicates)}"


def test_ground_truth_case_id_matches_case_directory(ground_truth_path: Path) -> None:
    ground_truth = _load_ground_truth(ground_truth_path)
    assert ground_truth_path.stem == ground_truth.case_id
    assert (CASES_ROOT / ground_truth.case_id).is_dir()
