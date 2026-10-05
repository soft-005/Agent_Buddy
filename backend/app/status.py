"""Readiness of the parts Buddy needs, for the desktop app's startup loader.

LLM states:
  "ready"       the model can answer now
  "not_loaded"  the server is up but the model isn't in memory yet (it loads on
                the first request, or the launcher is already loading it)
  "missing"     the server doesn't have this model
  "offline"     the server isn't reachable
"""

import json
import time
import urllib.request

from . import agent, stt

_CACHE_SECONDS = 1.5
_cache: dict = {"at": 0.0, "llm": None}


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def _check_llm() -> str:
    MODEL = agent.MODEL  # read now: the model can be switched while Buddy runs
    if MODEL.api != "openai-completions":
        return "ready"  # cloud API: nothing to load locally
    base = (MODEL.base_url or "").rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    try:
        # LM Studio reports each model's load state here.
        models = _get_json(f"{root}/api/v0/models").get("data", [])
    except Exception:
        models = None
    if models is not None:
        if not models:
            return "offline"  # LM Studio just started and hasn't listed its models yet
        entry = next((m for m in models if m.get("id") == MODEL.id), None)
        if entry is None:
            return "missing"
        return "ready" if entry.get("state") == "loaded" else "not_loaded"
    try:
        _get_json(f"{base}/models")  # other OpenAI-compatible servers: reachable means ready
        return "ready"
    except Exception:
        return "offline"


def llm_status() -> str:
    """Blocking (local HTTP, 2 s timeout); call through asyncio.to_thread. Cached briefly."""
    now = time.monotonic()
    if _cache["llm"] is None or now - _cache["at"] > _CACHE_SECONDS:
        _cache["llm"] = _check_llm()
        _cache["at"] = now
    return _cache["llm"]


def snapshot(llm: str) -> dict:
    return {"type": "status", "stt": stt.status(), "llm": llm, "model_name": agent.MODEL.name, "provider": agent.MODEL.provider}
