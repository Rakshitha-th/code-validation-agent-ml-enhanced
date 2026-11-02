from fastapi import FastAPI
from pathlib import PurePosixPath
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from pydantic import BaseModel, Field, field_validator, model_validator
from shared.languages import adapter_for_language, detect_language, is_source_path, validate_project_path
import docker
import os
import shutil
import uuid
import time

app = FastAPI(
    title="Sandbox Agent",
    description="Isolated code execution service",
    version="2.2.0"
)

docker_client = docker.from_env()

MAX_TIMEOUT = 180
DJANGO_CHECK_TIMEOUT = MAX_TIMEOUT
MAX_OUTPUT = 50000
MAX_FILES = 100
MAX_PROJECT_CHARACTERS = 1_000_000
WORKSPACE_ROOT = os.getenv("SANDBOX_WORKSPACE_ROOT", "/sandbox-workspaces")
WORKSPACE_VOLUME = os.getenv("SANDBOX_WORKSPACE_VOLUME", "coding-validation-sandbox-workspaces")
DJANGO_RUNTIME_IMAGE = os.getenv("SANDBOX_DJANGO_RUNTIME_IMAGE", "coding-validation-agent-frontend-sandbox-runtime:latest")
STANDALONE_RUNTIME_IMAGE = os.getenv("SANDBOX_DJANGO_RUNTIME_IMAGE", "coding-validation-agent-frontend-sandbox:latest")
MANIFEST_NAMES = {"requirements.txt", "pyproject.toml", "package.json", "package-lock.json", "tsconfig.json", "pom.xml", "build.gradle", "build.gradle.kts", "go.mod", "go.sum"}
SUPPORTED_RUNTIME_PACKAGES = {
    "django": "5.2.4",
    "asgiref": "3.9.1",
    "sqlparse": "0.5.3",
}
DJANGO_MODULES = {"admin.py", "apps.py", "asgi.py", "models.py", "settings.py", "urls.py", "views.py", "wsgi.py"}
DJANGO_SETTINGS_PATTERN = re.compile(r"DJANGO_SETTINGS_MODULE[\"']?\s*[,=]\s*[\"']([^\"']+)[\"']")
NODE_GLOBALS = ("console", "process", "Buffer", "__dirname", "module", "require")
BROWSER_GLOBALS = (
    "window",
    "document",
    "XMLHttpRequest",
    "fetch",
    "navigator",
    "location",
    "localStorage",
    "sessionStorage",
    "HTMLElement",
    "Node",
    "alert",
)
BROWSER_API_PATTERN = re.compile(
    r"\b(?:window|document|XMLHttpRequest|fetch|navigator|location|localStorage|"
    r"sessionStorage|HTMLElement|Node|alert)\b"
)


class TestCase(BaseModel):
    input: str = Field(default="", max_length=100_000)
    expected: str | None = Field(default=None, max_length=100_000)


class CodeRequest(BaseModel):
    code: str = ""
    filename: str = "main.py"
    files: dict[str, str] = Field(default_factory=dict)
    timeout: int = 20
    stdin: str = Field(default="", max_length=100_000)
    test_cases: list[TestCase] = Field(default_factory=list, max_length=10)
    validation_only: bool = False
    analysis_only: bool = False

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        normalized = validate_project_path(value)
        if not is_source_path(normalized):
            raise ValueError("The sandbox entry filename must be a supported source file")
        return normalized

    @field_validator("files")
    @classmethod
    def validate_files(cls, files: dict[str, str]) -> dict[str, str]:
        if len(files) > MAX_FILES:
            raise ValueError(f"A project may contain at most {MAX_FILES} files")
        normalized_files = {}
        total_characters = 0
        for name, content in files.items():
            relative_path = validate_project_path(name)
            if relative_path in normalized_files:
                raise ValueError("Project file paths must be unique")
            normalized_files[relative_path] = content
            total_characters += len(content)
        if total_characters > MAX_PROJECT_CHARACTERS:
            raise ValueError("Project exceeds the sandbox input limit")
        return normalized_files

    @model_validator(mode="after")
    def select_entry_file(self):
        if not self.files:
            self.files = {self.filename: self.code}
        if self.filename not in self.files:
            raise ValueError("The entry file must be included in the project files")
        if detect_language(list(self.files)) is None:
            raise ValueError("At least one supported source file is required")
        self.code = self.files[self.filename]
        return self

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, value):
        if value < 1 or value > MAX_TIMEOUT:
            raise ValueError(f"Timeout must be between 1 and {MAX_TIMEOUT} seconds")
        return value


@app.get("/health", responses={503: {"description": "Docker execution engine is unavailable"}})
def health():
    try:
        docker_client.ping()
    except Exception as error:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Docker execution engine is unavailable") from error
    return {"status": "healthy", "service": "sandbox-agent"}


def classify_error(status, stderr):

    if status == "timeout":
        return "Timeout"

    if status == "completed":
        return None

    if "SyntaxError" in stderr:
        return "SyntaxError"

    if "Traceback" in stderr:
        return "RuntimeError"

    return "ExecutionError"


