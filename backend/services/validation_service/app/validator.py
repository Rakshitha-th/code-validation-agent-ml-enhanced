import ast
import io
import os
import re
import subprocess
import sys
import tempfile
import tokenize
from typing import Dict, List, Tuple


def get_severity(code: str) -> str:
    severities = {
        "E999": "HIGH",
        "F821": "HIGH",
        "F822": "HIGH",
        "F823": "HIGH",
        "F401": "MEDIUM",
        "F811": "MEDIUM",
        "F841": "LOW",
        "W292": "LOW",
        "E501": "LOW",
        "E302": "LOW",
        "E305": "LOW",
    }
    return severities.get(code, "MEDIUM" if code.startswith("F") else "LOW")


def get_recommendation(code: str) -> str:
    recommendations = {
        "E999": "Fix the Python syntax error.",
        "F401": "Remove the unused import.",
        "F821": "Check the variable name and define it before use.",
        "F841": "Remove the unused local variable or use it.",
        "E501": "Reduce the line length.",
        "E302": "Add the required blank lines.",
        "E305": "Add the required blank lines after the definition.",
        "W292": "Add a newline at the end of the file."
    }
    return recommendations.get(
        code,
        "Review the code and follow Python best practices."
    )


def validate_syntax(code: str, filename: str) -> Tuple[List[Dict], ast.AST | None]:
    try:
        return [], ast.parse(code, filename=filename)
    except SyntaxError as error:
        return [{
            "type": "SyntaxError",
            "severity": "HIGH",
            "file": filename,
            "line": error.lineno,
            "column": error.offset,
            "message": error.msg,
            "code": "SYNTAX",
            "recommendation": "Fix the syntax error."
        }], None


def run_flake8(code: str, filename: str) -> List[Dict]:
    findings = []
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            encoding="utf-8"
        ) as file:
            file.write(code)
            temp_path = file.name

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "flake8",
                temp_path,
                "--format=%(row)d:%(col)d:%(code)s:%(text)s"
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )

        if result.returncode not in (0, 1):
            detail = result.stderr.strip() or f"Flake8 exited with status {result.returncode}."
            return [{
                "type": "ValidationError",
                "severity": "HIGH",
                "file": filename,
                "line": None,
                "column": None,
                "message": f"Flake8 could not complete: {detail[:300]}",
                "code": "VALIDATOR_ERROR",
                "recommendation": "Check the validation environment and retry.",
            }]

        for line in result.stdout.strip().splitlines():
            parts = line.split(":", 3)

            if len(parts) != 4:
                continue

            row, col, error_code, message = parts

            findings.append({
                "type": "CodeQuality",
                "severity": get_severity(error_code),
                "file": filename,
                "line": int(row),
                "column": int(col),
                "message": f"{error_code}: {message}",
                "code": error_code,
                "recommendation": get_recommendation(error_code)
            })

    except subprocess.TimeoutExpired:
        findings.append({
            "type": "ValidationError",
            "severity": "HIGH",
            "file": filename,
            "line": None,
            "column": None,
            "message": "Flake8 validation timed out after 20 seconds.",
            "code": "VALIDATOR_TIMEOUT",
            "recommendation": "Reduce the input size or inspect the validation environment.",
        })
    except Exception as error:
        findings.append({
            "type": "ValidationError",
            "severity": "MEDIUM",
            "file": filename,
            "line": None,
            "column": None,
            "message": str(error),
            "code": "VALIDATOR_ERROR",
            "recommendation": "Check the validation environment."
        })

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    return findings


