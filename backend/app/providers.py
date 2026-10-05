"""Model registry read from backend/providers.json.

The file uses the same shape as the pi agent's models.json, so a provider
block can be copied between the two. Supported "api" values:
  - "openai-completions": any OpenAI-compatible server (LM Studio, Ollama,
    llama.cpp, vLLM, Colibri).
  - "anthropic-messages": the Claude API.

Fields Buddy reads per model: id, name, contextWindow, maxTokens, reasoning,
temperature, topP, and pi's samplingParams. Other fields are ignored.
"apiKey" may be a literal or an environment reference like "$NAME".

Buddy-only provider field: "timeout", in seconds. It is how long to wait for
the model to start replying, or between two parts of a streamed reply. Slow
local models need a large value. Unset means 180 s for OpenAI-compatible
servers and the SDK default (10 minutes) for Claude.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PROVIDERS_FILE = Path(__file__).resolve().parent.parent / "providers.json"
_ENV_REF = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")


@dataclass
class ModelConfig:
    provider: str
    api: str
    id: str
    name: str
    base_url: str | None
    api_key: str | None
    context_window: int
    max_tokens: int
    reasoning: bool
    sampling: dict = field(default_factory=dict)
    timeout: float | None = None  # seconds; None = backend default


def _resolve_key(value: str | None) -> str | None:
    if not value:
        return None
    match = _ENV_REF.match(value)
    return os.getenv(match.group(1)) if match else value


def load_model(provider_name: str, model_id: str = "", path: Path = PROVIDERS_FILE) -> ModelConfig:
    """Return the config for one model. An empty model_id picks the provider's first model."""
    providers = json.loads(path.read_text(encoding="utf-8")).get("providers", {})
    if provider_name not in providers:
        raise ValueError(
            f"Provider '{provider_name}' is not in {path.name}. "
            f"Available: {', '.join(providers) or 'none'}"
        )
    provider = providers[provider_name]
    models = provider.get("models", [])
    if not models:
        raise ValueError(f"Provider '{provider_name}' has no models in {path.name}")

    model = models[0] if not model_id else next((m for m in models if m["id"] == model_id), None)
    if model is None:
        raise ValueError(
            f"Model '{model_id}' is not listed under '{provider_name}'. "
            f"Available: {', '.join(m['id'] for m in models)}"
        )
    return _config(provider_name, provider, model)


def list_models(path: Path = PROVIDERS_FILE) -> list[ModelConfig]:
    """Every model in providers.json, in file order."""
    providers = json.loads(path.read_text(encoding="utf-8")).get("providers", {})
    return [_config(name, provider, model) for name, provider in providers.items() for model in provider.get("models", [])]


def _config(provider_name: str, provider: dict, model: dict) -> ModelConfig:
    sampling = {}
    if "temperature" in model:
        sampling["temperature"] = model["temperature"]
    if "topP" in model:
        sampling["top_p"] = model["topP"]
    sampling.update(model.get("samplingParams", {}))

    return ModelConfig(
        provider=provider_name,
        api=provider.get("api", "openai-completions"),
        id=model["id"],
        name=model.get("name", model["id"]),
        base_url=provider.get("baseUrl"),
        api_key=_resolve_key(provider.get("apiKey")),
        context_window=int(model.get("contextWindow", 8192)),
        max_tokens=int(model.get("maxTokens", 1024)),
        reasoning=bool(model.get("reasoning", False)),
        sampling=sampling,
        timeout=float(provider["timeout"]) if provider.get("timeout") else None,
    )