def _write_workspace(files: dict[str, str], stdin: str, workspace: str) -> None:
    os.makedirs(workspace, exist_ok=True)
    for relative_path, content in files.items():
        file_path = os.path.join(workspace, *PurePosixPath(relative_path).parts)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(content)
    input_path = os.path.join(workspace, "input.txt")
    with open(input_path, "w", encoding="utf-8") as file:
        file.write(stdin)

def _start_execution_container(
    execution_id: str,
    filename: str,
    image: str = STANDALONE_RUNTIME_IMAGE,
    command: list[str] | None = None,
    working_directory: str | None = None,
    pids_limit: int = 64,
):
    workspace = f"/sandbox/{execution_id}"
    return docker_client.containers.run(
        image=image,
        command=command or [
            "sh",
            "-c",
            'exec python "$1" < "$2"',
            "sandbox-run",
            f"{workspace}/{filename}",
            f"{workspace}/input.txt",
        ],
        volumes={WORKSPACE_VOLUME: {"bind": "/sandbox", "mode": "ro"}},
        working_dir=working_directory or workspace,
        tmpfs={"/tmp": "rw,exec,nosuid,mode=1777,size=256m"},
        environment={
            "PYTHONPATH": f"{workspace}:/app",
            "GOCACHE": "/tmp/go-cache",
            "GOPATH": "/tmp/go-path",
            "JAVA_TOOL_OPTIONS": "-Xmx192m -XX:ActiveProcessorCount=1",
            "HOME": "/tmp/home",
            "DOTNET_CLI_HOME": "/tmp/dotnet-home",
            "NUGET_PACKAGES": "/tmp/nuget-packages",
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
            "DOTNET_NOLOGO": "1",
        },
        network_disabled=True,
        mem_limit="512m",
        nano_cpus=1000000000,
        pids_limit=pids_limit,
        user="nobody",
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        read_only=True,
        detach=True,
        remove=False,
        name=f"sandbox-exec-{execution_id}",
    )

def _wait_for_container(container, timeout: int) -> bool:
    start = time.time()
    while True:
        container.reload()
        if container.status in {"exited", "dead"}:
            return True
        if time.time() - start >= timeout:
            try:
                container.kill()
            except Exception:
                pass
            return False
        time.sleep(0.1)


def _container_result(container) -> dict:
    result = container.wait()
    stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")[:MAX_OUTPUT]
    stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")[:MAX_OUTPUT]
    status = "completed" if result["StatusCode"] == 0 else "failed"
    return {
        "status": status,
        "executed": True,
        "passed": status == "completed",
        "error_type": classify_error(status, stderr),
        "exit_code": result["StatusCode"],
        "stdout": stdout,
        "stderr": stderr,
    }


def _cleanup_execution(container, workspace: str) -> None:
    if container:
        try:
            container.remove(force=True)
        except Exception:
            pass
    shutil.rmtree(workspace, ignore_errors=True)


def run_code(
    files,
    stdin,
    timeout,
    execution_id,
    filename,
    *,
    image=STANDALONE_RUNTIME_IMAGE,
    command=None,
    working_directory=None,
    pids_limit=64,
):
    workspace = os.path.join(WORKSPACE_ROOT, execution_id)
    container = None
    try:
        _write_workspace(files, stdin, workspace)
        container = _start_execution_container(
            execution_id, filename, image, command, working_directory, pids_limit
        )
        if not _wait_for_container(container, timeout):
            return {
                "status": "timeout",
            "executed": True,
            "passed": False,
                "error_type": "Timeout",
                "exit_code": -1,
                "stdout": "",
                "stderr": "Execution timed out.",
            }
        return _container_result(container)

    except Exception as e:
        return {
            "status": "unavailable",
            "executed": False,
            "passed": False,
            "error_type": "SandboxUnavailable",
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "error": "The isolated execution container could not be started.",
        }
    finally:
        _cleanup_execution(container, workspace)


def _manifest_requirements(files: dict[str, str]) -> tuple[list[str], str | None]:
    requirements = []
    try:
        for path, content in files.items():
            name = PurePosixPath(path).name.lower()
            if name == "requirements.txt":
                parsed, error = _parse_requirements_file(content)
                if error:
                    return [], error
                requirements.extend(parsed)
            elif name == "pyproject.toml":
                parsed, error = _parse_pyproject(content)
                if error:
                    return [], error
                requirements.extend(parsed)
    except (tomllib.TOMLDecodeError, KeyError, TypeError):
        return [], "The dependency manifest could not be parsed."
    return requirements, None


def _parse_requirements_file(content: str) -> tuple[list[str], str | None]:
    requirements = []
    for raw_line in content.splitlines():
        line = raw_line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", ".", "git+", "http:", "https:")):
            return [], "Dependency options and direct URLs are not allowed in the isolated runtime."
        requirements.append(line)
    return requirements, None


def _parse_pyproject(content: str) -> tuple[list[str], str | None]:
    project = tomllib.loads(content).get("project")
    if project is None:
        return [], "pyproject.toml must declare dependencies using the PEP 621 [project] table."
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        return [], "pyproject.toml dependencies must be a list of PEP 508 requirements."
    return dependencies, None


