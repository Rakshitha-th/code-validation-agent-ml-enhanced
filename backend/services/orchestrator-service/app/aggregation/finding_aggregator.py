"""
Merges findings from Security, Code Quality, and Bug/Resource agents
into a single ordered list, sorted by severity so the most important
issues drive the repair step first.
"""
import json

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def aggregate(
    security_findings: list[dict],
    quality_findings: list[dict],
    resource_findings: list[dict],
) -> list[dict]:
    merged = [*security_findings, *quality_findings, *resource_findings]

    # Collapse only exact repeated payloads; same-location findings can still
    # represent distinct categories, tools, or evidence.
    seen = set()
    deduped = []
    for finding in merged:
        key = json.dumps(finding, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)

    deduped.sort(key=lambda f: _SEVERITY_ORDER.get(f.get("severity", "medium"), 2))
    return deduped


def summarize(findings: list[dict]) -> dict:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        sev = f.get("severity", "medium")
        if sev in counts:
            counts[sev] += 1
    return {"total": len(findings), "by_severity": counts}
