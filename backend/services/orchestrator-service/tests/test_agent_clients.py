import asyncio
import httpx
import pytest

from app.services import quality_client, repair_client, sandbox_client, security_client, validation_client


class StubResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class StubAsyncClient:
    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, json):
        self.requests.append((url, json))
        return StubResponse(self.handler(url, json))


def test_quality_client_uses_quality_specific_timeout(monkeypatch):
    client = StubAsyncClient(lambda _url, _body: {"findings": []})
    client_options = []

    def fake_async_client(**kwargs):
        client_options.append(kwargs)
        return client

    monkeypatch.setattr(quality_client.httpx, "AsyncClient", fake_async_client)

    result = asyncio.run(quality_client.analyze("p1", {"main.java": "class Main {}\n"}))

    assert result == {"findings": []}
    assert client_options == [{"timeout": quality_client.settings.quality_call_timeout_seconds}]


def test_security_client_sends_project_files_and_adapts_project_scan_response(monkeypatch):
    client = StubAsyncClient(lambda _url, _body: {
        "status": "completed",
        "findings": [{"file": "src/main.py", "type": "secret", "severity": "high", "line": 4, "description": "Secret found"}]
    })
    client_options = []

    def fake_async_client(**kwargs):
        client_options.append(kwargs)
        return client

    monkeypatch.setattr(security_client.httpx, "AsyncClient", fake_async_client)

    project_files = {
        "src/main.py": "token = 'x'",
        "package.json": "{}",
        "package-lock.json": "{}",
    }
    result = asyncio.run(security_client.analyze("p1", project_files))

    assert client.requests[0][0].endswith("/scan-project")
    assert client.requests[0][1] == {"project_id": "p1", "files": project_files}
    assert result["findings"][0]["file"] == "src/main.py"
    assert result["findings"][0]["category"] == "security"
    assert result["findings"][0]["line_number"] == 4
    assert client_options == [{"timeout": security_client.settings.security_call_timeout_seconds}]


def test_sandbox_client_runs_main_file_and_reports_failed_execution(monkeypatch):
    client = StubAsyncClient(lambda _url, _body: {
        "status": "failed", "exit_code": 1, "stderr": "Traceback"
    })
    monkeypatch.setattr(sandbox_client.httpx, "AsyncClient", lambda **_kwargs: client)

    result = asyncio.run(sandbox_client.execute("p1", {
        "helper.py": "pass", "src/main.py": "raise RuntimeError()"
    }, "test input\n"))

    assert client.requests[0][0].endswith("/execute")
    assert client.requests[0][1]["filename"] == "src/main.py"
    assert client.requests[0][1]["files"] == {
        "helper.py": "pass",
        "src/main.py": "raise RuntimeError()",
    }
    assert client.requests[0][1]["stdin"] == "test input\n"
    assert result["executed"] is True
    assert result["passed"] is False
    assert result["error"] == "Traceback"


def test_sandbox_client_forwards_expected_output_test_cases(monkeypatch):
    client = StubAsyncClient(lambda _url, _body: {
        "status": "completed",
        "executed": True,
        "passed_execution": True,
        "comparison_status": "passed",
        "exit_code": 0,
        "results": [{"test_case": 1, "expected": "ok", "actual": "ok", "comparison_status": "passed"}],
    })
    monkeypatch.setattr(sandbox_client.httpx, "AsyncClient", lambda **_kwargs: client)
    test_cases = [{"input": "value\n", "expected": "ok"}]

    result = asyncio.run(sandbox_client.execute(
        "p1",
        {"main.go": "package main\n"},
        test_cases=test_cases,
    ))

    assert client.requests[0][1]["test_cases"] == test_cases
    assert result["passed"] is True
    assert result["results"][0]["comparison_status"] == "passed"


def test_sandbox_client_preserves_warnings_without_failing_successful_execution(monkeypatch):
    client = StubAsyncClient(lambda _url, _body: {
        "status": "completed",
        "executed": True,
        "exit_code": 0,
        "stdout": "",
        "stderr": "compiler warning: unused variable",
    })
    monkeypatch.setattr(sandbox_client.httpx, "AsyncClient", lambda **_kwargs: client)

    result = asyncio.run(sandbox_client.execute("p1", {"main.c": "int main(void) { return 0; }\n"}, validation_only=True))

    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert result["stderr"] == "compiler warning: unused variable"
    assert result["executed"] is True
    assert result["passed"] is True


