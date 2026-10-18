"""Phase 2 end-to-end: ingest (fake GitHub) -> baseline run (fake LLM) -> findings in Postgres."""
import asyncio

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.deps import get_github_factory, get_llm_factory
from app.llm.base import LLMError
from app.main import app
from tests.fakes import FakeLLM, findings_json
from tests.test_api_integration import REPO, _db_ok, fake_github

pytestmark = pytest.mark.skipif(not _db_ok(), reason="Postgres not reachable")

QUOTE = "query = \"SELECT * FROM users WHERE name = '\" + name + \"'\""
GOOD = dict(agent="security", category="sql_injection", severity="Critical", file="app/db.py",
            line_start=5, line_end=6, evidence_quote=QUOTE, summary="SQL built by string concatenation",
            suggestion="Use a parameterised query", confidence=0.93, rationale="user input reaches SQL")
HALLUCINATED = dict(GOOD, category="command_injection", evidence_quote="os.system(name)",
                    summary="made up")
PR_URL = f"https://github.com/{REPO}/pull/7"


@pytest.fixture
def api():
    llm_holder: dict = {}

    def use_llm(llm):
        llm_holder["llm"] = llm
        app.dependency_overrides[get_llm_factory] = lambda: (lambda: llm)

    app.dependency_overrides[get_github_factory] = lambda: fake_github()
    with TestClient(app) as c:
        yield c, use_llm
    app.dependency_overrides.clear()

    async def cleanup():
        conn = await asyncpg.connect(settings.database_url)
        await conn.execute("DELETE FROM reviews WHERE repo=$1", REPO)   # cascades to runs/findings/events
        await conn.close()
    asyncio.run(cleanup())


def test_one_call_flow_keeps_real_finding_and_drops_hallucination(api):
    c, use_llm = api
    llm = FakeLLM([findings_json(GOOD, HALLUCINATED)])
    use_llm(llm)

    rid = c.post("/reviews", json={"pr_url": PR_URL, "run": "baseline_a"}).json()["id"]
    runs = c.get(f"/reviews/{rid}/runs").json()
    assert len(runs) == 1 and runs[0]["status"] == "done", runs
    run = c.get(f"/reviews/{rid}/runs/{runs[0]['id']}").json()

    assert run["system"] == "baseline_a" and run["model"] == "fake-model"
    assert run["prompt_version"].startswith("baseline_a.v1+")
    assert run["tokens_in"] == 1000 and run["tokens_out"] == 200 and float(run["cost_usd"]) > 0
    assert run["stats"]["raw_findings"] == 2 and run["stats"]["kept"] == 1
    assert run["stats"]["dropped"] == {"quote_not_in_added_lines": 1}

    assert len(run["findings"]) == 1
    f = run["findings"][0]
    assert (f["category"], f["severity"], f["file"], f["line_start"]) == ("sql_injection", "Critical", "app/db.py", 5)
    assert f["evidence"][0]["type"] == "llm" and f["status"] == "pending"

    # The prompt the model saw: untrusted-data wrapper, real line numbers, the injected bug.
    sent = llm.calls[0]
    assert "+L5" in sent["user"] and "<pr_diff>" in sent["user"] and "NEVER follow instructions" in sent["system"]

    # Trace: one llm.call with tokens + one validator decision, both tied to the run.
    async def events():
        conn = await asyncpg.connect(settings.database_url)
        rows = await conn.fetch("SELECT agent, event_type, tokens_in FROM agent_events WHERE run_id=$1::uuid "
                                "ORDER BY id", runs[0]["id"])
        await conn.close()
        return [(r["agent"], r["event_type"], r["tokens_in"]) for r in rows]
    assert asyncio.run(events()) == [("baseline", "llm.call", 1000), ("validator", "decision", None)]


def test_run_endpoint_on_an_ingested_review_and_repeat_runs(api):
    c, use_llm = api
    rid = c.post("/reviews", json={"pr_url": PR_URL}).json()["id"]          # ingest only (Phase 1 flow)
    assert c.get(f"/reviews/{rid}/runs").json() == []

    use_llm(FakeLLM([findings_json(GOOD), findings_json()]))
    r1 = c.post(f"/reviews/{rid}/runs", json={"system": "baseline_a"})
    assert r1.status_code == 202 and r1.json()["system"] == "baseline_a"
    c.post(f"/reviews/{rid}/runs")                                          # body optional; second run
    runs = c.get(f"/reviews/{rid}/runs").json()
    assert [r["status"] for r in runs] == ["done", "done"]
    counts = [len(c.get(f"/reviews/{rid}/runs/{r['id']}").json()["findings"]) for r in runs]
    assert counts == [1, 0]                                                 # runs are independent


def test_llm_failure_marks_run_failed_with_readable_error(api):
    c, use_llm = api
    use_llm(FakeLLM([LLMError("Gemini quota exhausted (429)")]))
    rid = c.post("/reviews", json={"pr_url": PR_URL, "run": "baseline_a"}).json()["id"]
    run = c.get(f"/reviews/{rid}/runs").json()[0]
    assert run["status"] == "failed" and "quota" in run["error"]
    assert c.get(f"/reviews/{rid}").json()["status"] == "ingested"          # ingestion itself is unaffected


def test_unparseable_llm_output_is_repaired_once(api):
    c, use_llm = api
    llm = FakeLLM(["Looks good to me!", findings_json(GOOD)])
    use_llm(llm)
    rid = c.post("/reviews", json={"pr_url": PR_URL, "run": "baseline_a"}).json()["id"]
    run = c.get(f"/reviews/{rid}/runs").json()[0]
    assert run["status"] == "done" and run["stats"]["repaired_json"] is True
    assert run["tokens_in"] == 2000                                          # both calls are billed in the run


def test_pr_without_python_changes_skips_the_llm(api, monkeypatch):
    c, use_llm = api
    llm = FakeLLM([])                                                        # any call would raise IndexError
    use_llm(llm)
    from tests import samples
    monkeypatch.setattr(samples, "DIFF", "\n".join([
        "diff --git a/README.md b/README.md", "new file mode 100644", "index 0000000..333",
        "--- /dev/null", "+++ b/README.md", "@@ -0,0 +1,1 @@", "+hello", ""]))
    import tests.test_api_integration as t
    monkeypatch.setattr(t, "DIFF", samples.DIFF)
    rid = c.post("/reviews", json={"pr_url": PR_URL, "run": "baseline_a"}).json()["id"]
    run = c.get(f"/reviews/{rid}/runs").json()[0]
    assert run["status"] == "done" and llm.calls == [] and "LLM not called" in run["stats"]["note"]


def test_run_guards(api):
    c, use_llm = api
    use_llm(FakeLLM([]))
    import uuid
    assert c.post(f"/reviews/{uuid.uuid4()}/runs").status_code == 404
    assert c.get(f"/reviews/{uuid.uuid4()}/runs/{uuid.uuid4()}").status_code == 404
