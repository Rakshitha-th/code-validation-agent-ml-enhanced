from app.spotbugs_report import parse_report


def test_spotbugs_parser_preserves_multiple_findings_and_locations():
    report = b"""<?xml version="1.0"?>
    <BugCollection>
      <BugInstance type="NP_NULL_ON_SOME_PATH" abbrev="NP" category="CORRECTNESS" priority="1" classname="example.A">
        <ShortMessage>Possible null dereference</ShortMessage>
        <LongMessage>Dereference of a value that may be null</LongMessage>
        <SourceLine sourcepath="example/A.java" sourcefile="A.java" start="14" end="14"/>
      </BugInstance>
      <BugInstance type="EI_EXPOSE_REP" abbrev="EI" category="MALICIOUS_CODE" priority="2" classname="example.B">
        <Message>Exposes internal representation</Message>
        <SourceLine sourcefile="B.java" start="?" end="?"/>
      </BugInstance>
    </BugCollection>"""

    result = parse_report(report)

    assert result["error"] is None
    assert len(result["findings"]) == 2
    assert result["findings"][0] == {
        "tool": "spotbugs",
        "rule_id": "NP_NULL_ON_SOME_PATH",
        "type": "NP_NULL_ON_SOME_PATH",
        "abbreviation": "NP",
        "category": "CORRECTNESS",
        "message": "Dereference of a value that may be null",
        "priority": 1,
        "severity": "high",
        "file": "example/A.java",
        "line": 14,
        "end_line": 14,
        "class": "example.A",
        "method": None,
    }
    assert result["findings"][1]["file"] == "B.java"
    assert result["findings"][1]["line"] is None
    assert result["findings"][1]["severity"] == "medium"


def test_spotbugs_parser_keeps_finding_without_source_location():
    result = parse_report(
        '<BugCollection><BugInstance type="RCN_REDUNDANT_NULLCHECK_WOULD_HAVE_BEEN_A_NPE" '
        'priority="3" classname="example.C"><Message>Redundant null check</Message>'
        '</BugInstance></BugCollection>'
    )

    finding = result["findings"][0]
    assert finding["file"] is None
    assert finding["line"] is None
    assert finding["rule_id"] == "RCN_REDUNDANT_NULLCHECK_WOULD_HAVE_BEEN_A_NPE"
    assert finding["severity"] == "low"


def test_spotbugs_parser_handles_namespaces():
    result = parse_report(
        '<sb:BugCollection xmlns:sb="urn:spotbugs"><sb:BugInstance type="X" priority="2">'
        '<sb:Message>Namespaced finding</sb:Message></sb:BugInstance></sb:BugCollection>'
    )

    assert result["findings"][0]["message"] == "Namespaced finding"


def test_spotbugs_parser_ignores_java_output_noise_and_repeated_xml_declarations():
    report = b"""Picked up JAVA_TOOL_OPTIONS: -Xmx192m
<?xml version="1.0"?>
<BugCollection>
  <?xml version="1.0"?>
  <BugInstance type="NP_NULL_ON_SOME_PATH" priority="1">
    <Message>Possible null dereference</Message>
  </BugInstance>
</BugCollection>
"""

    result = parse_report(report)

    assert result["error"] is None
    assert len(result["findings"]) == 1
    assert result["findings"][0]["rule_id"] == "NP_NULL_ON_SOME_PATH"


def test_spotbugs_parser_reports_malformed_or_empty_xml_without_raising():
    malformed = parse_report(b"<BugCollection><BugInstance")
    empty = parse_report(b"")

    assert malformed["findings"] == []
    assert malformed["error"].startswith("Malformed SpotBugs XML:")
    assert empty["findings"] == []
    assert "empty" in empty["error"]
