"""OpenAI-compatible chat backend: LM Studio, Ollama, llama.cpp, vLLM, Colibri."""

import json
import re
from typing import AsyncIterator

import openai

from ..providers import ModelConfig
from ..tools import ToolCall
from .base import LLMError, StreamEvent

DEFAULT_TIMEOUT = 180.0  # seconds, when providers.json sets no "timeout"

OPEN_TAG, CLOSE_TAG = "<think>", "</think>"

# Reasoning models may put their thinking inline. The "$" branch also removes
# a block left unclosed when max_tokens cut the reply off mid-thought.
_THINK_BLOCK = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL)

# Sampling keys the OpenAI SDK accepts as named arguments. Anything else from
# the model's samplingParams (top_k, min_p, reasoning_effort, ...) is sent
# through extra_body unchanged.
_NAMED_PARAMS = {"temperature", "top_p", "presence_penalty", "frequency_penalty", "seed"}


def strip_reasoning(text: str | None) -> str:
    """Remove reasoning from a complete reply."""
    text = text or ""
    # Some chat templates open <think> in the prompt, so only the close tag appears.
    if CLOSE_TAG in text and OPEN_TAG not in text:
        text = text.rsplit(CLOSE_TAG, 1)[1]
    return _THINK_BLOCK.sub("", text).strip()


def _partial_tag(text: str, tag: str) -> int:
    """Length of the longest end of text that could be the start of tag."""
    for n in range(min(len(text), len(tag) - 1), 0, -1):
        if text.endswith(tag[:n]):
            return n
    return 0


class ThinkFilter:
    """Streaming counterpart of strip_reasoning.

    Drops <think>...</think> spans even when a tag is split across chunks, and
    releases other text as soon as it cannot be the start of a tag. A stray
    closing tag is removed, but reasoning before it may already have been
    released, so for models whose template opens the think block in the prompt,
    turn on LM Studio's "separate reasoning_content" setting instead.
    """

    def __init__(self) -> None:
        self.buf = ""
        self.in_think = False

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        return self._drain(final=False)

    def flush(self) -> str:
        return self._drain(final=True)

    def _drain(self, final: bool) -> str:
        out = []
        while True:
            if self.in_think:
                i = self.buf.find(CLOSE_TAG)
                if i < 0:
                    keep = 0 if final else _partial_tag(self.buf, CLOSE_TAG)
                    self.buf = self.buf[len(self.buf) - keep:]
                    break
                self.buf = self.buf[i + len(CLOSE_TAG):]
                self.in_think = False
                continue

            hits = [(i, tag) for tag in (OPEN_TAG, CLOSE_TAG) if (i := self.buf.find(tag)) >= 0]
            if hits:
                i, tag = min(hits)
                out.append(self.buf[:i])
                self.buf = self.buf[i + len(tag):]
                self.in_think = tag == OPEN_TAG  # a stray close tag is just dropped
                continue

            keep = 0 if final else max(_partial_tag(self.buf, OPEN_TAG), _partial_tag(self.buf, CLOSE_TAG))
            out.append(self.buf[: len(self.buf) - keep])
            self.buf = self.buf[len(self.buf) - keep:]
            break
        return "".join(out)


class OpenAICompatLLM:
    def __init__(self, cfg: ModelConfig) -> None:
        self.cfg = cfg
        self.timeout = cfg.timeout or DEFAULT_TIMEOUT
        self.client = openai.AsyncOpenAI(
            base_url=cfg.base_url,
            api_key=cfg.api_key or "local",  # local servers ignore it, the SDK requires one
            timeout=self.timeout,
            max_retries=0,  # retrying a slow local generation only doubles the wait
        )
        self.named = {k: v for k, v in cfg.sampling.items() if k in _NAMED_PARAMS}
        self.extra = {k: v for k, v in cfg.sampling.items() if k not in _NAMED_PARAMS}

    def _request(self, system: str, messages: list, max_tokens: int | None) -> dict:
        return dict(
            model=self.cfg.id,
            messages=[{"role": "system", "content": system}, *messages],
            max_tokens=max_tokens or self.cfg.max_tokens,
            extra_body=self.extra or None,
            **self.named,
        )

    def _error(self, e: openai.APIError) -> LLMError:
        if isinstance(e, openai.APITimeoutError):
            return LLMError(
                f"{self.cfg.provider} took longer than {self.timeout:g} s; "
                "raise \"timeout\" in providers.json or use a faster model"
            )
        if isinstance(e, openai.APIConnectionError):
            return LLMError(f"cannot reach {self.cfg.provider} at {self.cfg.base_url}; is the server running?")
        if isinstance(e, openai.APIStatusError):
            return LLMError(f"{self.cfg.provider} error {e.status_code}: {e.message}")
        return LLMError(f"{self.cfg.provider} error: {e}")

    async def complete(
        self, system: str, messages: list, max_tokens: int | None = None
    ) -> tuple[str, dict]:
        try:
            response = await self.client.chat.completions.create(
                **self._request(system, messages, max_tokens)
            )
        except openai.APIError as e:
            raise self._error(e) from e

        content = response.choices[0].message.content if response.choices else ""
        text = strip_reasoning(content)
        # History keeps only the visible reply. Old reasoning wastes a small context window.
        return text, {"role": "assistant", "content": text}

    async def stream(
        self, system: str, messages: list, max_tokens: int | None = None, tools: list[dict] | None = None
    ) -> AsyncIterator[StreamEvent]:
        think = ThinkFilter()
        parts: list[str] = []
        calls: dict[int, dict] = {}  # tool calls arrive in pieces, keyed by index
        request = self._request(system, messages, max_tokens)
        if tools:
            request["tools"] = [
                {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
                for t in tools
            ]
        try:
            response = await self.client.chat.completions.create(**request, stream=True)
            # "async with" closes the HTTP stream if the reply is cancelled,
            # which tells the local server to stop generating.
            async with response:
                async for chunk in response:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    for piece in delta.tool_calls or []:
                        call = calls.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
                        call["id"] = piece.id or call["id"]
                        if piece.function is not None:
                            call["name"] += piece.function.name or ""
                            call["arguments"] += piece.function.arguments or ""
                    # Separated reasoning (delta.reasoning_content) is ignored; only content is spoken.
                    visible = think.feed(delta.content or "")
                    if visible:
                        parts.append(visible)
                        yield ("text", visible)
        except openai.APIError as e:
            raise self._error(e) from e

        tail = think.flush()
        if tail:
            parts.append(tail)
            yield ("text", tail)

        text = "".join(parts).strip()
        tool_calls = []
        for index, call in sorted(calls.items()):
            call_id = call["id"] or f"call_{index}"
            try:
                arguments = json.loads(call["arguments"]) if call["arguments"].strip() else {}
            except json.JSONDecodeError:
                arguments = {}  # the toolbox reports the missing arguments to the model
            tool_calls.append({"id": call_id, "type": "function", "function": {"name": call["name"], "arguments": call["arguments"] or "{}"}})
            yield ("tool_call", ToolCall(call_id, call["name"], arguments if isinstance(arguments, dict) else {}))
        message = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = tool_calls
        yield ("message", message)

    def tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> list[dict]:
        return [{"role": "tool", "tool_call_id": call.id, "content": text} for call, text, _error in results]
