from datetime import datetime

from pydantic import BaseModel, Field

# synthetic incident 데이터의 형식을 정의, data contract


# 하나의 incident 자체를 설명
class IncidentManifest(BaseModel):
    case_id: str
    title: str
    started_at: datetime
    affected_services: list[str]
    symptom: str


# 숫자로 측정한 특정 시점에서의 값
class MetricPoint(BaseModel):
    source_id: str
    service: str
    metric: str
    timestamp: datetime
    value: float
    unit: str


# 로그 메시지
class LogRecord(BaseModel):
    source_id: str
    service: str
    timestamp: datetime
    level: str
    message: str


# 버전 배포 이력
class DeploymentEvent(BaseModel):
    source_id: str
    service: str
    timestamp: datetime
    version: str
    status: str


# FeatureFlag는 코드를 새로 배포하지 않고 특정 기능을 껴거나 끌 수 있는 설정 스위치
# 이 클래스는 FeatureFlag가 변경된 사건 한 건을 의미
class FeatureFlagEvent(BaseModel):
    source_id: str
    service: str
    timestamp: datetime
    flag_name: str
    old_value: str
    new_value: str


# 시스템이 사용하는 외부 서비스의 상태 기록
class ExternalStatusEvent(BaseModel):
    source_id: str
    provider: str
    timestamp: datetime
    status: str
    message: str


# 장애 조사 매뉴얼 문서
class RunbookDocument(BaseModel):
    source_id: str
    service: str | None = None
    title: str
    content: str


class GroundTruth(BaseModel):
    case_id: str
    category: str
    root_cause: str

    required_evidence_ids: list[str] = Field(min_length=1)
    temporal_evidence_ids: list[str] = Field(min_length=1)
    mechanistic_evidence_ids: list[str] = Field(min_length=1)

    acceptable_evidence_sets: list[list[str]]

    leakage_aliases: list[str] = Field(default_factory=list)
