import ast
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path


def _ruff_diagnostics(code: str) -> list[dict] | None:
    ruff_path = shutil.which("ruff")
    if not ruff_path:
        return None

    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = Path(temp_dir) / "candidate.py"
        file_path.write_text(code, encoding="utf-8")
        result = subprocess.run(
            [ruff_path, "check", "--select", "E,W,F", "--output-format", "json", str(file_path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode not in (0, 1):
            raise OSError("Ruff could not complete code-quality validation")
        diagnostics = json.loads(result.stdout or "[]")
        if not isinstance(diagnostics, list) or any(not isinstance(item, dict) for item in diagnostics):
            raise OSError("Ruff returned malformed code-quality diagnostics")
        for item in diagnostics:
            item["filename"] = Path(item.get("filename", "")).name
        return diagnostics


def _ruff_project_diagnostics(files: dict[str, str]) -> list[dict] | None:
    ruff_path = shutil.which("ruff")
    if not ruff_path:
        return None

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        paths = []
        project_names = {}
        for relative_path, code in files.items():
            path = root.joinpath(*Path(relative_path).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code, encoding="utf-8")
            paths.append(str(path))
            project_names[str(path.resolve()).lower()] = relative_path.replace("\\", "/")

        result = subprocess.run(
            [ruff_path, "check", "--select", "E,W,F", "--output-format", "json", *paths],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode not in (0, 1):
            raise OSError("Ruff could not complete project code-quality validation")
        diagnostics = json.loads(result.stdout or "[]")
        if not isinstance(diagnostics, list) or any(not isinstance(item, dict) for item in diagnostics):
            raise OSError("Ruff returned malformed project diagnostics")
        for item in diagnostics:
            filename = item.get("filename")
            if not isinstance(filename, str):
                raise OSError("Ruff returned a diagnostic without a valid filename")
            path = str(Path(filename).resolve()).lower()
            if path in project_names:
                item["filename"] = project_names[path]
        return diagnostics


def _run_language_check(
    command: list[str],
    timeout: int = 15,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
            cwd=cwd,
        )
    except FileNotFoundError:
        return False, f"Required validation tool is not installed: {command[0]}"
    except subprocess.TimeoutExpired:
        return False, f"{command[0]} validation timed out."
    except OSError as exc:
        return False, f"Could not run {command[0]} validation: {exc}"

    if result.returncode == 0:
        return True, f"{command[0]} validation passed."
    details = (result.stderr or result.stdout).strip()
    return False, details[:1200] or f"{command[0]} validation failed."


def _validate_non_python_project(files: dict[str, str], language: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        paths = {}
        for relative_path, content in files.items():
            path = root.joinpath(*Path(relative_path).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            paths[relative_path] = path
        source_paths = [
            paths[path]
            for path in files
            if Path(path).suffix.lower() in {
                "javascript": {".js", ".jsx", ".mjs", ".cjs"},
                "typescript": {".ts", ".tsx"},
                "java": {".java"},
                "c": {".c"},
                "cpp": {".cc", ".cpp", ".cxx"},
                "go": {".go"},
                "csharp": {".cs"},
            }.get(language, set())
        ]
        if not source_paths:
            return False, f"No {language} source files were supplied for project validation."

        if language == "javascript":
            for source_path in source_paths:
                valid, message = _run_language_check(["node", "--check", str(source_path)])
                if not valid:
                    return valid, message
            return True, "node project syntax validation passed."
        elif language == "typescript":
            project_file = next((path for path in paths.values() if path.name.lower() == "tsconfig.json"), None)
            command = (
                ["tsc", "--noEmit", "--project", str(project_file)]
                if project_file
                else [
                    "tsc", "--noEmit", "--target", "ES2020", "--module", "commonjs",
                    "--jsx", "react-jsx", "--skipLibCheck", *map(str, source_paths),
                ]
            )
            return _run_language_check(command, timeout=60)
        elif language == "java":
            classes_path = root / "classes"
            classes_path.mkdir()
            command = ["javac", "-Xlint:all", "-d", str(classes_path), *map(str, source_paths)]
        elif language == "c":
            command = ["gcc", "-std=c11", "-Wall", "-Wextra", "-fsyntax-only", *map(str, source_paths)]
        elif language == "cpp":
            command = ["g++", "-std=c++17", "-Wall", "-Wextra", "-fsyntax-only", *map(str, source_paths)]
        elif language == "go":
            go_env = os.environ.copy()
            module_file = next((path for path in paths.values() if path.name.lower() == "go.mod"), None)
            go_env.update({
                "GO111MODULE": "on" if module_file else "off",
                "GOCACHE": str(root / "go-cache"),
                "GOPATH": str(root / "go-path"),
                "GOMODCACHE": str(root / "go-mod-cache"),
                "GOPROXY": "off",
            })
            return _run_language_check(
                ["go", "build", "./..."] if module_file else [
                    "go", "build", "-o", str(root / "candidate"), *map(str, source_paths)
                ],
                timeout=60,
                env=go_env,
                cwd=str(root),
            )
        elif language == "csharp":
            project_file = next(
                (path for path in paths.values() if path.suffix.lower() in {".csproj", ".sln"}),
                None,
            )
            if project_file:
                return _run_language_check(
                    ["dotnet", "build", str(project_file), "--no-restore", "--nologo"],
                    timeout=60,
                    env=os.environ.copy(),
                    cwd=str(root),
                )
            command = [
                "mcs", "-warn:4", "-target:library",
                f"-out:{root / 'candidate.dll'}", *map(str, source_paths),
            ]
        else:
            return False, f"Repair validation does not support language '{language}'."
        return _run_language_check(command)


def validate_repair(
    original_code: str,
    candidate_code: str,
    language: str = "python",
    filename: str = "main.py",
    *,
    original_files: dict[str, str] | None = None,
    candidate_files: dict[str, str] | None = None,
) -> tuple[bool, str]:
    normalized_language = language.strip().lower()
    if not candidate_code.strip():
        return False, "Repair candidate is empty."
    if candidate_code == original_code:
        return False, "Repair candidate is unchanged."
    baseline_files = dict(original_files or {filename: original_code})
    repaired_files = dict(candidate_files or baseline_files)
    repaired_files[filename] = candidate_code
    if filename not in baseline_files:
        baseline_files[filename] = original_code
    if normalized_language != "python":
        return _validate_non_python_project(repaired_files, normalized_language)

    original_python = {path: code for path, code in baseline_files.items() if path.lower().endswith(".py")}
    candidate_python = {path: code for path, code in repaired_files.items() if path.lower().endswith(".py")}
    for path, code in candidate_python.items():
        try:
            ast.parse(code, filename=path)
        except SyntaxError as exc:
            return False, f"Syntax error in {path} at line {exc.lineno}: {exc.msg}"

    try:
        if len(original_python) == len(candidate_python) == 1:
            original_findings = _ruff_diagnostics(next(iter(original_python.values())))
            candidate_findings = _ruff_diagnostics(next(iter(candidate_python.values())))
        else:
            original_findings = _ruff_project_diagnostics(original_python)
            candidate_findings = _ruff_project_diagnostics(candidate_python)
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return False, "Ruff could not complete code-quality validation."

    if original_findings is not None and candidate_findings is not None:
        def identity(item: dict) -> tuple:
            return (
                str(item.get("filename", "")).replace("\\", "/").lower(),
                item.get("code", ""),
                item.get("message", ""),
            )

        remaining_original = Counter(identity(item) for item in original_findings)
        introduced = []
        for item in candidate_findings:
            key = identity(item)
            if remaining_original[key]:
                remaining_original[key] -= 1
                continue
            location = item.get("location", {})
            introduced.append(
                f"{item.get('code', 'unknown')} at line {location.get('row', '?')}, "
                f"column {location.get('column', '?')}"
            )
        if introduced:
            return False, "Repair introduced Ruff findings: " + "; ".join(introduced[:5])
        return True, "Syntax and no-new-Ruff-findings checks passed."
    if original_findings is None or candidate_findings is None:
        return False, "Ruff is unavailable; static-analysis verification could not complete."