from pydantic import ValidationError
import pytest

from shared.language_rules import load_language_rules
from shared.languages import LANGUAGE_ADAPTERS
from shared.schemas.finding import FindingCategory
from app.main import AnalyzeRequest, ProjectFile, analyze, health


def request_for(code: str) -> AnalyzeRequest:
    return AnalyzeRequest(
        project_id="bug-resource-test",
        files=[ProjectFile(path="main.py", content=code)],
    )


def test_health_reports_bug_resource_service():
    assert health() == {"status": "healthy", "service": "bug-resource-service"}


@pytest.mark.parametrize("language", sorted(LANGUAGE_ADAPTERS))
def test_each_supported_language_has_a_validated_rule_dataset(language):
    rules = load_language_rules(language)

    assert rules
    assert all(rule["id"] == rule_id for rule_id, rule in rules.items())


def test_reports_mutable_defaults_swallowed_exceptions_and_file_risks():
    response = analyze(request_for(
        "def process(items=[]):\n"
        "    try:\n"
        "        source = open('data.txt')\n"
        "        return source.readlines()\n"
        "    except Exception:\n"
        "        pass\n"
    ))

    assert response.agent == "bug_resource"
    assert response.findings_count == len(response.findings)
    assert {finding.issue for finding in response.findings} == {"BUG001", "BUG002", "RES001", "RES002"}
    assert {finding.category for finding in response.findings} == {"bug", "resource"}
    assert all(finding.file == "main.py" for finding in response.findings)


def test_context_managed_bounded_read_has_no_resource_findings():
    response = analyze(request_for(
        "def read_header(path):\n"
        "    with open(path) as source:\n"
        "        return source.read(128)\n"
    ))

    assert response.findings == []


def test_syntax_error_is_returned_as_a_bug_finding():
    response = analyze(request_for("def broken(:\n    pass\n"))

    assert response.findings_count == 1
    assert response.findings[0].issue == "BUG000"
    assert response.findings[0].line_number == 1


def test_file_paths_must_be_relative_python_files():
    with pytest.raises(ValidationError):
        ProjectFile(path="../outside.py", content="print('no')")

    with pytest.raises(ValidationError):
        ProjectFile(path="notes.txt", content="not python")


def test_generic_language_checks_detect_silent_exception_handling():
    response = analyze(AnalyzeRequest(
        project_id="javascript-bug-resource",
        files=[ProjectFile(
            path="main.js",
            content="try { run(); } catch (error) {}\nwhile (true) { work(); }\n",
        )],
    ))

    assert {finding.issue for finding in response.findings} == {"BUG004", "BUG005"}


@pytest.mark.parametrize(
    ("path", "source", "expected_issues"),
    [
        (
            "main.java",
            "class Sample { void run() { try { call(); } catch (Exception ex) {} "
            "while (true) { work(); } } void call() {} void work() {} }",
            {"BUG004", "BUG005"},
        ),
        (
            "main.js",
            "function run() { try { call(); } catch (error) {} "
            "while (true) { work(); } }",
            {"BUG004", "BUG005"},
        ),
        (
            "main.ts",
            "function run(): void { try { call(); } catch (error) {} "
            "while (true) { work(); } }",
            {"BUG004", "BUG005"},
        ),
        (
            "main.tsx",
            "const App = () => { try { call(); } catch (error) {} "
            "while (true) { work(); } return <div />; };",
            {"BUG004", "BUG005"},
        ),
        ("main.c", "void run(void) { while (1) { work(); } }", {"BUG005"}),
        ("main.cpp", "void run() { while (true) { work(); } }", {"BUG005"}),
        ("main.go", "package main\nfunc run() { for { work() } }\nfunc work() {}",
         {"BUG005"}),
        (
            "main.cs",
            "class Sample { void Run() { try { Call(); } catch (Exception) {} "
            "while (true) { Work(); } } void Call() {} void Work() {} }",
            {"BUG004", "BUG005"},
        ),
    ],
)
def test_tree_sitter_checks_cover_supported_non_python_languages(path, source, expected_issues):
    response = analyze(AnalyzeRequest(
        project_id="tree-sitter-bug-resource",
        files=[ProjectFile(path=path, content=source)],
    ))

    assert {finding.issue for finding in response.findings} == expected_issues
    assert all(finding.tool == "bug_resource_tree_sitter" for finding in response.findings)


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("Main.java", "import java.nio.file.Files; class Main { void read() throws Exception { Files.readAllBytes(path); } }"),
        ("main.js", "const fs = require('fs'); fs.readFileSync(path);"),
        ("main.ts", "import * as fs from 'fs'; fs.readFile(path, callback);"),
        ("Program.cs", "class Program { void Read() { System.IO.File.ReadAllText(path); } }"),
        ("main.go", 'package main\nimport "os"\nfunc read() { _, _ = os.ReadFile("data") }'),
    ],
)
def test_generic_checks_detect_unbounded_file_reads(path, source):
    response = analyze(AnalyzeRequest(
        project_id="unbounded-file-read",
        files=[ProjectFile(path=path, content=source)],
    ))

    assert [finding.issue for finding in response.findings] == ["RES003"]
    assert response.findings[0].category == FindingCategory.RESOURCE


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("Main.java", "class Main { void read() throws Exception { input.read(buffer); } }"),
        ("main.js", "const fs = require('fs'); fs.createReadStream(path);"),
        ("Program.cs", "class Program { void Read() { stream.Read(buffer, 0, buffer.Length); } }"),
        ("main.go", 'package main\nimport "os"\nfunc read() { f, _ := os.Open("data"); defer f.Close() }'),
    ],
)
def test_generic_checks_do_not_flag_streaming_or_bounded_reads(path, source):
    response = analyze(AnalyzeRequest(
        project_id="bounded-file-read",
        files=[ProjectFile(path=path, content=source)],
    ))

    assert response.findings == []


@pytest.mark.parametrize(
    ("path", "source", "expected_issue"),
    [
        ("main.c", "void work(void) { int *items = malloc(16); }", "RES004"),
        ("main.cpp", "void work() { auto *item = new int; }", "RES004"),
    ],
)
def test_generic_checks_detect_unreleased_heap_allocations(path, source, expected_issue):
    response = analyze(AnalyzeRequest(
        project_id="unreleased-allocation",
        files=[ProjectFile(path=path, content=source)],
    ))

    assert [finding.issue for finding in response.findings] == [expected_issue]
    assert response.findings[0].category == FindingCategory.RESOURCE


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("main.c", "void work(void) { int *items = malloc(16); free(items); }"),
        ("main.cpp", "void work() { auto *item = new int; delete item; }"),
    ],
)
def test_generic_checks_accept_matching_heap_deallocation(path, source):
    response = analyze(AnalyzeRequest(
        project_id="released-allocation",
        files=[ProjectFile(path=path, content=source)],
    ))

    assert response.findings == []


def test_project_manifests_are_not_parsed_as_source_files():
    response = analyze(AnalyzeRequest(
        project_id="csharp-project-manifest",
        files=[
            ProjectFile(path="main.cs", content="class Sample { void Run() {} }"),
            ProjectFile(path="Sample.csproj", content="<Project />"),
        ],
    ))

    assert response.findings == []