from app.analyzers.radon_analyzer import run_radon

_COMPLEX_FUNC = """
def complex_fn(a, b, c, d, e):
    if a:
        if b:
            if c:
                if d:
                    if e:
                        return 1
                    else:
                        return 2
                elif d == 2:
                    return 3
            elif c == 5:
                return 4
        elif b == 9:
            return 5
    elif a == 2:
        return 6
    for i in range(10):
        if i % 2 == 0:
            print(i)
        elif i % 3 == 0:
            print("fizz")
        else:
            print("other")
    return 0
"""


def test_run_radon_flags_high_complexity():
    files = {"complex.py": _COMPLEX_FUNC}
    issues = run_radon(files)
    assert any(i["code"].startswith("COMPLEXITY") for i in issues)


def test_run_radon_ignores_simple_functions():
    files = {"simple.py": "def add(a, b):\n    return a + b\n"}
    issues = run_radon(files)
    assert issues == []


def test_run_radon_handles_syntax_error():
    files = {"broken.py": "def f(:\n    pass"}
    issues = run_radon(files)
    assert any(i["code"] == "RADON_SYNTAX_ERROR" for i in issues)
