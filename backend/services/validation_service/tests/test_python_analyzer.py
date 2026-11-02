import ast

from app.validator import CodeSmellVisitor, detect_todo_comments, get_severity


def test_duplicate_function_detection_is_scoped_to_lexical_scope():
    tree = ast.parse("""
class First:
    def run(self):
        return 1

class Second:
    def run(self):
        return 2

def duplicate():
    return 1

def duplicate():
    return 2
""")
    visitor = CodeSmellVisitor("main.py")
    visitor.visit(tree)

    duplicates = [item for item in visitor.findings if item["code"] == "DUPLICATE_FUNCTION"]
    assert len(duplicates) == 1
    assert duplicates[0]["message"] == "Duplicate function name 'duplicate'."


def test_complexity_and_nesting_ignore_nested_function_bodies():
    tree = ast.parse("""
def outer(value):
    def inner(item):
        if item and item > 0:
            for index in range(item):
                if index:
                    return True
        return False
    return value
""")
    visitor = CodeSmellVisitor("main.py")
    visitor.visit(tree)

    outer = tree.body[0]
    assert visitor.calculate_complexity(outer) == 1
    assert visitor.calculate_nesting_depth(outer) == 0
    assert visitor.calculate_complexity(outer.body[0]) > 1
    assert visitor.calculate_nesting_depth(outer.body[0]) == 3


def test_mutable_collection_call_defaults_are_detected():
    tree = ast.parse("""
def first(value=list()):
    return value

def second(value: dict = {}):
    return value
""")
    visitor = CodeSmellVisitor("main.py")
    visitor.visit(tree)

    assert len([item for item in visitor.findings if item["code"] == "MUTABLE_DEFAULT"]) == 2


def test_broad_exception_and_bare_exception_are_distinguished():
    tree = ast.parse("""
def broad():
    try:
        work()
    except Exception:
        pass

def bare():
    try:
        work()
    except:
        pass
""")
    visitor = CodeSmellVisitor("main.py")
    visitor.visit(tree)

    codes = [item["code"] for item in visitor.findings]
    assert codes.count("BROAD_EXCEPTION") == 1
    assert codes.count("BARE_EXCEPT") == 1


def test_todo_detection_only_reads_comment_tokens():
    findings = detect_todo_comments(
        'message = "TODO: not a comment"  # FIXME: handle this\n'
        '"""HACK appears in this string."""\n',
        "main.py",
    )

    assert len(findings) == 1
    assert findings[0]["code"] == "FIXME_COMMENT"
    assert findings[0]["line"] == 1


def test_python_rule_severity_uses_explicit_high_risk_mapping():
    assert get_severity("F821") == "HIGH"
    assert get_severity("F401") == "MEDIUM"
    assert get_severity("F841") == "LOW"
    assert get_severity("E501") == "LOW"