def _requirement_error(raw_requirement: str) -> tuple[str, str] | None:
    try:
        requirement = Requirement(raw_requirement)
    except InvalidRequirement:
        return "unsupported_project", "A dependency declaration is not a supported PEP 508 requirement."
    if requirement.url or requirement.extras:
        return "unsupported_project", "Direct references and optional dependency extras are not supported in the offline runtime."
    if requirement.marker and not requirement.marker.evaluate():
        return None
    package = canonicalize_name(requirement.name)
    installed = SUPPORTED_RUNTIME_PACKAGES.get(package)
    if installed is None or not requirement.specifier.contains(Version(installed), prereleases=True):
        return "dependency_missing", f"Dependency '{requirement.name}' is not available in the prebuilt offline runtime."
    return None


def _dependency_error(files: dict[str, str]) -> tuple[str, str] | None:
    requirements, error = _manifest_requirements(files)
    if error:
        return "unsupported_project", error
    for requirement in requirements:
        error = _requirement_error(requirement)
        if error:
            return error
    return None


def _django_project(files: dict[str, str]) -> dict | None:
    python_paths = [path for path in files if path.lower().endswith(".py")]
    framework_files = [path for path in python_paths if PurePosixPath(path).name.lower() in DJANGO_MODULES]
    manage_paths = [path for path in python_paths if PurePosixPath(path).name.lower() == "manage.py"]
    if not framework_files and not manage_paths:
        return None

    for manage_path in manage_paths:
        manage_root = PurePosixPath(manage_path).parent
        match = DJANGO_SETTINGS_PATTERN.search(files[manage_path])
        settings_path = match.group(1).replace(".", "/") + ".py" if match else None
        root_prefix = "" if str(manage_root) == "." else f"{manage_root}/"
        settings_available = any(
            path == f"{root_prefix}{settings_path}" or path.startswith(root_prefix) and PurePosixPath(path).name == "settings.py"
            for path in python_paths
        )
        if settings_available:
            return {"manage_path": manage_path, "project_root": str(manage_root)}
    return {
        "status": "project_incomplete",
        "error": "Django project files were detected, but a complete manage.py and settings.py project was not supplied.",
    }


def _entry_file(files: dict[str, str], language: str, requested: str) -> str:
    source_paths = [path for path in files if is_source_path(path)]
    candidates = {
        "javascript": ("index.js", "main.js", "app.js"),
        "typescript": ("index.ts", "main.ts", "app.ts"),
        "java": ("Main.java",),
        "c": ("main.c",),
        "cpp": ("main.cpp", "main.cc", "main.cxx"),
        "go": ("main.go",),
        "csharp": ("Program.cs", "Main.cs"),
    }.get(language, ())
    by_basename = {PurePosixPath(path).name.lower(): path for path in source_paths}
    for candidate in candidates:
        if candidate.lower() in by_basename:
            return by_basename[candidate.lower()]
    if requested in source_paths and PurePosixPath(requested).suffix.lower() not in {".h", ".hh", ".hpp", ".hxx"}:
        return requested
    return next(
        (path for path in source_paths if PurePosixPath(path).suffix.lower() not in {".h", ".hh", ".hpp", ".hxx"}),
        requested,
    )


def _dotnet_assembly_name(files: dict[str, str], project_path: str) -> str:
    default_name = PurePosixPath(project_path).stem
    try:
        root = ET.fromstring(files[project_path])
    except ET.ParseError:
        return default_name
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "AssemblyName":
            continue
        name = (element.text or "").strip()
        if name and re.fullmatch(r"[A-Za-z0-9._-]+", name):
            return name
    return default_name


def _java_entrypoint_name(filename: str, source: str) -> str:
    package = re.search(r"(?m)^\s*package\s+([A-Za-z_][\w.]*)\s*;", source)
    main_method = re.search(
        r"\bstatic\s+(?:(?:public|protected|private|final|synchronized)\s+)*void\s+main\s*\(",
        source,
    )
    class_name = PurePosixPath(filename).stem
    if main_method:
        declarations = list(re.finditer(
            r"(?m)^[ \t]*(?:(?:public|protected|private|abstract|final|sealed|non-sealed)\s+)*"
            r"(?:class|enum|record)\s+([A-Za-z_$][\w$]*)\b",
            source[:main_method.start()],
        ))
        if declarations:
            class_name = declarations[-1].group(1)
    return f"{package.group(1)}.{class_name}" if package else class_name


def _javascript_analysis_globals(files: dict[str, str], source_paths: list[str]) -> tuple[str, ...]:
    source = "\n".join(files[path] for path in source_paths)
    browser_project = any(
        PurePosixPath(path).suffix.lower() == ".html"
        for path in files
    )
    for path, content in files.items():
        if PurePosixPath(path).name.lower() != "package.json":
            continue
        try:
            manifest = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not isinstance(manifest, dict):
            continue
        dependencies = {
            **(manifest.get("dependencies") if isinstance(manifest.get("dependencies"), dict) else {}),
            **(manifest.get("devDependencies") if isinstance(manifest.get("devDependencies"), dict) else {}),
        }
        browser_project |= bool(
            manifest.get("browser")
            or {"react", "react-dom", "vue", "@angular/core", "svelte", "next", "nuxt"} & dependencies.keys()
        )
    uses_browser_apis = BROWSER_API_PATTERN.search(source)
    if browser_project or uses_browser_apis:
        return (*NODE_GLOBALS, *BROWSER_GLOBALS)
    return NODE_GLOBALS


