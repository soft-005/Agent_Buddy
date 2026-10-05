"""List Buddy's language models, or switch to another one.

Run from the backend/ folder (or use: buddy.ps1 model [name]):
    python scripts/model.py              # list the models; * marks the one in use
    python scripts/model.py gemma        # switch to the model whose name or id contains "gemma"
    python scripts/model.py anthropic    # Claude (needs ANTHROPIC_API_KEY in backend/.env)

If Buddy is running, it switches right away (no restart). Either way the choice
is saved in backend/.env (LLM_PROVIDER and LLM_MODEL). The models come from
backend/providers.json. You can also just tell Buddy: "switch to Gemma".
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the app package

from app import config, model_switch  # noqa: E402
from app.providers import list_models  # noqa: E402

BASE = f"http://127.0.0.1:{config.PORT}"


def running_model() -> tuple[str, str] | None:
    """(provider, id) of the running Buddy's model, or None if Buddy isn't running."""
    try:
        with urllib.request.urlopen(f"{BASE}/models", timeout=3) as response:
            current = json.loads(response.read())["current"]
            return current["provider"], current["model"]
    except (OSError, ValueError, KeyError):
        return None


def show(current: tuple[str, str]) -> None:
    print("Models in providers.json (* = in use):")
    for m in list_models():
        mark = "*" if (m.provider, m.id) == current else " "
        problem = model_switch.unusable_reason(m)
        print(f" {mark} {m.name:<22} {m.provider}/{m.id}" + (f"   ({problem})" if problem else ""))
    print('\nSwitch with: buddy.ps1 model <name>, or tell Buddy "switch to <name>".')


def main() -> int:
    live = running_model()
    current = live or (config.LLM_PROVIDER, config.LLM_MODEL)
    name = " ".join(sys.argv[1:]).strip()
    if not name:
        show(current)
        return 0

    model, candidates = model_switch.find(name)
    if model is None:
        print(model_switch.not_found(name, candidates))
        return 1
    problem = model_switch.unusable_reason(model)
    if problem:
        print(f"Can't switch: {problem}.")
        return 1

    if live:
        request = urllib.request.Request(
            f"{BASE}/model",
            data=json.dumps({"provider": model.provider, "model": model.id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                json.loads(response.read())
        except urllib.error.HTTPError as e:
            print(f"Buddy couldn't switch: {json.loads(e.read()).get('error', e)}")
            return 1
        print(f"Buddy now uses {model.name} ({model.provider}/{model.id}). Saved in backend/.env.")
    else:
        config.update_env_file({"LLM_PROVIDER": model.provider, "LLM_MODEL": model.id})
        print(f"Saved {model.name} ({model.provider}/{model.id}) in backend/.env. Buddy uses it next time it starts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
