"""Check your Gemini setup end to end.   Run:  python -m scripts.smoke_llm

1. confirms GEMINI_API_KEY is set,
2. lists the Flash-class models your key can call (model ids change; pick one for GEMINI_MODEL),
3. makes one tiny real call with your configured model and prints tokens, latency and est. cost.
"""
import asyncio
import json
import sys

import httpx

from app.config import settings
from app.llm.base import LLMError
from app.llm.gemini import GeminiClient


async def list_models() -> None:
    async with httpx.AsyncClient(base_url=settings.gemini_api_url, timeout=30,
                                 headers={"x-goog-api-key": settings.gemini_api_key}) as http:
        r = await http.get("/models", params={"pageSize": 200})
    if r.status_code != 200:
        print(f"  could not list models (HTTP {r.status_code}): {r.text[:200]}")
        return
    names = sorted(m["name"].removeprefix("models/") for m in r.json().get("models", [])
                   if "generateContent" in m.get("supportedGenerationMethods", [])
                   and "flash" in m["name"] and not any(x in m["name"] for x in
                                                        ("image", "tts", "live", "audio", "robotics")))
    print("  Flash models your key can use (set one as GEMINI_MODEL):")
    for n in names:
        print(f"    - {n}")


async def main() -> int:
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is empty. Put it in .env (GEMINI_API_KEY=...) and restart the backend.")
        return 1
    print(f"Provider: {settings.llm_provider} | configured model: {settings.gemini_model}")
    print("Step 1/2: listing models")
    await list_models()
    print("Step 2/2: test call")
    try:
        async with GeminiClient() as llm:
            r = await llm.generate(
                system="You are a connectivity test. Reply with JSON only.",
                user='Return exactly {"ok": true, "word": "<any single word>"}')
    except LLMError as e:
        print(f"  FAILED: {e}")
        return 1
    print(f"  reply     : {json.dumps(r.text.strip())[:200]}")
    print(f"  model     : {r.model}")
    print(f"  tokens    : {r.tokens_in} in / {r.tokens_out} out | {r.latency_ms} ms | "
          f"attempts: {r.attempts} | est. cost ${r.cost_usd:.6f}")
    print("OK - the LLM layer works. You can run a review now.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
