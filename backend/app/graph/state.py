"""LangGraph state (skeleton). The graph itself is built in Phase 5.

findings uses an append reducer so parallel agent nodes can each return
{"findings": [...]} and LangGraph merges them instead of overwriting.
"""
import operator
from typing import Annotated, TypedDict

from app.schemas.finding import Finding


class ReviewState(TypedDict, total=False):
    review_id: str
    repo: str
    pr_number: int
    exec_mode: str                      # sequential | parallel | routed
    hunks: list[dict]                   # parsed diff hunks (Phase 1)
    context: dict                       # retrieved RAG context (Phase 3)
    agents_to_run: list[str]            # set by router (Phase 5)
    findings: Annotated[list[Finding], operator.add]
    errors: Annotated[list[str], operator.add]
    overall_confidence: float
    gate_decision: str                  # auto_post | needs_human
