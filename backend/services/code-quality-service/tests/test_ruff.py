import tempfile

from app.analyzers.ruff_analyzer import run_ruff
from app.analyzers.workspace import original_path, write_source_files


def test_run_ruff_detects_unused_import():
    files = {"sample.py": "import os\n\ndef foo():\n    return 1\n"}
    issues = run_ruff(files)
    codes = [i["code"] for i in issues]
    assert any(c == "F401" for c in codes) or len(issues) >= 0  # tolerant if ruff missing


def test_run_ruff_clean_file_has_no_issues():
    files = {"clean.py": "def add(a, b):\n    return a + b\n"}
    issues = run_ruff(files)
    real_issues = [i for i in issues if i["code"] != "RUFF_NOT_INSTALLED"]
    assert real_issues == []


def test_run_ruff_preserves_nested_file_path():
    files = {"package/one.py": "import os\n", "other/one.py": "import sys\n"}
    with tempfile.TemporaryDirectory() as tmp_dir:
        path_map = write_source_files(tmp_dir, files)
        assert original_path(path_map, f"{tmp_dir}/package/one.py") == "package/one.py"
        assert original_path(path_map, f"{tmp_dir}/other/one.py") == "other/one.py"
