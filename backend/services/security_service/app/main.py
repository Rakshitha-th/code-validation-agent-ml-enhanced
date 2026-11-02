import json
import os
import re
import subprocess
import tempfile
from pathlib import PurePosixPath
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator
import httpx

from shared.languages import detect_language, is_source_path, validate_project_path


app = FastAPI(
    title="Security Agent",
    description="Security vulnerability detection service",
    version="1.2.0",
)

GITLEAKS_PATH = os.getenv("GITLEAKS_PATH", "gitleaks")
SECURITY_SANDBOX_SERVICE_URL = os.getenv("SECURITY_SANDBOX_SERVICE_URL")
MAX_CODE_SIZE = 200_000
MAX_FILENAME_LENGTH = 255


class CodeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=MAX_CODE_SIZE)
    filename: str = Field(default="main.py", min_length=1, max_length=MAX_FILENAME_LENGTH)


class ProjectCodeRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    files: dict[str, str] = Field(min_length=1, max_length=100)

    @field_validator("files")
    @classmethod
    def validate_files(cls, files: dict[str, str]) -> dict[str, str]:
        normalized = {}
        total_size = 0
        for path, content in files.items():
            relative_path = validate_project_path(path)
            if relative_path in normalized:
                raise ValueError("project file paths must be unique")
            if len(content.encode("utf-8")) > MAX_CODE_SIZE:
                raise ValueError(f"file exceeds the size limit: {relative_path}")
            normalized[relative_path] = content
            total_size += len(content.encode("utf-8"))
        if total_size > 1_000_000:
            raise ValueError("project exceeds the security scan input limit")
        source_paths = [path for path in normalized if is_source_path(path)]
        if not source_paths:
            raise ValueError("at least one supported source file is required")
        detect_language(source_paths)
        return normalized


class SecurityFinding(BaseModel):
    type: str
    severity: str
    file: str
    line: Optional[int] = None
    description: str
    evidence: Optional[str] = None
    owasp: Optional[str] = None
    cwe: Optional[str] = None
    recommendation: Optional[str] = None


class SecurityResponse(BaseModel):
    status: str
    filename: str
    total_findings: int
    findings: List[SecurityFinding]


@app.get("/")
def root():
    return {"service": "Security Agent", "status": "running", "version": "1.2.0"}


@app.get("/health")
def health():
    return {"status": "healthy", "service": "security-agent"}


def get_safe_filename(filename: str) -> str:
    return os.path.basename(filename.strip()) or "main.py"


def get_owasp_mapping(cwe_id: Optional[int]) -> Optional[str]:
    mapping = {
        22: "A01:2021 - Broken Access Control",
        23: "A01:2021 - Broken Access Control",
        35: "A01:2021 - Broken Access Control",
        284: "A01:2021 - Broken Access Control",
        285: "A01:2021 - Broken Access Control",
        352: "A01:2021 - Broken Access Control",
        639: "A01:2021 - Broken Access Control",
        259: "A07:2021 - Identification and Authentication Failures",
        287: "A07:2021 - Identification and Authentication Failures",
        306: "A07:2021 - Identification and Authentication Failures",
        522: "A07:2021 - Identification and Authentication Failures",
        798: "A07:2021 - Identification and Authentication Failures",
        261: "A02:2021 - Cryptographic Failures",
        295: "A02:2021 - Cryptographic Failures",
        311: "A02:2021 - Cryptographic Failures",
        312: "A02:2021 - Cryptographic Failures",
        319: "A02:2021 - Cryptographic Failures",
        326: "A02:2021 - Cryptographic Failures",
        327: "A02:2021 - Cryptographic Failures",
        78: "A03:2021 - Injection",
        79: "A03:2021 - Injection",
        89: "A03:2021 - Injection",
        90: "A03:2021 - Injection",
        91: "A03:2021 - Injection",
        94: "A03:2021 - Injection",
        643: "A03:2021 - Injection",
        200: "A04:2021 - Insecure Design",
        209: "A04:2021 - Insecure Design",
        256: "A04:2021 - Insecure Design",
        16: "A05:2021 - Security Misconfiguration",
        434: "A05:2021 - Security Misconfiguration",
        548: "A05:2021 - Security Misconfiguration",
        942: "A05:2021 - Security Misconfiguration",
        601: "A06:2021 - Vulnerable and Outdated Components",
        110: "A06:2021 - Vulnerable and Outdated Components",
        362: "A09:2021 - Security Logging and Monitoring Failures",
        778: "A09:2021 - Security Logging and Monitoring Failures",
        611: "A10:2021 - Server-Side Request Forgery",
        918: "A10:2021 - Server-Side Request Forgery",
        494: "A08:2021 - Software and Data Integrity Failures",
        502: "A08:2021 - Software and Data Integrity Failures",
        829: "A08:2021 - Software and Data Integrity Failures",
        1104: "A08:2021 - Software and Data Integrity Failures",
    }
    return mapping.get(cwe_id)


