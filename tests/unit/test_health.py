"""Health endpoint tests."""

from httpx import AsyncClient


async def test_health_check(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "PingStats",
        "version": "0.1.0",
    }


async def test_openapi_and_swagger_are_available(client: AsyncClient) -> None:
    openapi_response = await client.get("/openapi.json")
    docs_response = await client.get("/docs")

    assert openapi_response.status_code == 200
    assert openapi_response.json()["info"]["version"] == "0.1.0"
    assert docs_response.status_code == 200
