import pytest

from app.main import CodeRequest, _execution_plan


@pytest.mark.parametrize(
    ("filename", "code", "command"),
    [
        ("main.js", 'console.log("ok");', "node"),
        ("main.ts", 'const value: string = "ok";', "tsc"),
        ("Main.java", "public class Main { public static void main(String[] args) {} }", "javac"),
        ("main.c", "int main(void) { return 0; }", "gcc"),
        ("main.cpp", "int main() { return 0; }", "g++"),
        ("main.go", "package main\nfunc main() {}", "go run"),
        ("Program.cs", "public class Program { public static void Main() {} }", "mcs"),
    ],
)
def test_supported_languages_have_isolated_execution_plans(filename, code, command):
    request = CodeRequest(filename=filename, code=code, files={filename: code})

    plan = _execution_plan(request)

    assert plan["status"] is None
    assert plan["check_type"] == "compile_and_execute"
    assert command in " ".join(plan["command"])
    assert plan["minimum_timeout"] == (45 if filename.endswith(".go") else 0)


def test_mixed_unrelated_languages_are_rejected():
    with pytest.raises(ValueError, match="one programming language"):
        CodeRequest(
            filename="main.py",
            code="print('ok')",
            files={"main.py": "print('ok')", "main.js": 'console.log("ok")'},
        )


@pytest.mark.parametrize(
    ("filename", "code", "checker"),
    [
        ("main.js", "const value = 1;\n", "node --check"),
        ("main.ts", "const value: number = 1;\n", "tsc --noEmit"),
        ("Main.java", "class Main {}\n", "javac -Xlint:all"),
        ("main.c", "int main(void) { return 0; }\n", "gcc -std=c11"),
        ("main.cpp", "int main() { return 0; }\n", "g++ -std=c++17"),
        ("main.go", "package main\nfunc main() {}\n", "go build"),
        ("Program.cs", "public class Program {}\n", "mcs -r:System.Data -target:library"),
    ],
)
def test_validation_only_uses_language_compiler_without_running_user_program(filename, code, checker):
    request = CodeRequest(
        filename=filename,
        code=code,
        files={filename: code},
        validation_only=True,
    )

    plan = _execution_plan(request)

    assert plan["check_type"] == "static_compilation"
    assert checker in " ".join(plan["command"])
    assert "exec " not in " ".join(plan["command"])


def test_dotnet_project_uses_dotnet_sdk_build():
    project = {
        "Program.cs": "public class Program { public static void Main() {} }\n",
        "App.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
    }
    request = CodeRequest(
        filename="Program.cs",
        code=project["Program.cs"],
        files=project,
        validation_only=True,
    )

    plan = _execution_plan(request)

    assert plan["check_type"] == "static_compilation"
    assert plan["validator"] == ".NET SDK Roslyn analyzers"
    assert "dotnet build" in " ".join(plan["command"])


@pytest.mark.parametrize(
    ("language", "filename", "files", "required_sources"),
    [
        ("java", "src/Main.java", {
            "src/Main.java": "package src; class Main { public static void main(String[] args) {} }\n",
            "src/Helper.java": "package src; class Helper {}\n",
        }, ("src/Main.java", "src/Helper.java")),
        ("c", "src/main.c", {
            "src/main.c": "int helper(void); int main(void) { return helper(); }\n",
            "src/helper.c": "int helper(void) { return 0; }\n",
        }, ("src/main.c", "src/helper.c")),
        ("cpp", "src/main.cpp", {
            "src/main.cpp": "int helper(); int main() { return helper(); }\n",
            "src/helper.cpp": "int helper() { return 0; }\n",
        }, ("src/main.cpp", "src/helper.cpp")),
        ("csharp", "Program.cs", {
            "Program.cs": "class Program { static void Main() { Helper.Run(); } }\n",
            "Helper.cs": "static class Helper { public static void Run() {} }\n",
        }, ("Program.cs", "Helper.cs")),
    ],
)
def test_execution_plans_compile_all_source_files_for_multi_file_projects(
    language, filename, files, required_sources
):
    request = CodeRequest(filename=filename, code=files[filename], files=files)

    plan = _execution_plan(request)
    command = " ".join(plan["command"])

    assert plan["status"] is None
    for source in required_sources:
        assert f"/sandbox/{{execution_id}}/{source}" in command


