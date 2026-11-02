from app.analyzers.pylint_analyzer import run_pylint


def test_run_pylint_returns_list():
    files = {"sample.py": "x=1\ndef f():\n  pass\n"}
    issues = run_pylint(files)
    assert isinstance(issues, list)


def test_run_pylint_handles_empty_files():
    issues = run_pylint({})
    assert issues == []
