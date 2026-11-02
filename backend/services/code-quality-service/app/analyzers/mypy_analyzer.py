import re
import subprocess
import tempfile

from app.analyzers.workspace import original_path, write_source_files


_DIAGNOSTIC = re.compile(r"^(.*?):(\d+): (error|warning|note): (.*?)(?:\s+\[([a-z0-9-]+)\])?$")


class PartialMypyAnalysisError(RuntimeError):
    def __init__(self, issues: list[dict]):
        super().__init__("mypy returned diagnostics but could not complete project analysis")
        self.issues = issues


def _parse_issues(output: str, path_map: dict[str, str]) -> list[dict]:
    issues = []
    for line in output.splitlines():
        match = _DIAGNOSTIC.match(line)
        if not match:
            continue
        absolute_path, line_number, severity, message, code = match.groups()
        issues.append({
            "source": "mypy",
            "file": original_path(path_map, absolute_path),
            "line_number": int(line_number),
            "code": f"MYPY_{(code or severity).upper().replace('-', '_')}",
            "message": message,
            "severity_hint": "high" if severity == "error" else "low",
        })
    return issues


def run_mypy(files: dict[str, str]) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path_map = write_source_files(tmp_dir, files)
        paths = [f"{tmp_dir}/{path}" for path in files]
        try:
            result = subprocess.run(
                [
                    "mypy",
                    "--show-error-codes",
                    "--no-error-summary",
                    "--ignore-missing-imports",
                    *paths,
                ],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError("mypy executable is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("mypy analysis timed out") from exc

        if result.returncode not in (0, 1):
            issues = _parse_issues(result.stdout, path_map)
            if issues:
                raise PartialMypyAnalysisError(issues)
            raise RuntimeError("mypy failed before completing analysis")

        return _parse_issues(result.stdout, path_map)
