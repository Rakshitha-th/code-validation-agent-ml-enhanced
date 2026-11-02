from fastapi.testclient import TestClient
import asyncio

from app.main import app
from app.routes import system


def test_service_health_distinguishes_reachable_agent_from_missing_provider():
    class Response:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"configured": True, "provider": "ollama", "provider_ready": False}

    class Client:
        async def get(self, _url):
            return Response()

    name, status = asyncio.run(system._check_service(Client(), "repair", "http://repair:8000"))

    assert name == "repair"
    assert status == {
        "status": "unavailable",
        "healthy": False,
        "reachable": True,
        "details": {"configured": True, "provider": "ollama", "provider_ready": False},
    }


def test_public_agent_status_is_unauthenticated_and_minimal(monkeypatch):
    async def fake_check_service(client, name, url):
        configured = name != "report_recommendation"
        return name, {
            "status": "healthy",
            "healthy": True,
            "details": {"configured": configured, "service": name},
        }

    monkeypatch.setattr(system, "_check_service", fake_check_service)

    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/system/public-agents")

    assert response.status_code == 200
    assert response.json() == {
        "services": {
            "orchestrator": True,
            "code_quality": True,
            "security": True,
            "bug_resource": True,
            "repair": True,
            "sandbox": True,
            "validation": True,
            "report_recommendation": False,
        },
    }