class CodeSmellVisitor(ast.NodeVisitor):

    def __init__(self, filename: str):
        self.filename = filename
        self.findings = []
        self.function_names: dict[tuple[str, ...], set[str]] = {}
        self.scope_stack: list[str] = []
        self.function_count = 0
        self.class_count = 0
        self.import_count = 0
        self.max_function_length = 0
        self.max_nesting_depth = 0

    def add_finding(
        self,
        smell_type,
        severity,
        node,
        message,
        recommendation,
        code
    ):
        self.findings.append({
            "type": smell_type,
            "severity": severity,
            "file": self.filename,
            "line": getattr(node, "lineno", None),
            "column": getattr(node, "col_offset", None),
            "message": message,
            "code": code,
            "recommendation": recommendation
        })

    def visit_Import(self, node):
        self.import_count += len(node.names)
        self.generic_visit(node)

    visit_ImportFrom = visit_Import

    def visit_FunctionDef(self, node):
        self.function_count += 1

        scope = tuple(self.scope_stack)
        names = self.function_names.setdefault(scope, set())
        if node.name in names:
            self.add_finding(
                "CodeSmell",
                "MEDIUM",
                node,
                f"Duplicate function name '{node.name}'.",
                "Rename one of the duplicate functions.",
                "DUPLICATE_FUNCTION"
            )

        names.add(node.name)

        if node.end_lineno:
            length = node.end_lineno - node.lineno + 1
            self.max_function_length = max(
                self.max_function_length,
                length
            )

            if length > 50:
                self.add_finding(
                    "CodeSmell",
                    "MEDIUM",
                    node,
                    f"Function '{node.name}' is too long.",
                    "Split the function into smaller functions.",
                    "LONG_FUNCTION"
                )

        args = node.args.posonlyargs + node.args.args

        if len(args) > 5:
            self.add_finding(
                "CodeSmell",
                "MEDIUM",
                node,
                f"Function '{node.name}' has too many arguments.",
                "Reduce the number of function parameters.",
                "TOO_MANY_ARGUMENTS"
            )

        defaults = [*node.args.defaults, *(item for item in node.args.kw_defaults if item is not None)]
        for default in defaults:
            if self._is_mutable_default(default):
                self.add_finding(
                    "CodeSmell",
                    "MEDIUM",
                    node,
                    f"Function '{node.name}' uses a mutable default.",
                    "Use None as the default value.",
                    "MUTABLE_DEFAULT"
                )

        nesting = self.calculate_nesting_depth(node)
        self.max_nesting_depth = max(
            self.max_nesting_depth,
            nesting
        )

        if nesting > 4:
            self.add_finding(
                "CodeSmell",
                "MEDIUM",
                node,
                f"Function '{node.name}' has deep nesting.",
                "Reduce nested conditions and loops.",
                "DEEP_NESTING"
            )

        complexity = self.calculate_complexity(node)

        if complexity > 10:
            self.add_finding(
                "Complexity",
                "MEDIUM",
                node,
                f"Function '{node.name}' has high complexity.",
                "Simplify the logic or split the function.",
                "HIGH_COMPLEXITY"
            )

        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node):
        self.class_count += 1
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_Pass(self, node):
        self.add_finding(
            "CodeSmell",
            "LOW",
            node,
            "Pass statement detected.",
            "Remove it or add the intended implementation.",
            "PASS_STATEMENT"
        )
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if node.type is None:
            self.add_finding(
                "CodeSmell",
                "MEDIUM",
                node,
                "Bare except block detected.",
                "Catch a specific exception.",
                "BARE_EXCEPT"
            )
        elif (
            isinstance(node.type, ast.Name)
            and node.type.id in {"Exception", "BaseException"}
        ) or (
            isinstance(node.type, ast.Tuple)
            and any(
                isinstance(item, ast.Name) and item.id in {"Exception", "BaseException"}
                for item in node.type.elts
            )
        ):
            self.add_finding(
                "CodeSmell",
                "MEDIUM",
                node,
                "Overly broad exception handler detected.",
                "Catch the specific exception types expected from this operation.",
                "BROAD_EXCEPTION"
            )
        self.generic_visit(node)

    @staticmethod
    def _is_mutable_default(node):
        return isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)) or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"list", "dict", "set"}
        )

    def calculate_nesting_depth(self, node):
        maximum = 0
        nesting_nodes = (
            ast.If,
            ast.For,
            ast.AsyncFor,
            ast.While,
            ast.With,
            ast.AsyncWith,
            ast.Try,
        )

        def walk(current, depth):
            nonlocal maximum
            for child in ast.iter_child_nodes(current):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                child_depth = depth + 1 if isinstance(child, nesting_nodes) else depth
                maximum = max(maximum, child_depth)
                walk(child, child_depth)

        walk(node, 0)
        return maximum

    def calculate_complexity(self, node):
        complexity = 1

        def walk(current):
            nonlocal complexity
            for child in ast.iter_child_nodes(current):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                if isinstance(
                    child,
                    (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler),
                ):
                    complexity += 1
                elif isinstance(child, ast.BoolOp):
                    complexity += len(child.values) - 1
                walk(child)

        walk(node)

        return complexity


def detect_todo_comments(code: str, filename: str) -> List[Dict]:
    findings = []
    pattern = re.compile(r"\b(TODO|FIXME|HACK)\b", re.IGNORECASE)

    try:
        comments = tokenize.generate_tokens(io.StringIO(code).readline)
        for token in comments:
            if token.type != tokenize.COMMENT:
                continue
            match = pattern.search(token.string)
            if not match:
                continue
            keyword = match.group(1).upper()
            findings.append({
                "type": "CodeSmell",
                "severity": "LOW",
                "file": filename,
                "line": token.start[0],
                "column": token.start[1] + match.start() + 1,
                "message": f"{keyword} comment detected.",
                "code": f"{keyword}_COMMENT",
                "recommendation": "Resolve the pending task."
            })
    except (tokenize.TokenError, IndentationError):
        return findings

    return findings


def validate_code(code: str, filename: str):
    findings = []

    syntax_findings, tree = validate_syntax(
        code,
        filename
    )

    findings.extend(syntax_findings)

    if tree is None:
        return findings, {
            "lines_of_code": len(code.splitlines()),
            "functions": 0,
            "classes": 0,
            "imports": 0,
            "max_function_length": 0,
            "max_nesting_depth": 0
        }

    findings.extend(
        run_flake8(code, filename)
    )

    visitor = CodeSmellVisitor(filename)
    visitor.visit(tree)

    findings.extend(visitor.findings)

    findings.extend(
        detect_todo_comments(code, filename)
    )

    metrics = {
        "lines_of_code": len(code.splitlines()),
        "functions": visitor.function_count,
        "classes": visitor.class_count,
        "imports": visitor.import_count,
        "max_function_length": visitor.max_function_length,
        "max_nesting_depth": visitor.max_nesting_depth
    }

    return findings, metrics