def test_typescript_execution_preserves_project_relative_output_paths():
    project = {
        "src/main.ts": 'import { message } from "../shared/message"; console.log(message);\n',
        "shared/message.ts": 'export const message = "ok";\n',
    }

    plan = _execution_plan(CodeRequest(
        filename="src/main.ts",
        code=project["src/main.ts"],
        files=project,
    ))
    command = " ".join(plan["command"])

    assert " --rootDir " in command
    assert "/sandbox/{execution_id}" in command
    assert '"/tmp/ts-out/$2"' in command
    assert "/sandbox/{execution_id}/src/main.ts" in command


def test_dotnet_project_execution_builds_and_runs_the_project_output():
    project = {
        "Program.cs": "class Program { static void Main() {} }\n",
        "App.csproj": (
            "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup>"
            "<OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework>"
            "<AssemblyName>SampleApp</AssemblyName></PropertyGroup></Project>"
        ),
    }

    plan = _execution_plan(CodeRequest(
        filename="Program.cs",
        code=project["Program.cs"],
        files=project,
    ))
    command = " ".join(plan["command"])

    assert "dotnet build" in command
    assert '"/tmp/dotnet-out/$2.dll"' in command
    assert "exec dotnet" in command


def test_java_execution_uses_declared_main_class_when_filename_differs():
    code = "class JavaTest { public static void main(String[] args) {} }\n"

    plan = _execution_plan(CodeRequest(
        filename="java_test.java",
        code=code,
        files={"java_test.java": code},
    ))

    assert plan["status"] is None
    assert "JavaTest" in plan["command"]


@pytest.mark.parametrize(
    ("filename", "code", "analyzer"),
    [
        ("main.js", "const unused = 1;\n", "eslint"),
        ("main.ts", "const value: number = 1;\n", "@typescript-eslint"),
        ("Main.java", "class Main {}\n", "pmd check"),
        ("main.c", "int main(void) { return 0; }\n", "clang-tidy"),
        ("main.cpp", "int main() { return 0; }\n", "clang-tidy"),
        ("main.go", "package main\nfunc main() {}\n", "golangci-lint"),
        ("Program.cs", "public class Program {}\n", "latest-all"),
    ],
)
def test_language_analysis_uses_real_static_analyzer(filename, code, analyzer):
    request = CodeRequest(
        filename=filename,
        code=code,
        files={filename: code},
        analysis_only=True,
    )

    plan = _execution_plan(request)

    assert plan["check_type"] == "static_analysis"
    assert analyzer in " ".join(plan["command"])
    if filename == "main.js":
        assert "--format json" in " ".join(plan["command"])
    if filename == "Main.java":
        assert "PYTHONPATH=/app python -m app.spotbugs_report" in " ".join(plan["command"])
        assert plan["minimum_timeout"] == 180
    if filename == "Program.cs":
        assert "/tmp/home/.local/share/NuGet/Migrations/1" in " ".join(plan["command"])
        assert "dotnet build" in " ".join(plan["command"])
    if filename == "main.ts":
        command = " ".join(plan["command"])
        assert "eslint --no-config-lookup" in command
        assert "--config /tmp/eslint-typescript.config.cjs" in command
        assert "@typescript-eslint/eslint-plugin/dist/index.js" in command
        assert "--resolve-plugins-relative-to" not in command
        assert "--no-eslintrc" not in command


def test_javascript_analysis_includes_browser_globals_when_browser_apis_are_used():
    code = 'document.getElementById("profile").textContent = "ready";\n'
    request = CodeRequest(
        filename="main.js",
        code=code,
        files={"main.js": code},
        analysis_only=True,
    )

    plan = _execution_plan(request)
    command = " ".join(plan["command"])

    assert "--global document" in command
    assert "--global XMLHttpRequest" in command
    assert "--global require" in command


@pytest.mark.parametrize(
    ("filename", "code"),
    [
        ("main.js", 'document.getElementById("user-panel");\n'),
        ("main.ts", 'document.getElementById("user-panel");\n'),
    ],
)
def test_browser_code_uses_headless_browser_execution(filename, code):
    request = CodeRequest(filename=filename, code=code, files={filename: code})

    plan = _execution_plan(request)

    assert plan["status"] is None
    assert plan["check_type"] == "browser_execution"
    assert "browser_runner.cjs" in " ".join(plan["command"])
    assert plan["validator"] == "Chromium headless browser"
    assert plan["execution_context"] == "generated_dom_harness"
    assert plan["pids_limit"] == 128


