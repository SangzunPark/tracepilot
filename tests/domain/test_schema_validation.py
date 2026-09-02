"""Category 1 -- every fixture file validates against its Pydantic model.

Loading goes through CaseRepository, which calls `model_validate`
internally, so a clean load already proves the schema is honored; an
invalid file would raise before these assertions run. Ground truth has no
runtime loader (by design, see test_runtime_boundary.py), so those files
are read and validated directly.
"""

from pathlib import Path

from tests.domain.support import load_json
from tracepilot.domain.models import (
    DeploymentEvent,
    ExternalStatusEvent,
    FeatureFlagEvent,
    GroundTruth,
    IncidentManifest,
    LogRecord,
    MetricPoint,
    RunbookDocument,
)
from tracepilot.domain.repository import CaseRepository


def test_incident_manifest_validates(repository: CaseRepository, case_id: str) -> None:
    manifest = repository.load_manifest(case_id)
    assert isinstance(manifest, IncidentManifest)
    assert manifest.case_id == case_id


def test_metrics_validate(repository: CaseRepository, case_id: str) -> None:
    metrics = repository.load_metrics(case_id)
    assert all(isinstance(item, MetricPoint) for item in metrics)


def test_logs_validate(repository: CaseRepository, case_id: str) -> None:
    logs = repository.load_logs(case_id)
    assert all(isinstance(item, LogRecord) for item in logs)


def test_deployments_validate(repository: CaseRepository, case_id: str) -> None:
    deployments = repository.load_deployments(case_id)
    assert all(isinstance(item, DeploymentEvent) for item in deployments)


def test_feature_flags_validate(repository: CaseRepository, case_id: str) -> None:
    flags = repository.load_feature_flags(case_id)
    assert all(isinstance(item, FeatureFlagEvent) for item in flags)


def test_external_status_validates(repository: CaseRepository, case_id: str) -> None:
    events = repository.load_external_status(case_id)
    assert all(isinstance(item, ExternalStatusEvent) for item in events)


def test_runbooks_validate(repository: CaseRepository) -> None:
    runbooks = repository.load_runbooks()
    assert all(isinstance(item, RunbookDocument) for item in runbooks)


def test_ground_truth_file_validates(ground_truth_path: Path) -> None:
    ground_truth = GroundTruth.model_validate(load_json(ground_truth_path))
    assert isinstance(ground_truth, GroundTruth)
