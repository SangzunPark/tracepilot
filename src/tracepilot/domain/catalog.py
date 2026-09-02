# TracePilot이 조사할 가상의 서비스 구성도를 Python 딕셔너리로 작성한 것
# 각 서비스는 상호의존성(dependencies)를 갖고 있기 때문에 장애가 발생하면
# 이 의존성 부분까지 모두 조사해야 한다.
SERVICE_CATALOG = {
    "checkout-api": {
        "type": "application",
        "dependencies": [
            "authentication-service",
            "postgresql",
            "payment-gateway",
        ],
    },
    "billing-api": {
        "type": "application",
        "dependencies": [
            "postgresql",
            "payment-gateway",
        ],
    },
    "authentication-service": {
        "type": "application",
        "dependencies": [
            "postgresql",
        ],
    },
    "postgresql": {
        "type": "database",
        "dependencies": [],
    },
    "payment-gateway": {
        "type": "external",
        "dependencies": [],
    },
}