def test_browser_typescript_is_compiled_as_es_module_before_browser_execution():
    files = {"src/main.ts": 'document.body.textContent = "ok";\n'}

    plan = _execution_plan(CodeRequest(
        filename="src/main.ts",
        code=files["src/main.ts"],
        files=files,
    ))

    command = " ".join(plan["command"])
    assert plan["status"] is None
    assert plan["check_type"] == "browser_execution"
    assert "--module ES2020" in command
    assert "browser_runner.cjs" in command
    assert "/sandbox/{execution_id}/src/main.ts" in command


def test_browser_project_uses_uploaded_nested_html():
    files = {
        "web/index.html": '<script src="main.js"></script>',
        "web/main.js": 'document.body.textContent = "ok";\n',
    }

    plan = _execution_plan(CodeRequest(
        filename="web/main.js",
        code=files["web/main.js"],
        files=files,
    ))

    assert plan["status"] is None
    assert plan["check_type"] == "browser_execution"
    assert plan["execution_context"] == "uploaded_html"
    assert "web/index.html" in plan["command"][-1]


def test_javascript_analysis_does_not_enable_browser_globals_for_node_only_source():
    code = 'const value = process.env.VALUE;\nconsole.log(value);\n'
    request = CodeRequest(
        filename="main.js",
        code=code,
        files={"main.js": code},
        analysis_only=True,
    )

    plan = _execution_plan(request)

    assert "--global process" in " ".join(plan["command"])
    assert "--global document" not in " ".join(plan["command"])


def test_javascript_analysis_detects_browser_package_manifest():
    code = 'console.log("browser app");\n'
    request = CodeRequest(
        filename="main.js",
        code=code,
        files={
            "main.js": code,
            "package.json": '{"dependencies":{"react":"18.0.0"}}',
        },
        analysis_only=True,
    )

    plan = _execution_plan(request)

    assert "--global document" in " ".join(plan["command"])


def test_dotnet_analysis_uses_sdk_build():
    project = {
        "Program.cs": "public class Program {}\n",
        "App.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
    }
    request = CodeRequest(
        filename="Program.cs",
        code=project["Program.cs"],
        files=project,
        analysis_only=True,
    )

    plan = _execution_plan(request)

    assert plan["check_type"] == "static_analysis"
    assert plan["validator"] == ".NET SDK Roslyn analyzers"
    assert "dotnet build" in " ".join(plan["command"])


@pytest.mark.parametrize(
    ("mode", "expected_command"),
    [
        ("validation_only", "go build -o /tmp/go-validation"),
        ("analysis_only", "go vet ./..."),
        ("execution", "go run /tmp/go-src/*.go"),
    ],
)
def test_go_test_filenames_are_staged_as_regular_source_files(mode, expected_command):
    request = CodeRequest(
        filename="go_test.go",
        code="package main\nfunc main() {}\n",
        files={"go_test.go": "package main\nfunc main() {}\n"},
        validation_only=mode == "validation_only",
        analysis_only=mode == "analysis_only",
    )

    plan = _execution_plan(request)

    command = " ".join(plan["command"])
    assert plan["status"] is None
    assert expected_command in command
    if mode == "analysis_only":
        assert "_validation.go" in command
    else:
        assert "source-${index}.go" in command


def test_go_execution_stages_all_source_files_including_test_suffixes():
    files = {
        "go_test.go": "package main\nfunc main() { println(message()) }\n",
        "helper_test.go": "package main\nfunc message() string { return \"ok\" }\n",
    }
    request = CodeRequest(
        filename="go_test.go",
        code=files["go_test.go"],
        files=files,
    )

    plan = _execution_plan(request)
    command = " ".join(plan["command"])

    assert plan["status"] is None
    assert "while [ \"$#\" -gt 1 ]" in command
    assert "go run /tmp/go-src/*.go" in command
    assert "/sandbox/{execution_id}/go_test.go" in command
    assert "/sandbox/{execution_id}/helper_test.go" in command


@pytest.mark.parametrize(
    ("mode", "expected_command"),
    [
        ("validation_only", "mcs -r:System.Data -target:library"),
        ("analysis_only", "latest-all"),
        ("execution", "mcs -r:System.Data -out:/tmp/program.exe"),
    ],
)
def test_mono_validation_references_standard_system_data_assembly(mode, expected_command):
    request = CodeRequest(
        filename="Program.cs",
        code="using System.Data;\nclass Program {}\n",
        files={"Program.cs": "using System.Data;\nclass Program {}\n"},
        validation_only=mode == "validation_only",
        analysis_only=mode == "analysis_only",
    )

    plan = _execution_plan(request)

    assert plan["status"] is None
    assert expected_command in " ".join(plan["command"])