def _uses_browser_apis(files: dict[str, str]) -> bool:
    return bool(BROWSER_API_PATTERN.search("\n".join(
        content for path, content in files.items() if is_source_path(path)
    )))


def _browser_execution_plan(
    request: CodeRequest,
    language: str,
    filename: str,
) -> dict:
    html_paths = [
        path for path in request.files
        if PurePosixPath(path).suffix.lower() == ".html"
    ]
    html_path = next(
        (path for path in html_paths if PurePosixPath(path).name.lower() == "index.html"),
        html_paths[0] if html_paths else "",
    )
    workspace = "/sandbox/{execution_id}"
    if language == "typescript":
        command = (
            'root="$1"; output="$2"; entry="$3"; html="$4"; shift 4; '
            'mkdir -p "$output" && '
            'tsc --target ES2020 --module ES2020 --skipLibCheck '
            '--rootDir "$root" --outDir "$output" "$@" && '
            'exec node /app/browser_runner.cjs "$root" "$output" "$entry" "$html"'
        )
        arguments = [
            workspace,
            "/tmp/browser-out",
            str(PurePosixPath(filename).with_suffix(".js")),
            html_path,
            *[
                f"{workspace}/{path}"
                for path in request.files
                if PurePosixPath(path).suffix.lower() in {".ts", ".tsx"}
            ],
        ]
    else:
        command = 'exec node /app/browser_runner.cjs "$1" "" "$2" "$3"'
        arguments = [workspace, filename, html_path]
    return {
        "status": None,
        "filename": filename,
        "image": STANDALONE_RUNTIME_IMAGE,
        "command": ["sh", "-c", command, "sandbox-run", *arguments],
        "working_directory": "/sandbox",
        "check_type": "browser_execution",
        "minimum_timeout": 45,
        "pids_limit": 128,
        "validator": "Chromium headless browser",
        "execution_context": "uploaded_html" if html_path else "generated_dom_harness",
    }


