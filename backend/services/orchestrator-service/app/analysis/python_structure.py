import ast


def inspect_python_files(files: dict[str, str]) -> dict:
    """Return structural context without executing submitted source code."""
    structure = {"files": [], "functions": [], "classes": [], "imports": [], "errors": []}

    for path, content in files.items():
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError as exc:
            structure["errors"].append({
                "file": path,
                "line": exc.lineno,
                "message": f"Python syntax error: {exc.msg}",
            })
            structure["files"].append({"path": path, "parseable": False})
            continue

        functions = [
            {"file": path, "name": node.name, "line": node.lineno}
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        classes = [
            {"file": path, "name": node.name, "line": node.lineno}
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        ]
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        structure["files"].append({
            "path": path,
            "parseable": True,
            "line_count": len(content.splitlines()),
        })
        structure["functions"].extend(functions)
        structure["classes"].extend(classes)
        structure["imports"].extend({"file": path, "module": module} for module in sorted(set(imports)))

    return structure