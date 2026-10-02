# CaseRepository(domain/repository)가 파일을 읽는 역할이라면
# DiagnosticService는 읽어온 데이터 중에서 필요한 것만 골라주는 역할

# 시간 범위 다루기 위해 임포트
from datetime import UTC, datetime

# 사용할 에러 목록
from tracepilot.diagnostics.errors import (
    CaseNotFoundError,
    InvalidTimeRangeError,
    UnknownServiceError,
)

# 반환할 서비스 목록
from tracepilot.domain.catalog import SERVICE_CATALOG

# 반환할 데이터 형태
from tracepilot.domain.models import (
    DeploymentEvent,
    ExternalStatusEvent,
    FeatureFlagEvent,
    LogRecord,
    MetricPoint,
    RunbookDocument,
)

# 실제 JSON 파일을 읽어오는 계층
from tracepilot.domain.repository import CaseRepository


# 모든 비교를 UTC 기준으로 맞추는 helper
def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


# 이 파일의 핵심, Incident 조사용 검색 기능 모음
class DiagnosticService:
    # doctring """ 사용, 기본적으로 문자열(str)이나 함수 아래 쓰면 설명문 취급
    """Provides deterministic diagnostic queries over synthetic incident data."""

    # 클래스로부터 실제 객체를 만들 떄 자동으로 한 번 실행되는 초기 설정 함수
    def __init__(self, repository: CaseRepository | None = None) -> None:
        self._repository = repository if repository is not None else CaseRepository()

    # 특정 incident에서 특정 서비스의 metric을 찾아주는 함수
    def query_service_metrics(
        self,
        case_id: str,
        service: str,
        metric: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[MetricPoint]:
        # 검증 부분
        self._validate_case(case_id)
        self._validate_service(service)
        self._validate_time_range(start_time, end_time)
        # metric 읽기
        records = self._repository.load_metrics(case_id)
        # 필요한 부분만 필터링
        results = [
            record
            for record in records
            if record.service == service
            and (metric is None or record.metric == metric)
            and self._in_time_range(record.timestamp, start_time, end_time)
        ]

        return sorted(results, key=lambda record: (record.timestamp, record.source_id))

    # 특정 서비스의 로그에서 원하는 문자열을 검색하는 함수
    def search_application_logs(
        self,
        case_id: str,
        service: str,
        query: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 50,
    ) -> list[LogRecord]:
        self._validate_case(case_id)
        self._validate_service(service)
        self._validate_time_range(start_time, end_time)
        self._validate_limit(limit)

        records = self._repository.load_logs(case_id)
        # casefold는 대소문자를 무시하기 위한 처리
        normalized_query = query.casefold() if query is not None else None

        results = [
            record
            for record in records
            if record.service == service
            and (normalized_query is None or normalized_query in record.message.casefold())
            and self._in_time_range(record.timestamp, start_time, end_time)
        ]

        ordered = sorted(
            results,
            key=lambda record: (record.timestamp, record.source_id),
        )
        return ordered[:limit]

    # 특정 서비스의 배포 기록을 조회
    def get_deployment_history(
        self,
        case_id: str,
        service: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[DeploymentEvent]:
        self._validate_case(case_id)
        self._validate_service(service)
        self._validate_time_range(start_time, end_time)

        records = self._repository.load_deployments(case_id)

        results = [
            record
            for record in records
            if record.service == service
            and self._in_time_range(record.timestamp, start_time, end_time)
        ]

        return sorted(results, key=lambda record: (record.timestamp, record.source_id))

    # 특정 서비스의 feature flag 변경 이력 조회
    def get_feature_flag_history(
        self,
        case_id: str,
        service: str,
        flag_name: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[FeatureFlagEvent]:
        self._validate_case(case_id)
        self._validate_service(service)
        self._validate_time_range(start_time, end_time)

        records = self._repository.load_feature_flags(case_id)

        results = [
            record
            for record in records
            if record.service == service
            and (flag_name is None or record.flag_name == flag_name)
            and self._in_time_range(record.timestamp, start_time, end_time)
        ]

        return sorted(results, key=lambda record: (record.timestamp, record.source_id))

    # 외부 서비스 상태를 확인
    def get_external_service_status(
        self,
        case_id: str,
        provider: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[ExternalStatusEvent]:
        self._validate_case(case_id)
        self._validate_service(provider)
        self._validate_time_range(start_time, end_time)

        records = self._repository.load_external_status(case_id)

        results = [
            record
            for record in records
            if record.provider == provider
            and self._in_time_range(record.timestamp, start_time, end_time)
        ]

        return sorted(results, key=lambda record: (record.timestamp, record.source_id))

    # 운영 매뉴얼 검색
    def search_runbooks(
        self,
        query: str,
        service: str | None = None,
        limit: int = 20,
    ) -> list[RunbookDocument]:
        if service is not None:
            self._validate_service(service)
        self._validate_limit(limit)

        records = self._repository.load_runbooks()
        normalized_query = query.casefold()

        results = [
            record
            for record in records
            if (service is None or record.service == service)
            and normalized_query in f"{record.title} {record.content}".casefold()
        ]

        ordered = sorted(results, key=lambda record: record.source_id)
        return ordered[:limit]

    # 내부용 helper 함수, 요청한 incident가 실제로 존재하는지 확인
    def _validate_case(self, case_id: str) -> None:
        try:
            self._repository.load_manifest(case_id)
        except FileNotFoundError as exc:
            raise CaseNotFoundError(case_id) from exc

    # 서비스가 실제 catalog에 있는지 검사
    @staticmethod
    def _validate_service(service: str) -> None:
        if service not in SERVICE_CATALOG:
            raise UnknownServiceError(service)

    # 시간 입력이 정상인지 확인
    @staticmethod
    def _validate_time_range(
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> None:
        normalized_start = _as_utc(start_time) if start_time is not None else None
        normalized_end = _as_utc(end_time) if end_time is not None else None

        if (
            normalized_start is not None
            and normalized_end is not None
            and normalized_start > normalized_end
        ):
            raise InvalidTimeRangeError(f"start_time {start_time!r} is after end_time {end_time!r}")

    # limit이 정상인지 검사
    @staticmethod
    def _validate_limit(limit: int) -> None:
        if limit < 1:
            raise ValueError("limit must be greater than or equal to 1")

    # 기록이 요청한 시간 범위 안에 있는지 확인
    @staticmethod
    def _in_time_range(
        timestamp: datetime,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> bool:
        normalized_timestamp = _as_utc(timestamp)
        normalized_start = _as_utc(start_time) if start_time is not None else None
        normalized_end = _as_utc(end_time) if end_time is not None else None

        if normalized_start is not None and normalized_timestamp < normalized_start:
            return False

        if normalized_end is not None and normalized_timestamp > normalized_end:
            return False

        return True