def _language_execution_plan(request: CodeRequest, language: str) -> dict:
    adapter = adapter_for_language(language)
    if adapter is None or adapter["support"] == "unsupported":
        return {
            "status": "unsupported_project",
            "error": f"No validation adapter is registered for language {language!r}.",
        }
    filename = _entry_file(request.files, language, request.filename)
    entry = f"/sandbox/{{execution_id}}/{filename}"
    input_path = "/sandbox/{execution_id}/input.txt"
    source_paths = [
        f"/sandbox/{{execution_id}}/{path}"
        for path in request.files
        if is_source_path(path)
    ]
    go_project_paths = [
        f"/sandbox/{{execution_id}}/{path}"
        for path in request.files
        if PurePosixPath(path).name.lower() in {"go.mod", "go.sum"}
    ]
    if request.analysis_only:
        javascript_globals = " ".join(
            f"--global {name}"
            for name in _javascript_analysis_globals(request.files, [
                path for path in request.files if is_source_path(path)
            ])
        )
        analyzers = {
            "javascript": (
                f'for file do report=$(eslint --no-config-lookup --no-warn-ignored --stdin --stdin-filename "$file" {javascript_globals} --rule "no-undef:error" --rule "no-unused-vars:error" --format json < "$file"); code=$?; [ "$code" -le 1 ] || {{ printf "%s\\n" "$report" >&2; exit "$code"; }}; printf "%s" "$report" | node -e \'let input="";process.stdin.on("data",chunk=>input+=chunk);process.stdin.on("end",()=>{{for(const file of JSON.parse(input)){{for(const message of file.messages){{console.log(file.filePath+":"+(message.line||1)+":"+(message.column||1)+":["+(message.ruleId||"eslint")+"] "+message.message)}}}}}})\' || exit $?; done',
                source_paths,
                "ESLint",
            ),
            "typescript": (
                'printf "%s\\n" \'const parser = require("/usr/local/lib/node_modules/@typescript-eslint/parser/dist/index.js");\' \'const plugin = require("/usr/local/lib/node_modules/@typescript-eslint/eslint-plugin/dist/index.js");\' \'module.exports = [{ files: ["**/*.ts", "**/*.tsx"], languageOptions: { parser }, plugins: { "@typescript-eslint": plugin }, rules: { "no-unused-vars": "off", "@typescript-eslint/no-unused-vars": "error" } }];\' > /tmp/eslint-typescript.config.cjs; for file do report=$(eslint --no-config-lookup --config /tmp/eslint-typescript.config.cjs --stdin --stdin-filename "$file" --format json < "$file"); code=$?; [ "$code" -le 1 ] || { printf "%s\\n" "$report" >&2; exit "$code"; }; printf "%s" "$report" | node -e \'let input="";process.stdin.on("data",chunk=>input+=chunk);process.stdin.on("end",()=>{for(const file of JSON.parse(input)){for(const message of file.messages){console.log(file.filePath+":"+(message.line||1)+":"+(message.column||1)+":["+(message.ruleId||"eslint")+"] "+message.message)}}})\' || exit $?; done; tsc --noEmit --target ES2020 --module commonjs --skipLibCheck "$@"',
                [path for path in source_paths if PurePosixPath(path).suffix.lower() in {".ts", ".tsx"}],
                "ESLint TypeScript + TypeScript compiler",
            ),
            "java": (
                'checkstyle -c /opt/coding-validation-checkstyle.xml "$@"; style=$?; pmd check --no-cache -d "/sandbox/{execution_id}" -R rulesets/java/quickstart.xml -f text; pmd_status=$?; mkdir -p /tmp/java-out && javac -Xlint:all -d /tmp/java-out "$@"; compile=$?; spotbugs_status=0; if [ "$compile" -eq 0 ]; then spotbugs -textui -xml:withMessages -effort:max -low /tmp/java-out > /tmp/spotbugs-report.xml 2>/tmp/spotbugs.stderr; spotbugs_status=$?; if [ "$spotbugs_status" -eq 0 ]; then PYTHONPATH=/app python -m app.spotbugs_report < /tmp/spotbugs-report.xml; else cat /tmp/spotbugs.stderr >&2; fi; fi; [ "$style" -eq 0 ] && [ "$pmd_status" -eq 0 ] && [ "$compile" -eq 0 ] && [ "$spotbugs_status" -eq 0 ]',
                [path for path in source_paths if path.lower().endswith(".java")],
                "Checkstyle + PMD + SpotBugs + javac",
            ),
            "c": (
                'cppcheck --error-exitcode=0 --enable=warning,style,performance,portability --template=gcc --quiet "$@" 2>&1; cppcheck_status=$?; clang-tidy --checks=clang-analyzer-* "$@" -- -std=c11; tidy_status=$?; clang --analyze -Xanalyzer -analyzer-output=text "$@" -std=c11; analyzer_status=$?; [ "$cppcheck_status" -eq 0 ] && [ "$tidy_status" -eq 0 ] && [ "$analyzer_status" -eq 0 ]',
                [path for path in source_paths if path.lower().endswith(".c")],
                "Cppcheck + Clang-Tidy + Clang Static Analyzer",
            ),
            "cpp": (
                'cppcheck --error-exitcode=0 --enable=warning,style,performance,portability --template=gcc --quiet "$@" 2>&1; cppcheck_status=$?; clang-tidy --checks=clang-analyzer-* "$@" -- -std=c++17; tidy_status=$?; clang++ --analyze -Xanalyzer -analyzer-output=text "$@" -std=c++17; analyzer_status=$?; [ "$cppcheck_status" -eq 0 ] && [ "$tidy_status" -eq 0 ] && [ "$analyzer_status" -eq 0 ]',
                [path for path in source_paths if PurePosixPath(path).suffix.lower() in {".cc", ".cpp", ".cxx"}],
                "Cppcheck + Clang-Tidy + Clang Static Analyzer",
            ),
            "go": (
                'mkdir -p /tmp/go-src; for file do relative=${file#/sandbox/{execution_id}/}; case "$relative" in *_test.go) relative="${relative%_test.go}_validation.go";; esac; mkdir -p "/tmp/go-src/$(dirname "$relative")"; cp "$file" "/tmp/go-src/$relative"; done; if [ ! -f /tmp/go-src/go.mod ]; then printf "module sandbox.validation\\n\\ngo 1.19\\n" > /tmp/go-src/go.mod; fi; cd /tmp/go-src && go vet ./...; vet_status=$?; golangci-lint run --timeout=45s --no-config ./...; lint_status=$?; [ "$vet_status" -eq 0 ] && [ "$lint_status" -eq 0 ]',
                [
                    *[path for path in source_paths if path.lower().endswith(".go")],
                    *go_project_paths,
                ],
                "go vet + golangci-lint",
            ),
            "csharp": (
                'mcs -r:System.Data -warn:4 -target:library -out:/tmp/validation.dll "$@"',
                [path for path in source_paths if path.lower().endswith(".cs")],
                "Mono C# compiler",
            ),
        }
        if language == "csharp":
            project_paths = [
                f"/sandbox/{{execution_id}}/{path}"
                for path in request.files
                if path.lower().endswith((".csproj", ".sln"))
            ]
            if project_paths:
                analyzers["csharp"] = (
                    'mkdir -p /tmp/home/.local/share/NuGet/Migrations /tmp/dotnet-home /tmp/nuget-packages /tmp/dotnet-out /tmp/dotnet-obj && : > /tmp/home/.local/share/NuGet/Migrations/1 && dotnet build "$1" --nologo --verbosity quiet --output /tmp/dotnet-out -p:EnableNETAnalyzers=true -p:AnalysisLevel=latest-all -p:BaseIntermediateOutputPath=/tmp/dotnet-obj/',
                    [project_paths[0]],
                    ".NET SDK Roslyn analyzers",
                )
            else:
                analyzers["csharp"] = (
                    'mkdir -p /tmp/home/.local/share/NuGet/Migrations /tmp/csharp-src /tmp/dotnet-home /tmp/nuget-packages /tmp/dotnet-out /tmp/dotnet-obj; : > /tmp/home/.local/share/NuGet/Migrations/1; for file do relative=${file#/sandbox/{execution_id}/}; mkdir -p "/tmp/csharp-src/$(dirname "$relative")"; cp "$file" "/tmp/csharp-src/$relative"; done; printf "%s\\n" \'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework><EnableNETAnalyzers>true</EnableNETAnalyzers><AnalysisLevel>latest-all</AnalysisLevel></PropertyGroup></Project>\' > /tmp/csharp-src/Validation.csproj; dotnet build /tmp/csharp-src/Validation.csproj --nologo --verbosity quiet --output /tmp/dotnet-out -p:EnableNETAnalyzers=true -p:AnalysisLevel=latest-all -p:BaseIntermediateOutputPath=/tmp/dotnet-obj/',
                    [path for path in source_paths if path.lower().endswith(".cs")],
                    ".NET SDK Roslyn analyzers",
                )
        command, arguments, toolchain = analyzers[language]
        if not arguments:
            return {
                "status": "unsupported_project",
                "error": f"No source files were supplied for {language} analysis.",
            }
        return {
            "status": None,
            "filename": filename,
            "image": STANDALONE_RUNTIME_IMAGE,
            "command": ["sh", "-c", command, "sandbox-analyze", *arguments],
            "working_directory": "/sandbox",
            "check_type": "static_analysis",
            "minimum_timeout": 180 if language == "java" else 60 if language in {"go", "csharp"} else 0,
            "validator": toolchain,
        }
    if request.validation_only:
        compiler_plans = {
            "javascript": (
                'for file do node --check "$file" || exit $?; done',
                source_paths,
                "Node.js syntax checker",
            ),
            "typescript": (
                'tsc --noEmit --target ES2020 --module commonjs --skipLibCheck "$@"',
                [path for path in source_paths if PurePosixPath(path).suffix.lower() in {".ts", ".tsx"}],
                "TypeScript compiler",
            ),
            "java": (
                'mkdir -p /tmp/java-out && javac -Xlint:all -d /tmp/java-out "$@"',
                [path for path in source_paths if path.lower().endswith(".java")],
                "JDK compiler",
            ),
            "c": (
                'gcc -std=c11 -Wall -Wextra -fsyntax-only "$@"',
                [path for path in source_paths if path.lower().endswith(".c")],
                "GCC",
            ),
            "cpp": (
                'g++ -std=c++17 -Wall -Wextra -fsyntax-only "$@"',
                [path for path in source_paths if PurePosixPath(path).suffix.lower() in {".cc", ".cpp", ".cxx"}],
                "G++",
            ),
            "go": (
                'mkdir -p /tmp/go-src; index=0; for file do index=$((index + 1)); cp "$file" "/tmp/go-src/source-${index}.go"; done; export GO111MODULE=off; go build -o /tmp/go-validation /tmp/go-src/*.go',
                [path for path in source_paths if path.lower().endswith(".go")],
                "Go compiler",
            ),
            "csharp": (
                'mcs -r:System.Data -target:library -out:/tmp/validation.dll "$@"',
                [path for path in source_paths if path.lower().endswith(".cs")],
                "Mono C# compiler",
            ),
        }
        if language == "csharp":
            project_paths = [
                f"/sandbox/{{execution_id}}/{path}"
                for path in request.files
                if path.lower().endswith((".csproj", ".sln"))
            ]
            if project_paths:
                compiler_plans["csharp"] = (
                    'mkdir -p /tmp/home /tmp/dotnet-home /tmp/nuget-packages /tmp/dotnet-out /tmp/dotnet-obj && dotnet build "$1" --nologo --verbosity quiet --output /tmp/dotnet-out -p:EnableNETAnalyzers=true -p:AnalysisLevel=latest-all -p:BaseIntermediateOutputPath=/tmp/dotnet-obj/',
                    [project_paths[0]],
                    ".NET SDK Roslyn analyzers",
                )
        command, arguments, toolchain = compiler_plans[language]
        if not arguments:
            return {
                "status": "unsupported_project",
                "error": f"No compilable source files were supplied for {language}.",
            }
        return {
            "status": None,
            "filename": filename,
            "image": STANDALONE_RUNTIME_IMAGE,
            "command": ["sh", "-c", command, "sandbox-run", *arguments],
            "working_directory": "/sandbox",
            "check_type": "static_compilation",
            "minimum_timeout": 45 if language == "go" else 0,
            "validator": toolchain,
        }
    if language in {"javascript", "typescript"} and _uses_browser_apis(request.files):
        return _browser_execution_plan(request, language, filename)
    commands = {
        "javascript": (
            'exec node "$1" < "$2"',
            [entry, input_path],
        ),
        "typescript": (
            'mkdir -p /tmp/ts-out && tsc --target ES2020 --module commonjs --skipLibCheck --rootDir "$3" --outDir /tmp/ts-out "$1" && exec node "/tmp/ts-out/$2" < "$4"',
            [
                entry,
                str(PurePosixPath(filename).with_suffix(".js")),
                "/sandbox/{execution_id}",
                input_path,
            ],
        ),
        "java": (
            'input=$1; main_class=$2; shift 2; mkdir -p /tmp/java-out && javac -d /tmp/java-out "$@" && exec java -cp /tmp/java-out "$main_class" < "$input"',
            [
                input_path,
                _java_entrypoint_name(filename, request.files[filename]),
                *[
                    f"/sandbox/{{execution_id}}/{path}"
                    for path in request.files
                    if path.lower().endswith(".java")
                ],
            ],
        ),
        "c": (
            'input=$1; shift; gcc -std=c11 "$@" -o /tmp/program && exec /tmp/program < "$input"',
            [
                input_path,
                *[
                    f"/sandbox/{{execution_id}}/{path}"
                    for path in request.files
                    if path.lower().endswith(".c")
                ],
            ],
        ),
        "cpp": (
            'input=$1; shift; g++ -std=c++17 "$@" -o /tmp/program && exec /tmp/program < "$input"',
            [
                input_path,
                *[
                    f"/sandbox/{{execution_id}}/{path}"
                    for path in request.files
                    if PurePosixPath(path).suffix.lower() in {".cc", ".cpp", ".cxx"}
                ],
            ],
        ),
        "go": (
            'mkdir -p /tmp/go-src; index=0; while [ "$#" -gt 1 ]; do index=$((index + 1)); cp "$1" "/tmp/go-src/source-${index}.go"; shift; done; export GO111MODULE=off; go run /tmp/go-src/*.go < "$1"',
            [*source_paths, input_path],
        ),
        "csharp": (
            'input=$1; shift; mcs -r:System.Data -out:/tmp/program.exe "$@" && exec mono /tmp/program.exe < "$input"',
            [
                input_path,
                *[
                    f"/sandbox/{{execution_id}}/{path}"
                    for path in request.files
                    if path.lower().endswith(".cs")
                ],
            ],
        ),
    }
    dotnet_projects = [
        path for path in request.files
        if path.lower().endswith(".csproj")
    ]
    if language == "csharp" and dotnet_projects:
        project_path = dotnet_projects[0]
        commands["csharp"] = (
            'mkdir -p /tmp/home/.local/share/NuGet/Migrations /tmp/dotnet-home /tmp/nuget-packages /tmp/dotnet-out /tmp/dotnet-obj && : > /tmp/home/.local/share/NuGet/Migrations/1 && dotnet build "$1" --nologo --verbosity quiet --output /tmp/dotnet-out -p:EnableNETAnalyzers=true -p:AnalysisLevel=latest-all -p:BaseIntermediateOutputPath=/tmp/dotnet-obj/ && exec dotnet "/tmp/dotnet-out/$2.dll" < "$3"',
            [
                f"/sandbox/{{execution_id}}/{project_path}",
                _dotnet_assembly_name(request.files, project_path),
                input_path,
            ],
        )
    if language not in commands:
        return {"status": "unsupported_project", "error": f"No isolated runtime is configured for {language}."}
    command, arguments = commands[language]
    return {
        "status": None,
        "filename": filename,
        "image": STANDALONE_RUNTIME_IMAGE,
        "command": ["sh", "-c", command, "sandbox-run", *arguments],
        "working_directory": "/sandbox",
        "check_type": "compile_and_execute",
        "minimum_timeout": 45 if language == "go" else 0,
        "validator": "isolated compile and execution",
    }


