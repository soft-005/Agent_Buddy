"""Switching the language model while Buddy runs.

Three ways in, all ending in agent.switch_model() (which also saves the choice
in backend/.env, so it survives a restart):
  - chat, typed or spoken: "switch to Gemma", "change the model to Qwen",
    "use the Claude model", "/model gemma", and "/model" or "which model are
    you?" to hear the current one and the choices. Handled before the model is
    called, with no model call.
  - the command line: python scripts/model.py gemma (or buddy.ps1 model gemma),
    which asks the running backend over POST /model.
  - the desktop tray's Model menu, over the WebSocket.

A name matches a model when every word of it appears in the model's provider,
id or name, so "gemma", "claude", "qwen 3.5" and "anthropic" all work.
A word must be at least two characters, so "a" or a stray letter matches nothing.
This module doesn't import the agent, so the command-line script can use it.
"""

import os
import re

from .providers import ModelConfig, list_models

_LEAD = r"^\s*(?:(?:hey|hi|ok|okay)\s+buddy[,!.]?\s*)?(?:please\s+)?(?:can you\s+|could you\s+)?"
_SLASH = re.compile(r"^\s*/models?(?:\s+(?P<name>.+?))?\s*$", re.I)
# "switch to gemma", "change your model to qwen", "swap the model to claude"
_SWITCH = re.compile(
    _LEAD + r"(?:switch|change|swap|set)\s+(?:(?:the|your)\s+)?(?P<keyword>(?:language\s+)?model\s+|brain\s+|llm\s+)?"
    r"to\s+(?:the\s+)?(?P<name>.+?)[.!?]*\s*$",
    re.I,
)
# "use the gemma model", "use claude model"
_USE = re.compile(_LEAD + r"use\s+(?:the\s+)?(?P<name>.+?)\s+(?:language\s+)?model[.!?]*\s*$", re.I)
_WHICH = re.compile(r"\b(?:which|what)\s+(?:language\s+)?model\s+(?:are\s+you|is\s+this|do\s+you)\b", re.I)
_WORD = re.compile(r"[a-z0-9]+")


def parse(text: str) -> tuple[str, str] | None:
    """("switch", name) or ("show", "") if text is a model command, else None.

    A natural sentence ("switch to dark mode") only counts when the name is a
    known model or the sentence says "model"; otherwise it goes to the model as usual.
    """
    if match := _SLASH.match(text):
        name = (match.group("name") or "").strip()
        return ("switch", name) if name else ("show", "")
    if _WHICH.search(text):
        return ("show", "")
    for pattern in (_SWITCH, _USE):
        if match := pattern.match(text):
            name = match.group("name").strip()
            said_model = pattern is _USE or bool(match.groupdict().get("keyword"))
            if said_model or find(name)[0] is not None:
                return ("switch", name)
    return None


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def find(name: str) -> tuple[ModelConfig | None, list[ModelConfig]]:
    """(the model called name, []) or (None, the candidates if it was ambiguous)."""
    models = list_models()
    wanted = {w for w in _words(name) - {"the", "model", "please", "now"} if len(w) >= 2 or w.isdigit()}
    if not wanted:
        return None, []
    for model in models:  # an exact id or name wins
        if name.strip().lower() in (model.id.lower(), model.name.lower(), f"{model.provider}/{model.id}".lower()):
            return model, []
    # Each word is looked for inside the run-together provider, id and name, so
    # "qwen 3.5" finds "qwen/qwen3.5-9b" and "e4b" finds Gemma.
    matches = [m for m in models if all(w in "".join(_WORD.findall(f"{m.provider} {m.id} {m.name}".lower())) for w in wanted)]
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def unusable_reason(model: ModelConfig) -> str | None:
    """Why this model can't be used right now, or None if it can."""
    if model.api == "anthropic-messages" and not (model.api_key or os.getenv("ANTHROPIC_API_KEY")):
        return f"{model.name} needs ANTHROPIC_API_KEY in backend/.env"
    return None


def describe_choices(current: ModelConfig) -> str:
    """A spoken answer to "which model are you?"."""
    others = [m.name for m in list_models() if (m.provider, m.id) != (current.provider, current.id)]
    where = {"lmstudio": "LM Studio", "ollama": "Ollama"}.get(current.provider, current.provider)
    if current.api == "anthropic-messages":
        where = "the Claude API"
    text = f"I'm using {current.name}, through {where}."
    if others:
        text += f" I can switch to {_join(others)}. Just say, for example, switch to {others[0]}."
    return text


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def not_found(name: str, candidates: list[ModelConfig]) -> str:
    if candidates:
        return f"Which one do you mean: {_join([m.name for m in candidates])}?"
    return f"I don't have a model called {name}. I can use {_join([m.name for m in list_models()])}."
