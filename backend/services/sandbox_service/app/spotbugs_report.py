"""Convert SpotBugs XML into a stable JSON diagnostic payload."""

import json
import re
import sys
import xml.etree.ElementTree as ET
from typing import Any


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element.iter() if _local_name(child.tag) == name]


def _severity(priority: int | None) -> str:
    if priority == 1:
        return "high"
    if priority == 2:
        return "medium"
    if priority is not None and priority >= 3:
        return "low"
    return "unknown"


def parse_report(report: bytes | str) -> dict[str, Any]:
    """Parse each bug instance, retaining diagnostics with partial locations."""
    data = report.encode("utf-8") if isinstance(report, str) else report
    root_match = re.search(rb"<(?P<prefix>[A-Za-z_][\w.-]*:)?BugCollection\b", data)
    if root_match is None:
        start = data.find(b"<")
        if start < 0:
            return {"tool": "spotbugs", "findings": [], "error": "SpotBugs returned empty or non-XML output."}
        xml_data = data[start:]
    else:
        prefix = root_match.group("prefix") or b""
        closing_tag = b"</" + prefix + b"BugCollection>"
        end = data.find(closing_tag, root_match.start())
        xml_data = data[root_match.start():end + len(closing_tag)] if end >= 0 else data[root_match.start():]
        xml_data = re.sub(rb"<\?xml[^?]*\?>", b"", xml_data)

    if not xml_data:
        return {"tool": "spotbugs", "findings": [], "error": "SpotBugs returned empty or non-XML output."}

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as error:
        return {"tool": "spotbugs", "findings": [], "error": f"Malformed SpotBugs XML: {error}."}

    findings = []
    for bug in _children(root, "BugInstance"):
        priority_text = bug.get("priority")
        try:
            priority = int(priority_text) if priority_text is not None else None
        except ValueError:
            priority = None

        message = next(
            (
                item.text.strip()
                for item in _children(bug, "LongMessage")
                if item.text and item.text.strip()
            ),
            None,
        ) or next(
            (
                item.text.strip()
                for item in _children(bug, "Message")
                if item.text and item.text.strip()
            ),
            bug.get("type", "SpotBugs finding"),
        )
        source = next(iter(_children(bug, "SourceLine")), None)
        source_path = None
        start_line = None
        end_line = None
        if source is not None:
            source_path = source.get("sourcepath") or source.get("sourcefile")
            try:
                start_line = int(source.get("start")) if source.get("start") not in {None, "?"} else None
            except ValueError:
                start_line = None
            try:
                end_line = int(source.get("end")) if source.get("end") not in {None, "?"} else None
            except ValueError:
                end_line = None

        findings.append({
            "tool": "spotbugs",
            "rule_id": bug.get("type") or bug.get("abbrev") or "SPOTBUGS",
            "type": bug.get("type") or "SpotBugs",
            "abbreviation": bug.get("abbrev"),
            "category": bug.get("category"),
            "message": message,
            "priority": priority,
            "severity": _severity(priority),
            "file": source_path,
            "line": start_line,
            "end_line": end_line,
            "class": bug.get("classname"),
            "method": bug.get("method"),
        })

    return {"tool": "spotbugs", "findings": findings, "error": None}


def main() -> None:
    result = parse_report(sys.stdin.buffer.read())
    print(
        "CODE_VALIDATION_SPOTBUGS:"
        + json.dumps(result, ensure_ascii=True, separators=(",", ":"))
    )


if __name__ == "__main__":
    main()