def test_validation_client_maps_validity_and_findings(monkeypatch):
    client = StubAsyncClient(lambda _url, _body: {
        "valid": False,
        "status": "failed",
        "findings": [{"type": "syntax", "severity": "critical", "line": 2, "message": "Invalid syntax"}],
    })
    monkeypatch.setattr(validation_client.httpx, "AsyncClient", lambda **_kwargs: client)

    result = asyncio.run(validation_client.validate("p1", {"main.py": "if:"}, []))

    assert client.requests[0][0].endswith("/validate")
    assert result["passed"] is False
    assert result["error"] is None
    assert result["findings"][0]["category"] == "code_quality"
    assert result["findings"][0]["severity"] == "critical"


def test_validation_client_preserves_nonblocking_review_status(monkeypatch):
    client = StubAsyncClient(lambda _url, _body: {
        "valid": True,
        "status": "needs_review",
        "needs_review": True,
        "findings": [{"type": "CodeQuality", "severity": "low", "line": 1, "message": "Style advice", "code": "F401"}],
    })
    monkeypatch.setattr(validation_client.httpx, "AsyncClient", lambda **_kwargs: client)

    result = asyncio.run(validation_client.validate("p1", {"main.py": "import os\n"}, []))

    assert result["passed"] is True
    assert result["status"] == "needs_review"
    assert result["findings_count"] == 1


def test_validation_client_reports_unavailable_service_as_error(monkeypatch):
    class TimeoutClient(StubAsyncClient):
        async def post(self, _url, json):
            raise httpx.ReadTimeout("")

    monkeypatch.setattr(validation_client.httpx, "AsyncClient", lambda **_kwargs: TimeoutClient(lambda *_args: {}))
    result = asyncio.run(validation_client.validate("p1", {"main.py": "print('ok')"}, []))

    assert result["status"] == "error"
    assert result["passed"] is False
    assert "validation service request failed" in result["error"]


def test_validation_client_rejects_malformed_success_response(monkeypatch):
    client = StubAsyncClient(lambda _url, _body: {"findings": []})
    monkeypatch.setattr(validation_client.httpx, "AsyncClient", lambda **_kwargs: client)

    result = asyncio.run(validation_client.validate("p1", {"main.py": "print('ok')"}, []))

    assert result["status"] == "error"
    assert result["passed"] is False
    assert "invalid response" in result["error"]


def test_validation_client_rejects_missing_response_status(monkeypatch):
    client = StubAsyncClient(lambda _url, _body: {
        "valid": True,
        "findings": [],
    })
    monkeypatch.setattr(validation_client.httpx, "AsyncClient", lambda **_kwargs: client)

    result = asyncio.run(validation_client.validate("p1", {"main.py": "print('ok')"}, []))

    assert result["status"] == "error"
    assert result["passed"] is False
    assert "invalid response" in result["error"]


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        ("Main.java", "class Main {}\n"),
        ("main.js", 'console.log("ok");\n'),
        ("main.ts", 'const value: number = 1;\n'),
        ("main.c", "int main(void) { return 0; }\n"),
        ("main.cpp", "int main() { return 0; }\n"),
        ("Program.cs", "class Program {}\n"),
        ("main.go", "package main\nfunc main() {}\n"),
    ],
)
def test_validation_client_uses_sandbox_for_non_python_without_python_endpoint(
    monkeypatch, filename, source
):
    sandbox_result = {
        "status": "completed",
        "executed": True,
        "passed": True,
        "exit_code": 0,
        "validator": "language compiler",
    }
    client = StubAsyncClient(lambda _url, _body: {
        "valid": True,
        "status": "passed",
        "findings": [],
        "validator": "language compiler",
        "sandbox_result": sandbox_result,
    })
    monkeypatch.setattr(validation_client.httpx, "AsyncClient", lambda **_kwargs: client)

    result = asyncio.run(validation_client.validate(
        "p1",
        {filename: source},
        [],
    ))

    assert client.requests[0][0].endswith("/validate")
    assert client.requests[0][1] == {"files": {filename: source}}
    assert result["validator"] == "language compiler"
    assert result["status"] == "passed"
    assert result["passed"] is True
    assert result["sandbox_result"] == sandbox_result


