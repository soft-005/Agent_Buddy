"""The conversational agent: one Conversation, shared by every connected app.

The conversation is saved to backend/data/conversation.json after every turn
and loaded at startup, so Buddy picks up where you left off. "New conversation"
in the apps moves the file to data/conversations/ and starts empty.

Local models have small context windows, so history is budgeted the way the pi
agent does it: when the prompt would not leave room for a full reply, older
turns are summarized by the same model and replaced by that summary, and the
most recent turns are kept verbatim.

When tools are on (backend/tools.json), the model can ask to use them. Each
round runs the requested tools (asking the user first where the tool's
approval is "ask") and gives the results back, up to MAX_TOOL_ROUNDS times.

Replies start with a mood tag (app/moods.py). Each sentence goes out with the
mood in effect for it, so the avatar's face can follow the reply.
"""

import asyncio
import datetime
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from .config import (
    CONVERSATION_FILE, LLM_MODEL, LLM_PROVIDER, MOOD_TAGS, SAVE_CONVERSATION, SYSTEM_PROMPT, update_env_file,
)
from .llm import LLMError, create_llm
from .memory import BuddyMemory
from .model_switch import unusable_reason
from .moods import MOOD_PROMPT, MoodFilter, strip_moods
from .providers import ModelConfig, load_model
from .sentences import SentenceSplitter
from .tools import Approver, ToolCall, Toolbox, deny_all

log = logging.getLogger("buddy.agent")

# The model in use. switch_model() replaces both, so other modules read agent.MODEL
# at the time they need it rather than importing the name.
MODEL = load_model(LLM_PROVIDER, LLM_MODEL)
_llm = create_llm(MODEL)
MEMORY = BuddyMemory()  # one user, so one memory shared by all connections
TOOLS = Toolbox()
MAX_TOOL_ROUNDS = 4

# Small local models sometimes announce an action ("I'll open Notepad!") without
# calling the tool. When a reply says that but no tool was called, Buddy asks the
# model once to either call the tool or say it can't.
_PROMISE = re.compile(
    r"\b(?:(?:i'?ll|i will|let me|i'?m going to|i am going to)\b[^.!?]*"
    r"\b(?:open|launch|start|create|write|save|make|search|run|list|check|read|look)"
    r"|opening|launching|creating|saving|searching)\b",
    re.IGNORECASE,
)
_NUDGE = (
    "(Note from the app: you said you would do that, but no tool was called, so nothing "
    "happened. Call the right tool now, or tell the user you can't do it.)"
)

SUMMARY_PROMPT = (
    "You compress conversation history for a voice assistant called Buddy. "
    "Summarize the transcript in a few short sentences of plain notes. Keep the "
    "user's name, facts about the user, their preferences, and anything left "
    "unresolved. Drop small talk. Output only the notes."
)
SUMMARY_MAX_TOKENS = 512


@dataclass
class Sentence:
    """One sentence of a reply, ready to speak, and the mood to show while it's spoken."""

    text: str
    mood: str


def estimate_tokens(text: str) -> int:
    """Rough count (about 4 characters per token), as pi uses for budgeting."""
    return (len(text) + 3) // 4


def estimate_message(message: dict) -> int:
    content = message.get("content") or ""
    size = estimate_tokens(content if isinstance(content, str) else json.dumps(content))
    if message.get("tool_calls"):
        size += estimate_tokens(json.dumps(message["tool_calls"]))
    return size + 4


def _text_of(message: dict) -> str:
    """A message's plain text, whether its content is a string or Claude's content blocks."""
    content = message.get("content") or ""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _starts_turn(message: dict) -> bool:
    """Something the user said (not a tool result): history may start here."""
    return message.get("role") == "user" and isinstance(message.get("content"), str)


def plain_turns(messages: list) -> list[dict]:
    """History as alternating user and assistant text, for a model with a different
    API than the one that wrote it. Tool exchanges and thinking are dropped."""
    turns: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant" or (_starts_turn(message) and message["content"] != _NUDGE):
            text = _text_of(message).strip()
            if not text:
                continue
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"] += "\n" + text
            elif turns or role == "user":
                turns.append({"role": role, "content": text})
    if turns and turns[-1]["role"] == "user":
        turns.pop()  # never answered
    return turns


async def warm_up() -> None:
    """Load the local model now with a one-token request, so the first reply doesn't
    wait for it. Sending the real system prompt and tool list also makes the server
    cache that prefix: on this laptop the first request otherwise spends about a
    minute reading it, and later ones take seconds. The saved conversation goes
    along too (when it fits), so the first reply after a restart is quick as well."""
    fits = SESSION.used_tokens() <= MODEL.context_window - MODEL.max_tokens
    messages = [*(SESSION.messages if fits else []), {"role": "user", "content": "Hi"}]
    async for _ in _llm.stream(SESSION.system_prompt(), messages, max_tokens=1, tools=SESSION.tools or None):
        pass


