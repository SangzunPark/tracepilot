"""Category 3 -- evidence explicitly marked *temporal* must actually precede
the incident's declared start.

Required and mechanistic evidence are deliberately not checked here: an
incident can be legitimately diagnosed using evidence that only makes sense
after the fact (e.g. a log line explaining what happened), so those
categories carry no "must precede the incident" constraint.
"""

from pathlib import Path

from tests.domain.support import load_json, observable_timestamps_by_source_id
from tracepilot.domain.models import GroundTruth
from tracepilot.domain.repository import CaseRepository


def test_temporal_evidence_precedes_incident_start(
    repository: CaseRepository, ground_truth_path: Path
) -> None:
    ground_truth = GroundTruth.model_validate(load_json(ground_truth_path))
    manifest = repository.load_manifest(ground_truth.case_id)
    timestamps = observable_timestamps_by_source_id(repository, ground_truth.case_id)

    for source_id in ground_truth.temporal_evidence_ids:
        assert source_id in timestamps, f"{source_id} is not an observable record"
        assert timestamps[source_id] < manifest.started_at, (
            f"{source_id} at {timestamps[source_id]} does not precede "
            f"incident start {manifest.started_at}"
        )
