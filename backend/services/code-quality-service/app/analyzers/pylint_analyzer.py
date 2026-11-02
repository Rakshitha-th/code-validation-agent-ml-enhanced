"""
Runs Pylint against a set of files and returns raw issue dicts.
Pylint catches structural/design problems ruff doesn't (e.g. too many
branches, poor naming, missing docstrings) plus additional correctness
checks.
"""
import json
import os
import subprocess
import tempfile

from app.analyzers.workspace import original_path, write_source_files

# Map pylint's message categories to our severity vocabulary
_PYLINT_SEVERITY_MAP = {
    "fatal": "critical",
    "warning": "medium",
    "convention": "low",
    "refactor": "low",
}


def run_pylint(files: dict[str, str]) -> list[dict]:
    issues: list[dict] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        path_map = write_source_files(tmp_dir, files)
        file_paths = [
            os.path.join(tmp_dir, *relative_path.replace("\\", "/").split("/"))
            for relative_path in files
        ]

        try:
            result = subprocess.run(
                ["pylint", *file_paths, "--output-format=json", "--disable=all",
                 "--enable=E,W,C0114,C0115,C0116,R0911,R0912,R0913,R0915"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            raw_output = result.stdout.strip()
            if not raw_output:
                if result.returncode != 0:
                    raise RuntimeError("pylint exited without machine-readable output")
                return issues
            if raw_output == "No files to lint: exiting.":
                return issues

            pylint_issues = json.loads(raw_output)
            for item in pylint_issues:
                abs_path = item.get("path", "")
                category = item.get("type", "warning")
                issues.append(
                    {
                        "source": "pylint",
                        "file": original_path(path_map, abs_path),
                        "line_number": item.get("line"),
                        "code": item.get("message-id") or item.get("symbol"),
                        "message": item.get("message"),
                        "severity_hint": _PYLINT_SEVERITY_MAP.get(category, "medium"),
                    }
                )
        except FileNotFoundError as exc:
            raise FileNotFoundError("pylint executable is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("pylint analysis timed out") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("pylint returned malformed JSON") from exc

    return issues