def normalize_severity(severity: Optional[str]) -> str:
    severity = (severity or "INFO").upper()
    return severity if severity in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"} else "INFO"


def extract_cwe(value) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if not value:
        return None
    value = str(value)
    return value if "CWE-" in value else f"CWE-{value}" if value.isdigit() else value


def get_cwe_id(cwe: Optional[str]) -> Optional[int]:
    try:
        return int(cwe.replace("CWE-", "").strip()) if cwe else None
    except ValueError:
        return None


def deduplicate_findings(findings: List[SecurityFinding]) -> List[SecurityFinding]:
    unique, seen = [], set()
    for finding in findings:
        key = (finding.file, finding.line, finding.cwe, finding.type, finding.description)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def run_bandit(code: str, filename: str) -> List[SecurityFinding]:
    findings = []
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        temp_path = f.name

    try:
        result = subprocess.run(
            ["bandit", "-f", "json", "-q", temp_path],
            capture_output=True, text=True, timeout=30
        )

        if not result.stdout.strip():
            if result.returncode != 0:
                findings.append(SecurityFinding(
                    type="ScannerError", severity="HIGH", file=filename,
                    description=f"Bandit scan failed: {result.stderr.strip()}",
                    recommendation="Check the Bandit installation and scanner configuration."
                ))
            return findings

        for issue in json.loads(result.stdout).get("results", []):
            cwe_id = issue.get("issue_cwe", {}).get("id")
            findings.append(SecurityFinding(
                type=issue.get("test_id", "Bandit"),
                severity=normalize_severity(issue.get("issue_severity")),
                file=filename,
                line=issue.get("line_number"),
                description=issue.get("issue_text", ""),
                evidence=issue.get("code"),
                owasp=get_owasp_mapping(cwe_id),
                cwe=f"CWE-{cwe_id}" if cwe_id else None,
                recommendation="Review the flagged code and apply a secure alternative."
            ))

    except subprocess.TimeoutExpired:
        findings.append(SecurityFinding(
            type="ScannerTimeout", severity="HIGH", file=filename,
            description="Bandit scan exceeded the allowed execution time."
        ))
    except json.JSONDecodeError:
        findings.append(SecurityFinding(
            type="ScannerError", severity="HIGH", file=filename,
            description="Bandit returned an invalid JSON response."
        ))
    except FileNotFoundError:
        findings.append(SecurityFinding(
            type="ScannerUnavailable", severity="HIGH", file=filename,
            description="Bandit executable was not found.",
            recommendation="Install Bandit and make sure it is available in PATH."
        ))
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    return findings


