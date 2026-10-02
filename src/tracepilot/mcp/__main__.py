from tracepilot.mcp.server import create_mcp_server

# MCP 서버를 실제로 실행하고 client 요청을 기다리는 코드
# server.py는 서버를 만드는 코드 / __main__은 서버를 켜는 코드
# server.run()을 server.py 안에(함수 밖에) 두면 테스트에서 import 하는 순간 서버가 켜지며 멈춤


def main() -> None:
    server = create_mcp_server()
    server.run()


if __name__ == "__main__":
    main()
