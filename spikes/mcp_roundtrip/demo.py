import asyncio

from mcp import Client
from mcp.server import MCPServer

# 빈 서버 만들기
mcp = MCPServer(
    "TracePilot Diagnostic Spike",
    version="0.1.0",
)

# tool 등록 파트
# @mcp.tool()를 사용해서 일반 파이썬 함수를 MCP tool로 변환
@mcp.tool(title="Get service status")
# service: str는 이 도구를 사용할때 문자열 데이터만 넣어서 호출할 것 명시
# 예: {"service": "database"}
def get_service_status(service: str) -> dict[str, str]:
    # 이 tool의 description 부분 doctoring
    # MCP는 오직 이 doctoring 만 읽는다
    """Return the synthetic status of a known service."""

    if service != "checkout-api":
        raise ValueError(f"Unknown service: {service}")

    return {
        "service": service,
        "status": "healthy",
    }

# async는 함수 안에 기다림이 필요한 작업이 있다고 알림
# 네트워크 속도와 하드웨어 속도는 큰 차이가 있기 때문에
# await을 통해 다른 작업을 기다리는 동안 진행
async def main() -> None:
    # mcp 서버 연결 및 클라이언트 연결
    async with Client(mcp) as client:
        print("Protocol version:", client.protocol_version)
        print("Server info:", client.server_info)

        # 서버에 어떤 도구가 있는지 조회
        # await, 서버가 응답할때 까지 다른 작업 진행 지시
        tools_result = await client.list_tools()

        print("\nDiscovered tools:")

        for tool in tools_result.tools:
            print("Name:", tool.name)
            print("Title:", tool.title)
            print("Description:", tool.description)
            print("Input schema:", tool.input_schema)
            print("Output schema:", tool.output_schema)
        
        # 도구 호출, 성공의 경우 
        success = await client.call_tool(
            "get_service_status",
            {"service": "checkout-api"},
        )

        print("\nSuccessful call:")
        print("is_error:", success.is_error)
        print("structured_content:", success.structured_content)

        # 도구 호출, 실패의 경우
        failure = await client.call_tool(
            "get_service_status",
            {"service": "unknown-service"},
        )

        print("\nDomain failure:")
        print("is_error:", failure.is_error)
        print("content:", failure.content)

# import 한 것이 아니라 해당 파일을 실행하는 경우 코드를 실행
if __name__ == "__main__":
    asyncio.run(main())