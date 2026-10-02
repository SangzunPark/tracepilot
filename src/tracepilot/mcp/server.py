from datetime import datetime

from mcp.server import MCPServer

from tracepilot.diagnostics.service import DiagnosticService
from tracepilot.domain.models import (
    DeploymentEvent,
    ExternalStatusEvent,
    FeatureFlagEvent,
    LogRecord,
    MetricPoint,
    RunbookDocument,
)


def create_mcp_server(
    diagnostic_service: DiagnosticService | None = None,
) -> MCPServer:
    """Create the TracePilot diagnostic MCP server."""

    service_impl = diagnostic_service if diagnostic_service is not None else DiagnosticService()

    # 괄호 안은 서버이름
    mcp = MCPServer("tracepilot-diagnostics")

    # 이 함수를 등록하라는 의미의 @는 원래 파이썬 기본 문법
    @mcp.tool()
    def query_service_metrics(
        case_id: str,
        service: str,
        metric: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[MetricPoint]:
        """Query metric points for a service in an incident case."""

        return service_impl.query_service_metrics(
            case_id=case_id,
            service=service,
            metric=metric,
            start_time=start_time,
            end_time=end_time,
        )

    @mcp.tool()
    def search_application_logs(
        case_id: str,
        service: str,
        query: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 50,
    ) -> list[LogRecord]:
        """Search application logs for a service in an incident case.

        Matches query as a case-insensitive substring of the log message.
        """

        return service_impl.search_application_logs(
            case_id=case_id,
            service=service,
            query=query,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    @mcp.tool()
    def get_deployment_history(
        case_id: str,
        service: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[DeploymentEvent]:
        """Get deployment events for a service in an incident case."""

        return service_impl.get_deployment_history(
            case_id=case_id,
            service=service,
            start_time=start_time,
            end_time=end_time,
        )

    @mcp.tool()
    def get_feature_flag_history(
        case_id: str,
        service: str,
        flag_name: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[FeatureFlagEvent]:
        """Get feature flag change events for a service in an incident case."""

        return service_impl.get_feature_flag_history(
            case_id=case_id,
            service=service,
            flag_name=flag_name,
            start_time=start_time,
            end_time=end_time,
        )

    @mcp.tool()
    def get_external_service_status(
        case_id: str,
        provider: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[ExternalStatusEvent]:
        """Get status events for an external provider in an incident case."""

        return service_impl.get_external_service_status(
            case_id=case_id,
            provider=provider,
            start_time=start_time,
            end_time=end_time,
        )

    @mcp.tool()
    def search_runbooks(
        query: str,
        service: str | None = None,
        limit: int = 20,
    ) -> list[RunbookDocument]:
        """Search shared operational runbooks by title and content.

        Runbooks are not tied to an incident case.
        """

        return service_impl.search_runbooks(
            query=query,
            service=service,
            limit=limit,
        )

    return mcp
