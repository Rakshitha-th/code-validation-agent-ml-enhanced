from fastapi.testclient import TestClient
import httpx
import pytest

from app import main


def test_analyze_uses_ollama(monkeypatch):
    monkeypatch.setenv("REPORT_RECOMMENDATION_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434/api/generate")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

    async def generate(files, ollama_base_url, ollama_model=None):
        assert files == {"main.py": "print(1)\n"}
        assert ollama_base_url == "http://ollama:11434/api/generate"
        assert ollama_model == "qwen2.5-coder:3b"
        return {"scanned_files": 1, "issues": []}, {"fixes": [], "suggestions": []}

    monkeypatch.setattr(main, "generate_report_and_recommendations", generate)
    client = TestClient(main.app)

    response = client.post("/analyze", json={"files": {"main.py": "print(1)\n"}})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["provider"] == "ollama"
    assert client.get("/health").json()["configured"] is True


def test_analyze_skips_gemini_when_api_key_is_missing(monkeypatch):
    monkeypatch.setenv("REPORT_RECOMMENDATION_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    response = TestClient(main.app).post("/analyze", json={"files": {"main.py": "print(1)\n"}})

    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    assert "Gemini API key" in response.json()["message"]
    assert TestClient(main.app).get("/health").json()["provider_ready"] is False


def test_analyze_uses_gemini_when_selected(monkeypatch):
    monkeypatch.setenv("REPORT_RECOMMENDATION_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")

    async def generate(files, api_key, model_name=None):
        assert files == {"main.py": "print(1)\n"}
        assert api_key == "test-key"
        assert model_name == "gemini-test"
        return {"scanned_files": 1, "issues": []}, {"fixes": [], "suggestions": []}

    monkeypatch.setattr(main, "generate_gemini_report_and_recommendations", generate)
    client = TestClient(main.app)

    response = client.post("/analyze", json={"files": {"main.py": "print(1)\n"}})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["provider"] == "gemini"
    assert client.get("/health").json()["provider_ready"] is True


@pytest.mark.parametrize(
    "filename,source",
    [
        ("main.java", "class Main {}\n"),
        ("main.js", "const ready = true;\n"),
        ("main.ts", "const ready: boolean = true;\n"),
        ("main.c", "int main(void) { return 0; }\n"),
        ("main.cpp", "int main() { return 0; }\n"),
        ("main.cs", "class Main {}\n"),
        ("main.go", "package main\n"),
    ],
)
def test_analyze_accepts_supported_non_python_source(monkeypatch, filename, source):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434/api/generate")

    async def generate(files, *_args, **_kwargs):
        assert files == {filename: source}
        return {"scanned_files": 1, "issues": []}, {"fixes": [], "suggestions": []}

    monkeypatch.setattr(main, "generate_report_and_recommendations", generate)
    response = TestClient(main.app).post(
        "/analyze",
        json={"files": {filename: source}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_analyze_rejects_mixed_languages(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434/api/generate")
    response = TestClient(main.app).post(
        "/analyze",
        json={"files": {"main.py": "print('ok')", "main.java": "class Main {}"}},
    )

    assert response.status_code == 422
    assert "one programming language" in response.text


def test_analyze_reports_safe_timeout_reason(monkeypatch):
    async def timeout(*_args, **_kwargs):
        raise httpx.ReadTimeout("")

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434/api/generate")
    monkeypatch.setattr(main, "generate_report_and_recommendations", timeout)

    response = TestClient(main.app).post("/analyze", json={"files": {"main.py": "print(1)"}})

    assert response.status_code == 502
    assert "timed out" in response.json()["detail"]


def test_health_does_not_confuse_configured_url_with_available_model(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434/api/generate")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

    class OllamaResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "different-model:latest"}]}

    class OllamaClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            assert url == "http://ollama:11434/api/tags"
            return OllamaResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_kwargs: OllamaClient())

    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["provider_ready"] is False
    assert "not installed" in response.json()["provider_error"]