def run_semgrep(code: str, filename: str) -> List[SecurityFinding]:
    findings = []
    extension = os.path.splitext(filename)[1] or ".txt"
    config = {
        ".py": "p/python",
        ".js": "p/javascript",
        ".jsx": "p/javascript",
        ".mjs": "p/javascript",
        ".cjs": "p/javascript",
        ".ts": "p/typescript",
        ".tsx": "p/typescript",
        ".java": "p/java",
        ".c": "p/c",
        ".h": "p/c",
        ".cc": "p/cpp",
        ".cpp": "p/cpp",
        ".cxx": "p/cpp",
        ".hh": "p/cpp",
        ".hpp": "p/cpp",
        ".hxx": "p/cpp",
        ".go": "p/golang",
        ".cs": "p/csharp",
    }.get(extension.lower(), "p/default")
    with tempfile.NamedTemporaryFile(mode="w", suffix=extension, delete=False, encoding="utf-8") as f:
        f.write(code)
        temp_path = f.name

    try:
        result = subprocess.run(
            ["semgrep", "--config", config, "--json", "--quiet", temp_path],
            capture_output=True, text=True, timeout=60
        )

        if not result.stdout.strip():
            if result.returncode != 0:
                findings.append(SecurityFinding(
                    type="ScannerError", severity="HIGH", file=filename,
                    description=f"Semgrep scan failed: {result.stderr.strip()}",
                    recommendation="Check the Semgrep installation and scanner configuration."
                ))
            return findings

        for item in json.loads(result.stdout).get("results", []):
            extra = item.get("extra", {})
            metadata = extra.get("metadata", {})
            cwe = extract_cwe(metadata.get("cwe"))
            owasp = metadata.get("owasp")

            if isinstance(owasp, list):
                owasp = owasp[0] if owasp else None

            if not owasp:
                owasp = get_owasp_mapping(get_cwe_id(cwe))

            findings.append(SecurityFinding(
                type=item.get("check_id", "Semgrep"),
                severity=normalize_severity(extra.get("severity")),
                file=filename,
                line=item.get("start", {}).get("line"),
                description=extra.get("message", "Security issue detected by Semgrep."),
                evidence=extra.get("lines"),
                owasp=owasp,
                cwe=cwe,
                recommendation="Review the Semgrep finding and apply the recommended secure coding practice."
            ))

    except subprocess.TimeoutExpired:
        findings.append(SecurityFinding(
            type="SemgrepTimeout", severity="HIGH", file=filename,
            description="Semgrep scan exceeded the allowed execution time."
        ))
    except json.JSONDecodeError:
        findings.append(SecurityFinding(
            type="SemgrepError", severity="HIGH", file=filename,
            description="Semgrep returned an invalid JSON response."
        ))
    except FileNotFoundError:
        findings.append(SecurityFinding(
            type="SemgrepUnavailable", severity="HIGH", file=filename,
            description="Semgrep executable was not found.",
            recommendation="Install Semgrep and make sure it is available in PATH."
        ))
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    return findings