def _execution_plan(request: CodeRequest) -> dict:
    language = detect_language(list(request.files))
    if language is None:
        return {"status": "unsupported_project", "error": "No supported source files were supplied."}
    if language != "python":
        return _language_execution_plan(request, language)
    django = _django_project(request.files)
    if django and django.get("status"):
        return django
    dependency_error = _dependency_error(request.files)
    if dependency_error:
        return {"status": dependency_error[0], "error": dependency_error[1]}
    if django:
        manage_path = django["manage_path"]
        project_root = django["project_root"]
        if request.test_cases:
            return {"status": "unsupported_project", "error": "Django projects run the configured manage.py check; standalone test_cases are not applied."}
        return {
            "status": None,
            "filename": manage_path,
            "image": DJANGO_RUNTIME_IMAGE,
            "command": [
                "sh",
                "-c",
                'cd "$1" && exec python "$2" "$3"',
                "sandbox-run",
                f"/sandbox/{{execution_id}}/{project_root}",
                f"/sandbox/{{execution_id}}/{manage_path}",
                "check",
            ],
            "working_directory": "/sandbox",
            "check_type": "django_check",
        }

    selected_path = request.filename
    if PurePosixPath(selected_path).name.lower() in DJANGO_MODULES:
        return {"status": "project_incomplete", "error": "Framework modules cannot be executed directly; provide the complete project entrypoint."}
    return {
        "status": None,
        "filename": selected_path,
        "image": DJANGO_RUNTIME_IMAGE if any(PurePosixPath(path).name.lower() in MANIFEST_NAMES for path in request.files) else STANDALONE_RUNTIME_IMAGE,
        "command": None,
        "working_directory": None,
        "check_type": "script_execution",
    }


