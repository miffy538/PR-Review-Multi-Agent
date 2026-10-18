"""Hallucination guard: check every LLM finding against the real diff before we keep it.

The model must quote the code line it is pointing at. We then confirm:
  1. the file is one the model was actually shown,
  2. the quote really appears on an ADDED line of that file (not invented, not unchanged code),
  3. the cited line range contains that line. If it is off but the quote exists nearby, we
     relocate the finding to the real line (LLMs are often off by a few lines but right about
     the code); if the nearest match is far away, we drop it.
This is the "validate cited lines exist, drop hallucinated ones" step from the project plan, and
every drop is counted so the evaluation can report a hallucination rate.
"""
import re
from collections import Counter
from dataclasses import dataclass, field

from app.schemas.finding import Evidence, Finding
from app.schemas.llm_output import RawFinding

MAX_RELOCATE_DISTANCE = 10   # lines
MAX_SPAN = 25                # cap how many lines one finding may cover

_WS = re.compile(r"\s+")
_LINE_PREFIX = re.compile(r"^\s*(?:\+?L\d+\s*[|:]\s*|\+\s)")


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


def clean_quote(q: str) -> str:
    """First non-empty line, minus any `+L12 |` / `+ ` marker the model copied along."""
    first = next((ln for ln in q.splitlines() if ln.strip()), "")
    return _norm(_LINE_PREFIX.sub("", first))


def _matches(quote: str, line_text: str) -> bool:
    lt = _norm(line_text)
    if not quote or not lt:
        return False
    return quote in lt or (len(lt) >= 6 and lt in quote)


def resolve_file(name: str, shown: dict[str, dict[int, str]]) -> str | None:
    n = name.strip().removeprefix("a/").removeprefix("b/").removeprefix("./")
    if n in shown:
        return n
    tails = [p for p in shown if p.endswith("/" + n)]      # model wrote "db.py" for "app/db.py"
    return tails[0] if len(tails) == 1 else None


@dataclass
class ValidationResult:
    kept: list[Finding] = field(default_factory=list)
    dropped: Counter = field(default_factory=Counter)      # reason -> count
    dropped_detail: list[dict] = field(default_factory=list)
    relocated: int = 0


def _drop(res: ValidationResult, reason: str, raw: RawFinding) -> None:
    res.dropped[reason] += 1
    res.dropped_detail.append({"reason": reason, "file": raw.file, "line_start": raw.line_start,
                               "category": raw.category, "summary": raw.summary[:120]})


def validate_findings(raw_findings: list[RawFinding],
                      shown: dict[str, dict[int, str]]) -> ValidationResult:
    res = ValidationResult()
    for raw in raw_findings:
        path = resolve_file(raw.file, shown)
        if path is None:
            _drop(res, "file_not_in_diff", raw)
            continue
        quote = clean_quote(raw.evidence_quote)
        hits = sorted(ln for ln, text in shown[path].items() if _matches(quote, text))
        if not hits:
            _drop(res, "quote_not_in_added_lines", raw)
            continue

        start, end = raw.line_start, raw.line_end or raw.line_start
        if any(start <= h <= end for h in hits):
            anchor = next(h for h in hits if start <= h <= end)
        else:
            anchor = min(hits, key=lambda h: abs(h - start))
            if abs(anchor - start) > MAX_RELOCATE_DISTANCE:
                _drop(res, "lines_do_not_match_quote", raw)
                continue
            end = anchor + min(end - start, MAX_SPAN)
            start = anchor
            res.relocated += 1
        end = min(end, start + MAX_SPAN)

        res.kept.append(Finding(
            agent=raw.agent, category=raw.category, severity=raw.severity, file=path,
            line_start=start, line_end=end, summary=raw.summary, suggestion=raw.suggestion,
            confidence=round(raw.confidence, 3), rationale=raw.rationale,
            evidence=[Evidence(type="llm", detail=f"L{anchor}: {shown[path][anchor].strip()}"[:300])]))
    return res
