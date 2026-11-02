from app.analysis.python_structure import inspect_python_files


def test_python_structure_reports_functions_classes_and_imports():
    result = inspect_python_files({
        "app.py": "import os\n\nclass Service:\n    def run(self):\n        return os.getcwd()\n",
    })

    assert result["files"][0]["parseable"] is True
    assert result["classes"][0]["name"] == "Service"
    assert result["functions"][0]["name"] == "run"
    assert result["imports"][0]["module"] == "os"


def test_python_structure_reports_parse_errors_without_execution():
    result = inspect_python_files({"broken.py": "def broken(:\n    pass"})

    assert result["files"][0]["parseable"] is False
    assert result["errors"][0]["file"] == "broken.py"