def _execution_error(status: str, error: str, check_type: str) -> dict:
    return {
        "status": status,
        "check_type": check_type,
        "executed": False,
        "passed": False,
        "exit_code": None,
        "stdout": "",
        "stderr": error,
        "error": error,
        "error_type": "DependencyMissing" if status == "dependency_missing" else status,
    }


def _replace_execution_id(value: str | None, execution_id: str) -> str | None:
    return value.replace("{execution_id}", execution_id) if value else value


def _run_options(plan: dict, execution_id: str) -> dict:
    options = {
        "image": plan["image"],
        "command": [part.replace("{execution_id}", execution_id) for part in plan["command"]] if plan["command"] else None,
        "working_directory": _replace_execution_id(plan["working_directory"], execution_id),
    }
    if "pids_limit" in plan:
        options["pids_limit"] = plan["pids_limit"]
    return options


def _execute_test_cases(request: CodeRequest, execution_id: str, plan: dict) -> dict:
    results = []
    deadline = time.monotonic() + MAX_TIMEOUT
    for index, test in enumerate(request.test_cases, 1):
        remaining_seconds = int(deadline - time.monotonic())
        if remaining_seconds <= 0:
            results.append({
                "test_case": index,
                "input": test.input,
                "expected": test.expected,
                "actual": "",
                "passed": None,
                "execution_passed": False,
                "comparison_status": "not_run",
                "status": "not_run",
                "exit_code": None,
                "error_type": "Timeout",
                "stderr": "The test suite exceeded its total execution time limit.",
            })
            continue
        test_execution_id = f"{execution_id}-{index}"
        options = _run_options(plan, test_execution_id)
        if plan["command"] is None:
            options = {}
        result = run_code(
            request.files,
            test.input,
            min(max(request.timeout, plan.get("minimum_timeout", 0)), remaining_seconds),
            test_execution_id,
            plan["filename"],
            **options,
        )
        actual = result["stdout"].strip()
        execution_passed = result.get("status") == "completed" and result.get("exit_code") == 0
        comparison_status = (
            "not_provided"
            if test.expected is None
            else "passed"
            if execution_passed and actual == test.expected.strip()
            else "failed"
        )
        results.append({
            "test_case": index,
            "input": test.input,
            "expected": test.expected,
            "actual": actual,
            "passed": comparison_status == "passed" if test.expected is not None else None,
            "execution_passed": execution_passed,
            "comparison_status": comparison_status,
            "status": result["status"],
            "exit_code": result.get("exit_code"),
            "error_type": result["error_type"],
            "stderr": result["stderr"],
        })

    total_passed = sum(item["comparison_status"] == "passed" for item in results)
    total_failed = sum(
        item["comparison_status"] == "failed"
        or (not item["execution_passed"] and item["status"] != "not_run")
        for item in results
    )
    unverified = sum(item["comparison_status"] == "not_provided" for item in results)
    statuses = {item["status"] for item in results}
    if "unavailable" in statuses:
        status = "unavailable"
    elif "timeout" in statuses:
        status = "timeout"
    elif "not_run" in statuses:
        status = "timeout"
    elif total_failed:
        status = "failed"
    else:
        status = "completed"
    comparison_status = (
        "failed" if total_failed
        else "incomplete"
        if any(item["comparison_status"] == "not_run" for item in results)
        else "not_provided" if unverified
        else "passed"
    )
    execution_passed = all(item["execution_passed"] for item in results)
    return {
        "status": status,
        "check_type": "test_cases",
        "executed": all(item["status"] in {"completed", "failed", "timeout"} for item in results),
        "execution_status": "passed" if execution_passed else "failed",
        "passed_execution": execution_passed,
        "comparison_status": comparison_status,
        "total_tests": len(results),
        "passed": total_passed,
        "failed": total_failed,
        "unverified": unverified,
        "exit_code": 0 if execution_passed else 1,
        "error": (
            "One or more expected-output comparisons failed."
            if comparison_status == "failed"
            else "The test suite exceeded its total execution time limit."
            if comparison_status == "incomplete"
            else None
        ),
        "results": results,
    }


