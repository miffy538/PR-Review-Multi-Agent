"""Turn the Phase 1 data (hunks + changed functions) into the text the LLM reviews.

Format goals: every added line carries its exact new-file line number (`+L42`), so the model can
cite real lines, and the validator can check those citations against the same data.
"""
from dataclasses import dataclass, field

TITLE_MAX = 200


@dataclass
class ReviewFile:
    path: str
    status: str
    language: str | None
    reviewable: bool
    additions: int
    deletions: int
    hunks: list[dict]
    functions: list[dict]

    @classmethod
    def from_row(cls, row) -> "ReviewFile":
        return cls(row["path"], row["status"], row["language"], row["reviewable"], row["additions"],
                   row["deletions"], row["hunks"] or [], row["changed_functions"] or [])

    def added_lines(self) -> dict[int, str]:
        return {a["line"]: a["text"] for h in self.hunks for a in h["added"]}


@dataclass
class RenderedInput:
    text: str
    shown: dict[str, dict[int, str]] = field(default_factory=dict)   # path -> {line: text}, for validation
    skipped_files: list[str] = field(default_factory=list)           # reviewable but did not fit
    context_truncated: bool = False


def _diff_section(f: ReviewFile) -> str:
    out = [f"### FILE {f.path} ({f.status})"]
    for h in f.hunks:
        where = f" ({h['section']})" if h.get("section") else ""
        out.append(f"@@ new lines {h['new_start']}-{h['new_start'] + max(h['new_lines'] - 1, 0)}{where} @@")
        out += [f"-      | {r['text']}" for r in h["removed"]]
        out += [f"+L{a['line']:<4}| {a['text']}" for a in h["added"]]
    return "\n".join(out)


def _function_section(f: ReviewFile, fn: dict) -> str | None:
    if fn.get("start_line") is None or not fn.get("source"):
        return None                                    # module-level code: the diff already shows it
    changed = set(fn["changed_lines"])
    out = [f"### {f.path} :: {fn['qualname']} (lines {fn['start_line']}-{fn['end_line']})"]
    for i, line in enumerate(fn["source"].split("\n")):
        n = fn["start_line"] + i
        out.append(f"{'*' if n in changed else ' '}{n:>5} | {line}")
    if fn.get("truncated"):
        out.append("       | ... (function truncated)")
    return "\n".join(out)


def render_input(title: str | None, files: list[ReviewFile], max_chars: int) -> RenderedInput:
    reviewable = [f for f in files if f.reviewable and f.added_lines()]
    reviewable_ids = {id(f) for f in reviewable}

    header = [f"PR title: {(title or '(none)')[:TITLE_MAX]}", "", "Changed files:"]
    header += [f"- {f.path} ({f.status}, +{f.additions}/-{f.deletions})"
               + ("" if id(f) in reviewable_ids else " [not reviewed: not Python or nothing added]")
               for f in files]

    budget = max_chars - sum(len(x) + 1 for x in header) - 200
    diff_parts: list[str] = []
    shown: dict[str, dict[int, str]] = {}
    skipped: list[str] = []
    for f in reviewable:
        sec = _diff_section(f)
        if len(sec) + 1 > budget:
            skipped.append(f.path)
            continue
        budget -= len(sec) + 1
        diff_parts.append(sec)
        shown[f.path] = f.added_lines()

    func_parts: list[str] = []
    truncated = False
    for f in reviewable:
        if f.path not in shown:
            continue
        for fn in f.functions:
            sec = _function_section(f, fn)
            if sec is None:
                continue
            if len(sec) + 1 > budget:
                truncated = True
                continue
            budget -= len(sec) + 1
            func_parts.append(sec)

    body = ["<pr_diff>", *header, "", "## DIFF", *diff_parts]
    if func_parts:
        body += ["", "## FULL CODE OF CHANGED FUNCTIONS (context; * = changed in this PR)", *func_parts]
    if truncated:
        body.append("\n(Some function context was omitted for size.)")
    if skipped:
        body.append(f"\n(Not shown, too large: {', '.join(skipped)})")
    body.append("</pr_diff>")
    return RenderedInput(text="\n".join(body), shown=shown, skipped_files=skipped,
                         context_truncated=truncated)
