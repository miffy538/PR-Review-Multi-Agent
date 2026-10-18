"""End-to-end API tests with a fake GitHub. Need a reachable Postgres (skipped otherwise)."""
import asyncio
import hashlib
import hmac
import json
import uuid

import asyncpg
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.deps import get_github_factory
from app.integrations.github_client import GitHubClient
from app.main import app
from tests.samples import DB_PY, DIFF

REPO = "octo/demo-test"


def _db_ok() -> bool:
    async def go():
        c = await asyncpg.connect(settings.database_url, timeout=3)
        await c.close()
    try:
        asyncio.run(go())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_ok(), reason="Postgres not reachable")


def fake_github(missing_pr: bool = False):
    def handler(req: httpx.Request) -> httpx.Response:
        p, accept = req.url.path, req.headers.get("accept", "")
        if p == f"/repos/{REPO}/pulls/7":
            if missing_pr:
                return httpx.Response(404, json={"message": "Not Found"})
            if "diff" in accept:
                return httpx.Response(200, text=DIFF)
            return httpx.Response(200, json={"title": "Fix user lookup", "head": {"sha": "abc123"}})
        if p == f"/repos/{REPO}/contents/app/db.py":
            return httpx.Response(200, text=DB_PY)
        return httpx.Response(404, json={})
    return lambda: GitHubClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def client():
    app.dependency_overrides[get_github_factory] = lambda: fake_github()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

    async def cleanup():
        conn = await asyncpg.connect(settings.database_url)
        await conn.execute("DELETE FROM reviews WHERE repo=$1", REPO)
        await conn.execute("DELETE FROM webhook_deliveries WHERE delivery_id LIKE 'test-%'")
        await conn.close()
    asyncio.run(cleanup())


def test_post_review_ingests_pr(client):
    r = client.post("/reviews", json={"pr_url": f"https://github.com/{REPO}/pull/7"})
    assert r.status_code == 202
    rid = r.json()["id"]

    d = client.get(f"/reviews/{rid}").json()
    assert d["status"] == "ingested", d
    assert d["head_sha"] == "abc123" and d["title"] == "Fix user lookup"
    assert d["summary"] == {"files": 3, "reviewable_files": 1, "changed_functions": 1}

    files = {f["path"]: f for f in client.get(f"/reviews/{rid}/files").json()}
    fn = files["app/db.py"]["changed_functions"][0]
    assert fn["qualname"] == "get_user" and fn["changed_lines"] == [5, 6]
    assert files["app/db.py"]["hunks"][0]["added"][0]["line"] == 5


def test_bad_url_rejected(client):
    assert client.post("/reviews", json={"pr_url": "https://example.com"}).status_code == 422


def test_missing_pr_marks_failed():
    app.dependency_overrides[get_github_factory] = lambda: fake_github(missing_pr=True)
    try:
        with TestClient(app) as c:
            rid = c.post("/reviews", json={"pr_url": f"https://github.com/{REPO}/pull/7"}).json()["id"]
            d = c.get(f"/reviews/{rid}").json()
            assert d["status"] == "failed" and "not found" in d["error"]
    finally:
        app.dependency_overrides.clear()
        asyncio.run(_cleanup_repo())


async def _cleanup_repo():
    conn = await asyncpg.connect(settings.database_url)
    await conn.execute("DELETE FROM reviews WHERE repo=$1", REPO)
    await conn.close()


def _hook(client, body: dict, event="pull_request", delivery=None, secret="s3cret", sig=None):
    raw = json.dumps(body).encode()
    sig = sig or "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/webhook/github", content=raw, headers={
        "X-GitHub-Event": event, "X-GitHub-Delivery": delivery or f"test-{uuid.uuid4()}",
        "X-Hub-Signature-256": sig, "Content-Type": "application/json"})


PR_PAYLOAD = {"action": "opened", "pull_request": {"number": 7, "html_url": f"https://github.com/{REPO}/pull/7"},
              "repository": {"full_name": REPO}}


def test_webhook_flow(client, monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "s3cret")

    assert _hook(client, {}, sig="sha256=bad").status_code == 401           # forged signature
    assert _hook(client, {}, event="ping").json() == {"status": "pong"}

    r = _hook(client, PR_PAYLOAD, delivery="test-dup-1")
    assert r.json()["status"] == "queued"
    assert client.get(f"/reviews/{r.json()['review_id']}").json()["status"] == "ingested"

    assert _hook(client, PR_PAYLOAD, delivery="test-dup-1").json() == {"status": "duplicate"}   # retry dropped
    closed = {**PR_PAYLOAD, "action": "closed"}
    assert _hook(client, closed).json()["status"] == "ignored"


def test_webhook_requires_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "")
    assert _hook(client, PR_PAYLOAD).status_code == 503
