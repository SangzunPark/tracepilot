import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from tracepilot.domain.models import (
    DeploymentEvent,
    ExternalStatusEvent,
    FeatureFlagEvent,
    IncidentManifest,
    LogRecord,
    MetricPoint,
    RunbookDocument,
)

ModelT = TypeVar("ModelT", bound=BaseModel)
DEFAULT_EVALUATION_ROOT = Path(__file__).resolve().parents[3] / "evaluation"


class CaseRepository:
    """Reads evaluation case fixtures and validates them into domain models.

    Only reads from `<evaluation_root>/cases/` and `<evaluation_root>/runbooks/`.
    Deliberately has no knowledge of `evaluation/ground_truth/`.
    """

    def __init__(self, evaluation_root: str | Path | None = None) -> None:
        self._root = (
            Path(evaluation_root) if evaluation_root is not None else DEFAULT_EVALUATION_ROOT
        )

    def load_manifest(self, case_id: str) -> IncidentManifest:
        path = self._case_dir(case_id) / "incident.json"
        return IncidentManifest.model_validate(self._read_json(path))

    def load_metrics(self, case_id: str) -> list[MetricPoint]:
        return self._load_array(self._case_dir(case_id) / "metrics.json", MetricPoint)

    def load_logs(self, case_id: str) -> list[LogRecord]:
        return self._load_array(self._case_dir(case_id) / "logs.json", LogRecord)

    def load_deployments(self, case_id: str) -> list[DeploymentEvent]:
        return self._load_array(self._case_dir(case_id) / "deployments.json", DeploymentEvent)

    def load_feature_flags(self, case_id: str) -> list[FeatureFlagEvent]:
        return self._load_array(self._case_dir(case_id) / "feature_flags.json", FeatureFlagEvent)

    def load_external_status(self, case_id: str) -> list[ExternalStatusEvent]:
        return self._load_array(
            self._case_dir(case_id) / "external_status.json", ExternalStatusEvent
        )

    def load_runbooks(self) -> list[RunbookDocument]:
        runbooks_dir = self._root / "runbooks"
        return [
            RunbookDocument.model_validate(self._read_json(path))
            for path in sorted(runbooks_dir.glob("*.json"))
        ]

    def _case_dir(self, case_id: str) -> Path:
        if (
            not case_id
            or case_id in {".", ".."}
            or "/" in case_id
            or "\\" in case_id
            or Path(case_id).is_absolute()
        ):
            raise FileNotFoundError(case_id)

        return self._root / "cases" / case_id

    @staticmethod
    def _read_json(path: Path) -> object:
        return json.loads(path.read_text())

    @classmethod
    def _load_array(cls, path: Path, model: type[ModelT]) -> list[ModelT]:
        items = cls._read_json(path)
        assert isinstance(items, list)
        return [model.model_validate(item) for item in items]
