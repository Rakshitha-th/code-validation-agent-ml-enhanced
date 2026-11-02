"""
Runs Ruff (fast Python linter) against a set of files and returns raw
issue dicts. Ruff is used for style, unused imports/vars, and common
correctness lints.
"""
import json
import subprocess
import tempfile

from app.analyzers.workspace import original_path, write_source_files


def run_ruff(files: dict[str, str]) -> list[dict]:
    """
    files: {"app.py": "<source code>", ...}
    Returns a list of raw ruff issues (pre-normalization).
    """
    issues: list[dict] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        path_map = write_source_files(tmp_dir, files)

        try:
            result = subprocess.run(
                ["ruff", "check", tmp_dir, "--output-format=json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            raw_output = result.stdout.strip()
            if not raw_output:
                if result.returncode != 0:
                    raise RuntimeError("ruff exited without machine-readable output")
                return issues

            ruff_issues = json.loads(raw_output)
            for item in ruff_issues:
                abs_path = item.get("filename", "")
                issues.append(
                    {
                        "source": "ruff",
                        "file": original_path(path_map, abs_path),
                        "line_number": item.get("location", {}).get("row"),
                        "code": item.get("code"),
                        "message": item.get("message"),
                        "severity_hint": "medium",
                    }
                )
        except FileNotFoundError as exc:
            raise FileNotFoundError("ruff executable is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("ruff analysis timed out") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("ruff returned malformed JSON") from exc

    return issues