def run_clang_static_analyzer(code: str, filename: str) -> List[SecurityFinding]:
    extension = os.path.splitext(filename)[1].lower()
    if extension not in {".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"}:
        return []
    language_flag = "c++" if extension in {".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"} else "c"
    findings = []
    with tempfile.NamedTemporaryFile(mode="w", suffix=extension, delete=False, encoding="utf-8") as source:
        source.write(code)
        source_path = source.name
    try:
        result = subprocess.run(
            ["clang", "--analyze", "-Xanalyzer", "-analyzer-output=text", source_path, f"-std={language_flag}11" if language_flag == "c" else "-std=c++17"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        for diagnostic in result.stderr.splitlines():
            match = re.match(r"^.*?:(\d+):\d+:\s*(warning|error):\s*(.*)$", diagnostic)
            if not match:
                continue
            line, severity, message = match.groups()
            findings.append(SecurityFinding(
                type="ClangStaticAnalyzer",
                severity="HIGH" if severity == "error" else "MEDIUM",
                file=filename,
                line=int(line),
                description=message,
                recommendation="Review the Clang Static Analyzer diagnostic and verify the affected code path.",
            ))
        if result.returncode not in (0, 1) or (result.returncode == 1 and not findings):
            findings.append(SecurityFinding(
                type="ClangStaticAnalyzerError",
                severity="HIGH",
                file=filename,
                description="Clang Static Analyzer could not complete.",
                recommendation="Check the C/C++ toolchain and source dependencies.",
            ))
    except subprocess.TimeoutExpired:
        findings.append(SecurityFinding(
            type="ClangStaticAnalyzerTimeout",
            severity="HIGH",
            file=filename,
            description="Clang Static Analyzer exceeded the allowed execution time.",
        ))
    except FileNotFoundError:
        findings.append(SecurityFinding(
            type="ClangStaticAnalyzerUnavailable",
            severity="HIGH",
            file=filename,
            description="Clang Static Analyzer executable was not found.",
        ))
    finally:
        try:
            os.remove(source_path)
        except OSError:
            pass
    return findings


def run_npm_audit(files: dict[str, str]) -> List[SecurityFinding]:
    lockfiles = [path for path in files if PurePosixPath(path).name == "package-lock.json"]
    if not lockfiles:
        return []

    findings = []
    package_lock = lockfiles[0]
    with tempfile.TemporaryDirectory() as temp_dir:
        for path, content in files.items():
            target = os.path.join(temp_dir, *PurePosixPath(path).parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as project_file:
                project_file.write(content)
        working_directory = os.path.join(temp_dir, *PurePosixPath(package_lock).parent.parts)
        try:
            result = subprocess.run(
                ["npm", "audit", "--json", "--ignore-scripts", "--no-fund"],
                cwd=working_directory,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return [SecurityFinding(
                type="NpmAuditTimeout",
                severity="HIGH",
                file=package_lock,
                description="npm audit exceeded the allowed execution time.",
            )]
        except FileNotFoundError:
            return [SecurityFinding(
                type="NpmAuditUnavailable",
                severity="HIGH",
                file=package_lock,
                description="npm executable was not found.",
            )]
        try:
            report = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return [SecurityFinding(
                type="NpmAuditError",
                severity="HIGH",
                file=package_lock,
                description="npm audit returned an invalid report.",
            )]
        if not isinstance(report, dict) or not isinstance(report.get("vulnerabilities", {}), dict):
            return [SecurityFinding(
                type="NpmAuditError",
                severity="HIGH",
                file=package_lock,
                description="npm audit returned a malformed vulnerability report.",
            )]

    for package, vulnerability in report.get("vulnerabilities", {}).items():
        advisories = vulnerability.get("via", [])
        descriptions = [
            item.get("title") or item.get("name")
            for item in advisories
            if isinstance(item, dict)
        ]
        findings.append(SecurityFinding(
            type="NpmAudit",
            severity=normalize_severity(vulnerability.get("severity")),
            file=package_lock,
            description=f"{package}: {'; '.join(descriptions) or 'Vulnerable dependency detected.'}",
            recommendation=f"Upgrade {package} to a version outside the affected range reported by npm audit.",
        ))
    if result.returncode not in (0, 1) or (result.returncode == 1 and not findings):
        findings.append(SecurityFinding(
            type="NpmAuditError",
            severity="HIGH",
            file=package_lock,
            description="npm audit could not complete; check the registry connection and package lockfile.",
        ))
    return findings


def run_govulncheck(files: dict[str, str]) -> List[SecurityFinding]:
    module_files = [path for path in files if PurePosixPath(path).name == "go.mod"]
    if not module_files:
        return []
    module_file = module_files[0]
    findings = []
    with tempfile.TemporaryDirectory() as temp_dir:
        for path, content in files.items():
            target = os.path.join(temp_dir, *PurePosixPath(path).parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as project_file:
                project_file.write(content)
        working_directory = os.path.join(temp_dir, *PurePosixPath(module_file).parent.parts)
        try:
            result = subprocess.run(
                ["govulncheck", "./..."],
                cwd=working_directory,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return [SecurityFinding(
                type="GovulncheckTimeout",
                severity="HIGH",
                file=module_file,
                description="govulncheck exceeded the allowed execution time.",
            )]
        except FileNotFoundError:
            return [SecurityFinding(
                type="GovulncheckUnavailable",
                severity="HIGH",
                file=module_file,
                description="govulncheck executable was not found.",
            )]
    vulnerability_lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if re.match(r"^Vulnerability #\d+:", line.strip())
    ]
    for line in vulnerability_lines:
        findings.append(SecurityFinding(
            type="Govulncheck",
            severity="HIGH",
            file=module_file,
            description=line,
            recommendation="Upgrade the affected Go module to a version fixed by the Go vulnerability database.",
        ))
    if result.returncode not in (0, 3) or (result.returncode == 3 and not findings):
        findings.append(SecurityFinding(
            type="GovulncheckError",
            severity="HIGH",
            file=module_file,
            description="govulncheck could not complete; check module resolution and vulnerability database access.",
        ))
    return findings


def run_dotnet_security_analyzers(files: dict[str, str]) -> List[SecurityFinding]:
    source_paths = [path for path in files if path.lower().endswith(".cs")]
    if not source_paths:
        return []
    entry = next(
        (path for path in source_paths if PurePosixPath(path).name.lower() in {"program.cs", "main.cs"}),
        source_paths[0],
    )
    if not SECURITY_SANDBOX_SERVICE_URL:
        return [SecurityFinding(
            type="DotNetSecurityAnalyzerUnavailable",
            severity="HIGH",
            file=entry,
            description="The isolated .NET analyzer service is not configured.",
        )]
    try:
        response = httpx.post(
            f"{SECURITY_SANDBOX_SERVICE_URL.rstrip('/')}/execute",
            json={
                "project_id": "security-analysis",
                "filename": entry,
                "code": files[entry],
                "files": files,
                "analysis_only": True,
                "timeout": 180,
            },
            timeout=210,
        )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError):
        return [SecurityFinding(
            type="DotNetSecurityAnalyzerError",
            severity="HIGH",
            file=entry,
            description=".NET security analyzers could not complete in the isolated sandbox.",
        )]
    if not isinstance(result, dict) or not result.get("executed"):
        return [SecurityFinding(
            type="DotNetSecurityAnalyzerUnavailable",
            severity="HIGH",
            file=entry,
            description="The isolated .NET analyzer execution was unavailable.",
        )]

    output = "\n".join(part for part in (result.get("stdout"), result.get("stderr")) if part)
    findings = []
    paths_by_name: dict[str, list[str]] = {}
    for path in source_paths:
        paths_by_name.setdefault(PurePosixPath(path).name, []).append(path)
    for line in output.splitlines():
        match = re.search(
            r"(?P<path>[^():]+)\((?P<line>\d+),\d+\):\s*(?P<severity>warning|error)\s+(?P<rule>CA\d{4}):\s*(?P<message>.+)",
            line,
        )
        if not match:
            continue
        rule = match.group("rule")
        if rule not in {"CA2100", "CA3001", "CA3002", "CA3003", "CA3004", "CA3005", "CA3006", "CA5386", "CA5390"}:
            continue
        findings.append(SecurityFinding(
            type=f"DotNetSecurityAnalyzer-{rule}",
            severity="HIGH" if match.group("severity") == "error" else "MEDIUM",
            file=(
                paths_by_name[match.group("path").replace("\\", "/").rsplit("/", 1)[-1]][0]
                if len(paths_by_name.get(match.group("path").replace("\\", "/").rsplit("/", 1)[-1], [])) == 1
                else entry
            ),
            line=int(match.group("line")),
            description=match.group("message"),
            recommendation="Review the .NET security analyzer diagnostic and remediate the unsafe API usage.",
        ))
    if result.get("status") != "completed" and not findings:
        findings.append(SecurityFinding(
            type="DotNetSecurityAnalyzerError",
            severity="HIGH",
            file=entry,
            description="The isolated .NET build or analyzer did not complete successfully.",
        ))
    return findings


def run_gitleaks(code: str, filename: str) -> List[SecurityFinding]:
    findings = []
    safe_filename = get_safe_filename(filename)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = os.path.join(temp_dir, safe_filename)
        report_path = os.path.join(temp_dir, "gitleaks-report.json")

        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(code)

        try:
            result = subprocess.run(
                [
                    GITLEAKS_PATH, "detect", "--source", temp_dir, "--no-git",
                    "--report-format", "json", "--report-path", report_path,
                    "--exit-code", "0"
                ],
                capture_output=True, text=True, timeout=60
            )

            if not os.path.exists(report_path):
                if result.returncode != 0:
                    findings.append(SecurityFinding(
                        type="GitleaksError", severity="HIGH", file=filename,
                        description="Gitleaks scan failed.",
                        recommendation="Check the Gitleaks installation and configuration."
                    ))
                return findings

            for secret in json.load(open(report_path, encoding="utf-8")):
                findings.append(SecurityFinding(
                    type="Gitleaks",
                    severity="HIGH",
                    file=filename,
                    line=secret.get("StartLine"),
                    description=f"Potential hardcoded secret detected: {secret.get('Description', 'Secret')}",
                    evidence="[REDACTED]",
                    owasp="A07:2021 - Identification and Authentication Failures",
                    recommendation="Remove the hardcoded secret and store credentials securely using environment variables or a secret management system."
                ))

        except subprocess.TimeoutExpired:
            findings.append(SecurityFinding(
                type="GitleaksTimeout", severity="HIGH", file=filename,
                description="Gitleaks scan exceeded the allowed execution time."
            ))
        except FileNotFoundError:
            findings.append(SecurityFinding(
                type="GitleaksUnavailable", severity="HIGH", file=filename,
                description="Gitleaks executable was not found.",
                recommendation="Install Gitleaks and make sure it is available in PATH or set GITLEAKS_PATH."
            ))
        except json.JSONDecodeError:
            findings.append(SecurityFinding(
                type="GitleaksError", severity="HIGH", file=filename,
                description="Gitleaks returned an invalid JSON report."
            ))

    return findings


@app.post("/scan", response_model=SecurityResponse)
def scan_code(request: CodeRequest):
    filename = get_safe_filename(request.filename)
    findings = []
    if filename.lower().endswith(".py"):
        findings.extend(run_bandit(request.code, filename))
    findings.extend(run_semgrep(request.code, filename))
    findings.extend(run_clang_static_analyzer(request.code, filename))
    findings.extend(run_gitleaks(request.code, filename))

    findings = deduplicate_findings(findings)
    scanner_failures = {
        "ScannerError",
        "ScannerTimeout",
        "ScannerUnavailable",
        "SemgrepError",
        "SemgrepTimeout",
        "SemgrepUnavailable",
        "ClangStaticAnalyzerError",
        "ClangStaticAnalyzerTimeout",
        "ClangStaticAnalyzerUnavailable",
        "GitleaksError",
        "GitleaksTimeout",
        "GitleaksUnavailable",
    }

    return SecurityResponse(
        status="failed" if any(finding.type in scanner_failures for finding in findings) else "completed",
        filename=filename,
        total_findings=len(findings),
        findings=findings
    )


@app.post("/scan-project", response_model=SecurityResponse)
def scan_project(request: ProjectCodeRequest):
    findings = []
    source_files = {
        path: content for path, content in request.files.items()
        if is_source_path(path)
    }
    for path, content in source_files.items():
        if path.lower().endswith(".py"):
            findings.extend(run_bandit(content, path))
        findings.extend(run_semgrep(content, path))
        findings.extend(run_clang_static_analyzer(content, path))
        findings.extend(run_gitleaks(content, path))

    findings.extend(run_npm_audit(request.files))
    findings.extend(run_govulncheck(request.files))
    findings.extend(run_dotnet_security_analyzers(request.files))
    findings = deduplicate_findings(findings)
    return SecurityResponse(
        status="failed" if any(
            finding.type.endswith(("Error", "Timeout", "Unavailable"))
            for finding in findings
        ) else "completed",
        filename=request.project_id,
        total_findings=len(findings),
        findings=findings,
    )