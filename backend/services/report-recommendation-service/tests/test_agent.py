import asyncio
from types import SimpleNamespace

import pytest

from app import agent


def test_report_and_recommendations_use_single_ollama_request(monkeypatch):
    calls = []

    async def fake_generate(prompt, base_url, model_name, response_schema):
        calls.append((base_url, model_name, response_schema))
        assert "evidence-grounded" in prompt
        return agent.AnalysisOutput(scanned_files=1, issues=[], fixes=[], suggestions=[])

    monkeypatch.setattr(agent, "_generate_ollama_json", fake_generate)
    code_report, recommendation_report = asyncio.run(
        agent.generate_report_and_recommendations(
            {"main.py": "def main():\n    return 1\n"},
            ollama_base_url="http://ollama:11434/api/generate",
            ollama_model="qwen2.5-coder:3b",
        )
    )

    assert len(calls) == 1
    assert calls[0][1] == "qwen2.5-coder:3b"
    assert calls[0][2] is agent.AnalysisOutput
    assert calls[0][0] == "http://ollama:11434/api/generate"
    assert code_report == {"scanned_files": 1, "issues": []}
    assert recommendation_report == {"fixes": [], "suggestions": []}


@pytest.mark.parametrize(
    "language,filename",
    [
        ("python", "main.py"),
        ("java", "main.java"),
        ("javascript", "main.js"),
        ("typescript", "main.ts"),
        ("c", "main.c"),
        ("cpp", "main.cpp"),
        ("csharp", "main.cs"),
        ("go", "main.go"),
    ],
)
def test_report_prompt_identifies_each_supported_language(monkeypatch, language, filename):
    prompts = []

    async def fake_generate(prompt, *_args):
        prompts.append(prompt)
        return agent.AnalysisOutput(scanned_files=1, issues=[], fixes=[], suggestions=[])

    monkeypatch.setattr(agent, "_generate_ollama_json", fake_generate)
    asyncio.run(agent.generate_report_and_recommendations(
        {filename: "source code"},
        ollama_base_url="http://ollama:11434/api/generate",
    ))

    assert len(prompts) == 1
    assert f"Review this {language} source" in prompts[0]


def test_ollama_generation_uses_response_json_schema(monkeypatch):
    captured = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, json):
            captured.update(json)

            class Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"response": '{"scanned_files": 1, "issues": [], "fixes": [], "suggestions": []}'}

            return Response()

    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    result = asyncio.run(agent._generate_ollama_json(
        "Analyze files.",
        "http://ollama:11434/api/generate",
        "qwen2.5-coder:3b",
        agent.AnalysisOutput,
    ))

    assert captured["format"] == agent.AnalysisOutput.model_json_schema()
    assert result == agent.AnalysisOutput(scanned_files=1, issues=[], fixes=[], suggestions=[])


def test_ollama_generation_uses_extended_timeout(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            class Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"response": '{"scanned_files": 1, "issues": [], "fixes": [], "suggestions": []}'}

            return Response()

    monkeypatch.setattr(agent.httpx, "AsyncClient", FakeClient)
    asyncio.run(agent._generate_ollama_json(
        "Analyze files.",
        "http://ollama:11434/api/generate",
        "qwen2.5-coder:3b",
        agent.AnalysisOutput,
    ))

    assert captured["timeout"] == 600


def test_gemini_generation_uses_two_typed_requests(monkeypatch):
    from google import genai

    calls = []

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            if kwargs["config"].response_schema is agent.CodeReport:
                return SimpleNamespace(text='{"scanned_files": 1, "issues": []}')
            return SimpleNamespace(text='{"fixes": [], "suggestions": []}')

    class FakeClient:
        def __init__(self, api_key, http_options):
            assert api_key == "test-key"
            assert http_options.timeout == 120_000
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)
    code_report, recommendations = asyncio.run(
        agent.generate_gemini_report_and_recommendations(
            {"main.java": "class Main {}\n"},
            api_key="test-key",
            model_name="gemini-test",
        )
    )

    assert len(calls) == 2
    assert calls[0]["model"] == calls[1]["model"] == "gemini-test"
    assert calls[0]["config"].response_schema is agent.CodeReport
    assert calls[1]["config"].response_schema is agent.RecommendationReport
    assert "Analyze the java project" in calls[0]["contents"]
    assert code_report == {"scanned_files": 1, "issues": []}
    assert recommendations == {"fixes": [], "suggestions": []}