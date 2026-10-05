"""Claude API backend."""

from typing import AsyncIterator

import anthropic

from ..config import CLAUDE_EFFORT
from ..providers import ModelConfig
from ..tools import ToolCall
from .base import LLMError, StreamEvent

REFUSAL_TEXT = "Sorry, I can't help with that one."


def _content(blocks) -> list[dict]:
    """Response content as plain dicts, exactly as the SDK would send it back, so
    history can be saved as JSON (thinking blocks keep their signatures)."""
    return [
        b.model_dump(exclude_unset=True, mode="json", by_alias=True, exclude=getattr(b, "__api_exclude__", None))
        for b in blocks
    ]


class AnthropicLLM:
    def __init__(self, cfg: ModelConfig) -> None:
        self.cfg = cfg
        # With no key in providers.json or the environment, the SDK falls back
        # to its own credential lookup (for example an `ant auth login` profile).
        options = {}
        if cfg.api_key:
            options["api_key"] = cfg.api_key
        if cfg.timeout:
            options["timeout"] = cfg.timeout
        self.client = anthropic.AsyncAnthropic(**options)

    def _request(self, system: str, messages: list, max_tokens: int | None) -> dict:
        return dict(
            model=self.cfg.id,
            max_tokens=max_tokens or self.cfg.max_tokens,
            system=system,
            messages=messages,
            output_config={"effort": CLAUDE_EFFORT},
            # Server-side refusal fallback: re-runs a declined request on a
            # fallback model chosen by refusal category. When streaming, the
            # fallback continues on the same stream.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

    async def complete(
        self, system: str, messages: list, max_tokens: int | None = None
    ) -> tuple[str, dict]:
        try:
            response = await self.client.beta.messages.create(**self._request(system, messages, max_tokens))
        except anthropic.APIError as e:
            raise LLMError(f"Claude API error: {e.__class__.__name__}") from e

        if response.stop_reason == "refusal":
            text = REFUSAL_TEXT
        else:
            text = "".join(b.text for b in response.content if b.type == "text").strip()
        # Keep the full content so thinking blocks carry over to the next turn.
        return text, {"role": "assistant", "content": _content(response.content)}

    async def stream(
        self, system: str, messages: list, max_tokens: int | None = None, tools: list[dict] | None = None
    ) -> AsyncIterator[StreamEvent]:
        request = self._request(system, messages, max_tokens)
        if tools:
            # eager_input_streaming streams tool inputs as they're generated; the
            # toolbox validates the parsed input before anything runs.
            request["tools"] = [
                {"name": t["name"], "description": t["description"], "input_schema": t["parameters"], "eager_input_streaming": True}
                for t in tools
            ]
        emitted = False
        try:
            async with self.client.beta.messages.stream(**request) as stream:
                async for text in stream.text_stream:
                    emitted = True
                    yield ("text", text)
                response = await stream.get_final_message()
        except ValueError as e:  # a tool input the SDK couldn't parse at all
            raise LLMError("Claude sent a tool request that couldn't be read; please try again") from e
        except anthropic.APIError as e:
            raise LLMError(f"Claude API error: {e.__class__.__name__}") from e

        # A refusal here means the fallback model declined too.
        if response.stop_reason == "refusal":
            yield ("text", (" " if emitted else "") + REFUSAL_TEXT)
        elif response.stop_reason != "max_tokens":  # a cut-off tool input must not run
            for block in response.content:
                if block.type == "tool_use":
                    yield ("tool_call", ToolCall(block.id, block.name, block.input if isinstance(block.input, dict) else {}))
        # Keep the full content (thinking and tool_use blocks) so the next turn continues it.
        yield ("message", {"role": "assistant", "content": _content(response.content)})

    def tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> list[dict]:
        # All results of one turn go back in a single user message.
        return [{
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": call.id, "content": text, "is_error": error}
                for call, text, error in results
            ],
        }]