@app.post("/execute")
def execute_code(request: CodeRequest):
    execution_id = str(uuid.uuid4())
    plan = _execution_plan(request)
    if plan["status"]:
        check_type = "django_check" if _django_project(request.files) else "script_execution"
        return _execution_error(plan["status"], plan["error"], check_type)

    if not request.test_cases:
        options = _run_options(plan, execution_id)
        if plan["command"] is None:
            options = {}
        timeout = (
            DJANGO_CHECK_TIMEOUT
            if plan["check_type"] == "django_check"
            else max(request.timeout, plan.get("minimum_timeout", 0))
        )
        result = run_code(request.files, request.stdin, timeout, execution_id, plan["filename"], **options)
        if (
            plan["check_type"] in {"static_compilation", "static_analysis"}
            and result.get("exit_code") == 127
            and re.search(r"(not found|no such file or directory)", result.get("stderr", ""), re.IGNORECASE)
        ):
            result.update({
                "status": "unavailable",
                "executed": False,
                "passed": False,
                "error_type": "ToolchainUnavailable",
                "error": result["stderr"][:MAX_OUTPUT],
            })
        if (
            str(plan.get("validator", "")).startswith(".NET SDK")
            and "NuGet-Migrations" in result.get("stderr", "")
            and "Microsoft.DotNet.Configurer" in result.get("stderr", "")
        ):
            result.update({
                "status": "unavailable",
                "executed": False,
                "passed": False,
                "error_type": "ToolchainUnavailable",
                "error": (
                    ".NET SDK project builds are unavailable in the hardened "
                    "non-root sandbox because NuGet cannot initialize its "
                    "first-run mutex."
                ),
            })
        return {
            **result,
            "check_type": plan["check_type"],
            "validator": plan.get("validator"),
            "execution_context": plan.get("execution_context"),
            "language": detect_language(list(request.files)),
        }
    return _execute_test_cases(request, execution_id, plan)