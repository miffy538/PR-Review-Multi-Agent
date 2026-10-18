"""Unified diff -> per-file hunks with exact line numbers (new-file numbering)."""
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from unidiff import PatchSet

LANGS = {".py": "python", ".js": "javascript", ".ts": "typescript", ".java": "java",
         ".go": "go", ".rb": "ruby", ".md": "markdown", ".json": "json",
         ".yml": "yaml", ".yaml": "yaml", ".sql": "sql", ".html": "html", ".css": "css"}
MAX_LINE_CHARS = 300


@dataclass
class ParsedFile:
    path: str
    old_path: str | None
    status: str                       # added | modified | removed | renamed
    language: str | None
    additions: int
    deletions: int
    hunks: list[dict] = field(default_factory=list)
    touch_lines: list[int] = field(default_factory=list)   # new-file lines to map to functions

    @property
    def reviewable(self) -> bool:
        # Phase 1 scope: Python only, and nothing to review in a deleted file.
        return self.language == "python" and self.status != "removed"


def _strip(p: str | None) -> str | None:
    if p is None or p == "/dev/null":
        return None
    return p[2:] if p[:2] in ("a/", "b/") else p


def parse_diff(diff_text: str) -> list[ParsedFile]:
    out: list[ParsedFile] = []
    for pf in PatchSet(diff_text):
        if pf.is_added_file:
            status = "added"
        elif pf.is_removed_file:
            status = "removed"
        elif pf.is_rename:
            status = "renamed"
        else:
            status = "modified"
        new_path, old_path = _strip(pf.target_file), _strip(pf.source_file)
        path = new_path or old_path or pf.path
        parsed = ParsedFile(
            path=path,
            old_path=old_path if status == "renamed" else None,
            status=status,
            language=LANGS.get(PurePosixPath(path).suffix.lower()),
            additions=pf.added,
            deletions=pf.removed,
        )
        touched: set[int] = set()
        for h in pf:
            added = [{"line": ln.target_line_no, "text": ln.value.rstrip("\n")[:MAX_LINE_CHARS]}
                     for ln in h if ln.is_added]
            removed = [{"line": ln.source_line_no, "text": ln.value.rstrip("\n")[:MAX_LINE_CHARS]}
                       for ln in h if ln.is_removed]
            parsed.hunks.append({
                "old_start": h.source_start, "old_lines": h.source_length,
                "new_start": h.target_start, "new_lines": h.target_length,
                "section": h.section_header or None,
                "added": added, "removed": removed,
            })
            if added:
                touched.update(a["line"] for a in added)
            elif removed and status != "removed":
                # Deletion-only hunk: attribute it to the line where the code used to be.
                touched.add(max(h.target_start, 1))
        parsed.touch_lines = sorted(touched)
        out.append(parsed)
    return out
