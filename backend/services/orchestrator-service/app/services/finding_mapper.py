"""Translate agent-specific findings into the shared orchestrator contract."""


def normalize_finding(
    finding: dict,
    path: str,
    prefix: str,
    index: int,
    category: str,
) -> dict:
    issue = finding.get("issue") or finding.get("type") or finding.get("code") or "Agent finding"
    severity = str(finding.get("severity") or "medium").lower()
    if severity in {"informational", "info", "unknown"}:
        severity = "low"
    elif severity not in {"low", "medium", "high", "critical"}:
        severity = "medium"

    return {
        "finding_id": f"{prefix}-{index:04d}",
        "category": category,
        "severity": severity,
        "file": path,
        "line_number": finding.get("line_number", finding.get("line")),
        "issue": issue,
        "description": finding.get("description") or finding.get("message") or str(issue),
        "evidence": finding.get("evidence") or finding.get("code"),
        "recommendation": finding.get("recommendation"),
        "status": "open",
        "tool": finding.get("tool") or prefix.lower(),
        "subcategory": finding.get("subcategory") or finding.get("type"),
    }