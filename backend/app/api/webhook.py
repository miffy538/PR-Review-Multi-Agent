import hashlib
import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request

from app.config import settings
from app.db import get_pool
from app.deps import GitHubFactory, get_github_factory
from app.integrations.github_client import PRRef
from app.services.ingest import create_review, run_ingestion

router = APIRouter(prefix="/webhook", tags=["webhook"])
HANDLED_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


@router.post("/github")
async def github_webhook(
    request: Request, bg: BackgroundTasks,
    x_github_event: str | None = Header(None),
    x_github_delivery: str | None = Header(None),
    x_hub_signature_256: str | None = Header(None),
    factory: GitHubFactory = Depends(get_github_factory),
):
    if not settings.github_webhook_secret:
        raise HTTPException(503, "GITHUB_WEBHOOK_SECRET is not configured")
    body = await request.body()
    if not verify_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(401, "Invalid signature")
    if not x_github_delivery or not x_github_event:
        raise HTTPException(400, "Missing GitHub headers")

    # Idempotency: GitHub retries deliveries; the same delivery id must never run twice.
    inserted = await get_pool().fetchval(
        "INSERT INTO webhook_deliveries (delivery_id) VALUES ($1) "
        "ON CONFLICT DO NOTHING RETURNING delivery_id", x_github_delivery)
    if inserted is None:
        return {"status": "duplicate"}

    if x_github_event == "ping":
        return {"status": "pong"}
    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event {x_github_event}"}

    try:
        payload = json.loads(body)
        action = payload["action"]
        number = int(payload["pull_request"]["number"])
        full_name = payload["repository"]["full_name"]
        owner, repo = full_name.split("/", 1)
        pr_url = payload["pull_request"].get("html_url")
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(400, "Malformed pull_request payload") from e
    if action not in HANDLED_ACTIONS:
        return {"status": "ignored", "reason": f"action {action}"}

    ref = PRRef(owner, repo, number)
    review_id = await create_review(ref, pr_url or f"https://github.com/{full_name}/pull/{number}")
    bg.add_task(run_ingestion, review_id, ref, factory)
    return {"status": "queued", "review_id": review_id}
