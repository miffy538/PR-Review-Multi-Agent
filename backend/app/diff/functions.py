"""Map changed line numbers to the enclosing Python function (via ast)."""
import ast

MAX_SOURCE_LINES = 150
MODULE = "<module>"


def _collect(tree: ast.AST) -> list[tuple[str, int, int]]:
    found: list[tuple[str, int, int]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                qual = f"{prefix}{child.name}"
                found.append((qual, start, child.end_lineno or child.lineno))
                visit(child, qual + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def map_changed_functions(source: str, changed_lines: list[int]) -> tuple[list[dict], str | None]:
    """Returns (functions, note). Each function: qualname, start_line, end_line,
    changed_lines, source, truncated. Lines outside any function go under '<module>'."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [], f"syntax error at head commit (line {e.lineno}); function mapping skipped"

    funcs = _collect(tree)
    lines = source.splitlines()
    grouped: dict[str, dict] = {}
    for ln in changed_lines:
        enclosing = [f for f in funcs if f[1] <= ln <= f[2]]
        if enclosing:
            qual, start, end = min(enclosing, key=lambda f: f[2] - f[1])   # innermost
        else:
            qual, start, end = MODULE, None, None
        g = grouped.setdefault(qual, {"qualname": qual, "start_line": start, "end_line": end,
                                      "changed_lines": []})
        g["changed_lines"].append(ln)

    result = []
    for g in grouped.values():
        if g["start_line"] is not None:
            body = lines[g["start_line"] - 1:g["end_line"]]
            g["truncated"] = len(body) > MAX_SOURCE_LINES
            g["source"] = "\n".join(body[:MAX_SOURCE_LINES])
        else:
            g["truncated"], g["source"] = False, None
        result.append(g)
    result.sort(key=lambda g: (g["start_line"] is None, g["start_line"] or 0))
    return result, None