class Conversation:
    """Buddy's conversation history, shared by every connected app.

    Turns run one at a time: a message from a second device waits until the
    current turn ends. With a `path`, the history is loaded from it at startup
    and saved to it after every turn. Long-term facts live in the memory file.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.messages: list = []
        self.summary = ""  # compacted older turns
        self.tools = TOOLS.schemas()
        self.tool_tokens = estimate_tokens(json.dumps(self.tools)) if self.tools else 0
        self._lock = asyncio.Lock()
        if path:
            self.load()

    def system_prompt(self) -> str:
        # Stable parts first: the local server reuses its cache for an unchanged prefix.
        parts = [SYSTEM_PROMPT]
        if MOOD_TAGS:
            parts.append(MOOD_PROMPT)
        tools_prompt = TOOLS.prompt()
        if tools_prompt:
            parts.append(tools_prompt)
        memory = MEMORY.prompt_block()
        if memory:
            parts.append(memory)
        if self.summary:
            parts.append(f"Summary of the earlier conversation:\n{self.summary}")
        return "\n\n".join(parts)

    def used_tokens(self) -> int:
        return estimate_tokens(self.system_prompt()) + self.tool_tokens + sum(estimate_message(m) for m in self.messages)

    async def reply_stream(self, user_text: str, approver: Approver = deny_all) -> AsyncIterator[Sentence | dict]:
        """Yield the reply one Sentence at a time, as soon as each is complete;
        {"type": "tool", "title": ..., "ok": ...} after each tool runs; and last
        {"type": "reply", "text": ..., "mood": ...} with the whole reply.

        `approver` asks the user who sent this message to allow a tool.
        Raises LLMError if the model call fails; sentences already yielded stand.
        """
        async with self._lock:
            MEMORY.observe(user_text)  # "remember ...", "forget ...", personal facts
            self.messages.append({"role": "user", "content": user_text})
            await self.fit_context()
            turn_start = len(self.messages) - 1

            moods = MoodFilter()
            parts: list[str] = []
            nudged = False
            used_tools = False  # after a tool ran (or was denied), the reply talks about it: no nudge
            try:
                for round_number in range(MAX_TOOL_ROUNDS + 1):
                    # Last round: no tools, so the model has to answer in words.
                    tools = self.tools if round_number < MAX_TOOL_ROUNDS else None
                    splitter = SentenceSplitter()
                    calls: list[ToolCall] = []
                    assistant_message = None
                    round_text = []
                    async for kind, value in _llm.stream(self.system_prompt(), self.messages, tools=tools):
                        if kind == "text":
                            text = moods.feed(value)
                            parts.append(text)
                            round_text.append(text)
                            for sentence in splitter.feed(text):
                                yield Sentence(sentence, moods.mood)
                        elif kind == "tool_call":
                            calls.append(value)
                        elif kind == "message":
                            assistant_message = value
                    self.messages.append(assistant_message)
                    tail = moods.flush()
                    parts.append(tail)
                    round_text.append(tail)
                    for sentence in splitter.feed(tail) + splitter.flush():
                        yield Sentence(sentence, moods.mood)
                    if not calls:
                        if tools and not nudged and not used_tools and _PROMISE.search("".join(round_text)):
                            nudged = True
                            log.info("reply promised an action without a tool call; asking once more")
                            self.messages.append({"role": "user", "content": _NUDGE})
                            if parts and not parts[-1].endswith(" "):
                                parts.append(" ")
                            continue
                        break
                    if parts and not parts[-1].endswith(" "):
                        parts.append(" ")
                    used_tools = True
                    results = []
                    for call in calls:
                        text, error, title = await TOOLS.run(call, approver)
                        results.append((call, text, error))
                        yield {"type": "tool", "title": title, "ok": not error}
                    self.messages.extend(_llm.tool_results(results))
            except LLMError:
                self._end_turn(turn_start, "")
                raise
            except BaseException:
                # Interrupted (the user spoke again) or the client left mid-reply.
                self._end_turn(turn_start, strip_moods("".join(parts)))
                raise
            self.save()
        yield {"type": "reply", "text": strip_moods("".join(parts)), "mood": moods.first or "neutral"}

    def _end_turn(self, turn_start: int, partial: str) -> None:
        """Keep history valid after a turn that didn't finish: drop its tool exchanges,
        then keep what was said as the reply, or drop the user message if nothing was.
        This keeps user and assistant turns alternating, which some chat templates require."""
        del self.messages[turn_start + 1:]
        if partial:
            self.messages.append({"role": "assistant", "content": partial})
        elif self.messages and len(self.messages) == turn_start + 1:
            self.messages.pop()
        self.save()

    async def reply(self, user_text: str) -> str:
        """Whole reply at once (used by tests and scripts)."""
        reply = ""
        async for item in self.reply_stream(user_text):
            if isinstance(item, dict) and item["type"] == "reply":
                reply = item["text"]
        return reply

    def transcript(self, limit: int = 50) -> list[dict]:
        """The recent conversation as the apps show it: [{"role": "user"|"assistant", "text": ...}].
        Turns already folded into the summary aren't included."""
        lines = []
        for message in self.messages:
            if _starts_turn(message) and message["content"] != _NUDGE:
                lines.append({"role": "user", "text": message["content"]})
            elif message.get("role") == "assistant":
                text = strip_moods(_text_of(message))
                if text:
                    lines.append({"role": "assistant", "text": text})
        return lines[-limit:]

    # ---------- saving ----------

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.warning("Could not read the saved conversation %s (%s); starting a new one", self.path, e)
            return
        messages = data.get("messages") or []
        if data.get("api") != MODEL.api:
            # Saved with another kind of model (Claude vs. local): keep only the words.
            messages = plain_turns(messages)
        self.messages = messages
        self.summary = data.get("summary") or ""
        log.info("Picking up the saved conversation (%d messages) from %s", len(messages), self.path)

    def save(self) -> None:
        if not self.path:
            return
        data = {"api": MODEL.api, "model": MODEL.id, "summary": self.summary, "messages": self.messages}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temporary, self.path)  # never leaves a half-written file
        except (OSError, TypeError, ValueError) as e:
            log.warning("Could not save the conversation: %s", e)

    async def reset(self) -> None:
        """Start a new conversation. A saved one moves to data/conversations/."""
        async with self._lock:
            if self.path and self.path.exists() and (self.messages or self.summary):
                stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                archive = self.path.parent / "conversations" / f"{stamp}.json"
                try:
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(self.path, archive)
                    log.info("Started a new conversation; the old one is in %s", archive)
                except OSError as e:
                    log.warning("Could not archive the conversation: %s", e)
            self.messages = []
            self.summary = ""
            self.save()

    # ---------- context budget ----------

    async def fit_context(self) -> None:
        """Compact history when prompt + max reply would overflow the context window.

        Claude's window is large, so this rarely runs for it. Cutting only at
        something the user said keeps tool exchanges and thinking blocks whole.
        """
        budget = MODEL.context_window - MODEL.max_tokens
        if self.used_tokens() <= budget:
            return

        # Keep the newest messages within half the budget, starting on a user turn.
        keep_budget = budget // 2
        kept: list = []
        used = 0
        for message in reversed(self.messages):
            cost = estimate_message(message)
            if kept and used + cost > keep_budget:
                break
            kept.insert(0, message)
            used += cost
        while len(kept) > 1 and not _starts_turn(kept[0]):
            kept.pop(0)

        older = self.messages[: len(self.messages) - len(kept)]
        if not older:
            return  # one oversized message; nothing left to compact
        self.messages = kept
        self.summary = await self.summarize(older)
        log.info(
            "compacted %d messages; context now ~%d of %d tokens",
            len(older), self.used_tokens(), budget,
        )

    async def summarize(self, older: list) -> str:
        lines = [f"Earlier summary: {self.summary}"] if self.summary else []
        lines += [f"{m['role']}: {strip_moods(_text_of(m))}" for m in older if _text_of(m)]
        transcript = "\n".join(lines)
        # The summary request must fit the window too; keep the newest part.
        max_chars = (MODEL.context_window - SUMMARY_MAX_TOKENS - estimate_tokens(SUMMARY_PROMPT) - 64) * 4
        transcript = transcript[-max_chars:]

        max_summary_chars = (MODEL.context_window - MODEL.max_tokens) // 5 * 4
        try:
            summary, _ = await _llm.complete(
                SUMMARY_PROMPT,
                [{"role": "user", "content": transcript}],
                max_tokens=min(SUMMARY_MAX_TOKENS, MODEL.max_tokens),
            )
        except LLMError:
            log.warning("summary failed; dropping older turns without one")
            return self.summary[:max_summary_chars]
        return (summary or self.summary)[:max_summary_chars]


SESSION = Conversation(CONVERSATION_FILE if SAVE_CONVERSATION else None)


async def switch_model(model: ModelConfig) -> None:
    """Use another model from providers.json from the next turn on, and save the
    choice in backend/.env. Waits for a running turn to finish first.
    Raises ValueError if the model can't be used (Claude without an API key)."""
    global MODEL, _llm
    reason = unusable_reason(model)
    if reason:
        raise ValueError(reason)
    llm = create_llm(model)
    async with SESSION._lock:
        if model.api != MODEL.api:
            # Claude's content blocks and a local model's tool messages don't mix: keep the words.
            SESSION.messages = plain_turns(SESSION.messages)
        MODEL, _llm = model, llm
        SESSION.save()
    update_env_file({"LLM_PROVIDER": model.provider, "LLM_MODEL": model.id})
    log.info("Now using %s (%s/%s)", model.name, model.provider, model.id)
