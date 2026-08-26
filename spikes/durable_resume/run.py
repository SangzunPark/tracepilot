# 이 코드는 4 가지를 검증 / Durability Spike
# 1. langgraph state가 postgre에 저장되는가
# 2. interrupt에서 graph가 안전하게 멈추는가
# 3. 파이썬을 종료했다 다시 실행해도  thread_id로 복구 가능한가
# 4. command(resume)으로 interrupt 이후 workflow 계속 실행 가능한가


# argparse 터미널에서 프로그램에 명령을 전달하기 위해 사용
# langgraph의 state가 어떤 데이터 구조를 가지는지 정의
# PostgresSaver는 langgraph의 실행 상태를 postgre에 저장
# interrupt는 멈춤, command는 다시 시작할때

import argparse
import os
from typing import TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict):
    counter: int
    # approved : None(아직미결정) / True / False
    approved: bool | None


def prepare(state: State) -> dict[str, object]:
    print(">>> prepare node entered")

    return {
        "counter": state["counter"] + 1,
    }

# 이 함수에서 실행이 멈춤(설정) -> 사람에게 승인을 요청하는 구조
def approval(state: State) -> dict[str, object]:
    print(">>> approval node entered")
    print(">>> counter before interrupt:", state["counter"])

    # interrupt 안의 정보는 사람이 보기 위한 것
    decision = interrupt(
        {
            "action": "simulated_rollback_request",
            "message": "Approve this simulated action?",
        }
    )

    print(">>> interrupt returned:", decision)
    # 나중에 resume 이후 실행 
    return {
        "approved": bool(decision),
    }

# 최종 상태를 출력
def finish(state: State) -> dict[str, object]:
    print(">>> finish node entered")
    print(">>> final counter:", state["counter"])
    print(">>> approved:", state["approved"])

    return {}

# 그래프 생성
def build_graph(checkpointer: PostgresSaver):
    builder = StateGraph(State)

    builder.add_node("prepare", prepare)
    builder.add_node("approval", approval)
    builder.add_node("finish", finish)
    # edge 연결
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "approval")
    builder.add_edge("approval", "finish")
    builder.add_edge("finish", END)
# 단순 컴파일이 아닌 workflow 실행 상태를 postgreSQL 체커포인트에 저장
    return builder.compile(checkpointer=checkpointer)


def main() -> None:
    # 터미널 인자를 처리
    parser = argparse.ArgumentParser()

    # command는 positional argument로 start / resume 만 가능
    parser.add_argument(
        "command",
        choices=["start", "resume"],
    )
    # thread-id 부분은 optional arg로 지정 없으면 durable-spike-001 사용
    parser.add_argument(
        "--thread-id",
        default="durable-spike-001",
    )

    args = parser.parse_args()

    database_url = os.environ["DATABASE_URL"]

    # thread id를 통해 복원 실행
    config = {
        "configurable": {
            "thread_id": args.thread_id,
        }
    }

    with PostgresSaver.from_conn_string(database_url) as checkpointer:
        graph = build_graph(checkpointer)

        if args.command == "start":
            result = graph.invoke(
                {
                    "counter": 0,
                    "approved": None,
                },
                config=config,
            )
        # resume 발동 시점
        # interrupt 가 있던 node를 처음부터 다시 실행
        # 재개후 counter=1를 확인하고 실제 state가 복구됨을 확인
        # decision = True 반환
        else:
            result = graph.invoke(
                Command(resume=True),
                config=config,
            )

        print("\nGraph result:")
        print(result)


if __name__ == "__main__":
    main()