def test_repair_client_adapts_finding_response(monkeypatch):
    client = StubAsyncClient(lambda _url, _body: {
        "status": "completed",
        "repairs": [{
            "path": "main.py",
            "original_code": "print('broken')\n",
            "regenerated_code": "print('fixed')\n",
            "changed": True,
            "explanation": "Updated the output.",
        }],
        "unresolved_findings": [],
        "validation_status": "syntax_and_ruff_checked",
    })
    monkeypatch.setattr(repair_client.httpx, "AsyncClient", lambda **_kwargs: client)

    result = asyncio.run(repair_client.repair(
        "p1",
        {"main.py": "print('broken')\n"},
        [{
            "file": "main.py",
            "description": "Replace broken output",
            "severity": "high",
            "rule_id": "RULE-001",
            "evidence": "diagnostic evidence",
            "tool": "ESLint",
        }],
    ))

    assert client.requests[0][0].endswith("/repair")
    assert client.requests[0][1]["findings"][0]["severity"] == "high"
    assert client.requests[0][1]["findings"][0]["rule_id"] == "RULE-001"
    assert client.requests[0][1]["findings"][0]["evidence"] == "diagnostic evidence"
    assert client.requests[0][1]["findings"][0]["tool"] == "ESLint"
    assert client.requests[0][1]["files"] == [{"path": "main.py", "content": "print('broken')\n"}]
    assert result["repair_status"] == "needs_review"
    assert result["repaired_files"] == [{"path": "main.py", "content": "print('fixed')\n"}]
    assert result["repair_details"] == [{
        "path": "main.py",
        "changed": True,
        "explanation": "Updated the output.",
        "resolved_finding_ids": [],
    }]


def test_repair_client_keeps_partial_candidate_for_reanalysis_without_agent_error(monkeypatch):
    client = StubAsyncClient(lambda _url, _body: {
        "status": "partial",
        "outcome": "partially_applied",
        "repairs": [{
            "path": "main.c",
            "original_code": "int main(void) { return 0; }\n",
            "regenerated_code": "int main(void) { return 1; }\n",
            "changed": True,
            "explanation": "Applied a targeted change.",
            "resolved_finding_ids": [],
        }],
        "unresolved_findings": [{"finding_id": "f-1", "file": "main.c"}],
        "validation_status": "pending_isolated_sandbox_validation",
    })
    monkeypatch.setattr(repair_client.httpx, "AsyncClient", lambda **_kwargs: client)

    result = asyncio.run(repair_client.repair(
        "p-partial",
        {"main.c": "int main(void) { return 0; }\n"},
        [{"finding_id": "f-1", "file": "main.c", "description": "Change the return value"}],
        language="c",
    ))

    assert result["status"] == "partial"
    assert result["error"] is None
    assert result["changed_files"] == ["main.c"]
    assert result["repaired_files"] == [{
        "path": "main.c",
        "content": "int main(void) { return 1; }\n",
    }]
    assert result["unresolved_findings"] == [{"finding_id": "f-1", "file": "main.c"}]


def test_repair_client_maps_fileless_analyzer_findings_to_a_source_file(monkeypatch):
    client = StubAsyncClient(lambda _url, _body: {
        "status": "completed",
        "repair_status": "success",
        "repairs": [{
            "path": "pasted-code.py",
            "original_code": "def add(value):\nreturn 0\n",
            "regenerated_code": "def add(value):\n    return 0\n",
            "changed": True,
            "explanation": "Fixed the function indentation.",
        }],
        "unresolved_findings": [],
    })
    monkeypatch.setattr(repair_client.httpx, "AsyncClient", lambda **_kwargs: client)

    result = asyncio.run(repair_client.repair(
        "p-invalid-python",
        {"pasted-code.py": "def add(value):\nreturn 0\n"},
        [
            {
                "finding_id": "QUAL-0001",
                "file": "unknown",
                "line_number": "N/A",
                "description": "Analyzer failed before producing a file-specific diagnostic.",
                "issue": "MYPY_FAILED",
            },
            {
                "finding_id": "QUAL-0002",
                "file": "unknown",
                "line_number": "3",
                "description": "Second analyzer finding.",
                "issue": "SECOND_FINDING",
            },
        ],
    ))

    assert [finding["file"] for finding in client.requests[0][1]["findings"]] == [
        "pasted-code.py",
        "pasted-code.py",
    ]
    assert [finding["line"] for finding in client.requests[0][1]["findings"]] == [None, 3]
    assert result["changed_files"] == ["pasted-code.py"]
    assert result["repair_status"] == "success"


def test_repair_client_preserves_original_code_and_explains_timeout(monkeypatch):
    class TimeoutClient(StubAsyncClient):
        async def post(self, _url, json):
            raise httpx.ReadTimeout("")

    monkeypatch.setattr(repair_client.httpx, "AsyncClient", lambda **_kwargs: TimeoutClient(lambda *_args: {}))
    original_files = {"main.py": "print('original')\n"}
    findings = [{"file": "main.py", "issue": "Remove unsafe call"}]

    result = asyncio.run(repair_client.repair("p-timeout", original_files, findings))

    assert result["status"] == "failed"
    assert result["repaired_files"] == [{"path": "main.py", "content": original_files["main.py"]}]
    assert result["unresolved_findings"] == findings
    assert result["error"] == "Repair service timed out before returning a candidate."