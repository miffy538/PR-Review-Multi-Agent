import pytest
from pydantic import ValidationError

from app.schemas.finding import AgentOutput, Evidence, Finding


def make(**over):
    base = dict(
        agent="security", category="sql_injection", severity="Critical",
        file="app/db.py", line_start=42, line_end=44, summary="String-built SQL",
        confidence=0.9,
    )
    base.update(over)
    return Finding(**base)


def test_valid_finding():
    f = make(evidence=[Evidence(type="bandit", detail="B608 at line 42")])
    assert f.severity.value == "Critical"
    assert f.evidence[0].type == "bandit"


def test_bad_severity_rejected():
    with pytest.raises(ValidationError):
        make(severity="Catastrophic")


def test_line_order_enforced():
    with pytest.raises(ValidationError):
        make(line_start=50, line_end=10)


def test_confidence_range():
    with pytest.raises(ValidationError):
        make(confidence=1.5)


def test_agent_output_parses_llm_json():
    raw = '{"findings":[{"agent":"docs","category":"missing_docstring","severity":"Low",' \
          '"file":"a.py","line_start":1,"line_end":1,"summary":"No docstring","confidence":0.6}]}'
    out = AgentOutput.model_validate_json(raw)
    assert out.findings[0].agent.value == "docs"
