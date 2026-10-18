import asyncio

import pytest

from app.llm.structured import StructuredOutputError, extract_json, generate_list
from app.schemas.llm_output import RawFinding
from tests.fakes import FakeLLM, findings_json

GOOD = dict(agent="security", category="SQL Injection", severity="critical", file="app/db.py",
            line_start=5, line_end=6, evidence_quote="query = ...", summary="bad", confidence=0.9)


def run(llm):
    return asyncio.run(generate_list(llm, system="s", user="u", item_model=RawFinding))


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here you go: {"a": 1} Hope that helps') == {"a": 1}
    assert extract_json("[1, 2]") == [1, 2]
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_raw_finding_normalises_sloppy_values():
    f = RawFinding.model_validate({**GOOD, "agent": " Security ", "severity": "HIGH",
                                   "confidence": 85, "line_end": 2})
    assert f.agent.value == "security" and f.severity.value == "High"
    assert f.category == "sql_injection" and f.confidence == 0.85
    assert f.line_end == f.line_start                      # reversed range fixed


def test_one_bad_item_does_not_sink_the_rest():
    bad = {**GOOD, "severity": "Catastrophic"}
    no_quote = {k: v for k, v in GOOD.items() if k != "evidence_quote"}
    res = run(FakeLLM([findings_json(GOOD, bad, no_quote)]))
    assert len(res.items) == 1 and len(res.invalid) == 2 and not res.repaired


def test_bare_list_is_accepted():
    import json
    assert len(run(FakeLLM([json.dumps([GOOD])])).items) == 1


def test_repair_call_recovers_from_garbage():
    llm = FakeLLM(["I think the code is fine!", findings_json(GOOD)])
    res = run(llm)
    assert res.repaired and len(res.items) == 1 and len(llm.calls) == 2
    assert "rejected" in llm.calls[1]["user"]
    assert len(res.responses) == 2                          # both calls are visible for cost accounting


def test_gives_up_after_one_repair():
    llm = FakeLLM(["garbage", "still garbage"])
    with pytest.raises(StructuredOutputError):
        run(llm)
    assert len(llm.calls) == 2


def test_empty_findings_is_valid():
    assert run(FakeLLM([findings_json()])).items == []
