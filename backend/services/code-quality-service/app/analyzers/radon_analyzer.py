"""
Runs Radon cyclomatic-complexity analysis. Radon has no CLI JSON error
channel of its own, so this wraps the Python API directly instead of
shelling out, which is more reliable in a sandboxed service.
"""
from radon.complexity import cc_visit

# Radon grades: A (1-5) .. F (41+). Anything C or worse is worth flagging.
_GRADE_SEVERITY = {
    "A": None,
    "B": None,
    "C": "low",
    "D": "medium",
    "E": "high",
    "F": "critical",
}


def _rank(complexity: int) -> str:
    if complexity <= 5:
        return "A"
    if complexity <= 10:
        return "B"
    if complexity <= 20:
        return "C"
    if complexity <= 30:
        return "D"
    if complexity <= 40:
        return "E"
    return "F"


def run_radon(files: dict[str, str]) -> list[dict]:
    issues: list[dict] = []

    for rel_path, content in files.items():
        try:
            blocks = cc_visit(content)
        except SyntaxError as exc:
            issues.append(
                {
                    "source": "radon",
                    "file": rel_path,
                    "line_number": getattr(exc, "lineno", None),
                    "code": "RADON_SYNTAX_ERROR",
                    "message": f"Could not parse file for complexity analysis: {exc}",
                    "severity_hint": "high",
                }
            )
            continue

        for block in blocks:
            grade = _rank(block.complexity)
            severity = _GRADE_SEVERITY.get(grade)
            if severity is None:
                continue  # A/B grades are healthy, don't report
            issues.append(
                {
                    "source": "radon",
                    "file": rel_path,
                    "line_number": block.lineno,
                    "code": f"COMPLEXITY_{grade}",
                    "message": (
                        f"'{block.name}' has cyclomatic complexity "
                        f"{block.complexity} (grade {grade})"
                    ),
                    "severity_hint": severity,
                }
            )

    return issues
