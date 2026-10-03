import asyncio
import logging
import os
import time

import httpx

BASE = os.environ.get("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "gemini-3.5-flash")
FALLBACKS = [m for m in os.environ.get("LLM_FALLBACK_MODELS", "gemini-3.8-flash,gemini-flash-lite-latest").split(",") if m]
REASONING = os.environ.get("LLM_REASONING_EFFORT", "low")
KEY = os.environ.get("LLM_API_KEY", "")

_http = httpx.AsyncClient(timeout=60)
log = logging.getLogger("uvicorn.error")


async def chat(messages, tools=None, temperature=0.3):
    body = {"messages": messages, "temperature": temperature}
    if REASONING:
        body["reasoning_effort"] = REASONING
    if tools:
        body["tools"] = tools
    last = None
    for model in [MODEL, *FALLBACKS]:
        for attempt in range(2):
            t0 = time.time()
            try:
                r = await _http.post(f"{BASE}/chat/completions", json={**body, "model": model},
                                     headers={"Authorization": f"Bearer {KEY}"})
            except httpx.TimeoutException as e:
                last = e
                break
            log.info("llm %s -> %s in %.1fs", model, r.status_code, time.time() - t0)
            if r.status_code in (429, 500, 502, 503):
                last = RuntimeError(f"{model}: {r.status_code}")
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]
    raise RuntimeError(f"The AI model is busy right now, please retry in a minute ({last}).")
