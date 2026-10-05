"""Shared types for LLM backends."""

from typing import AsyncIterator, Protocol

from ..tools import ToolCall

# A streamed reply yields ("text", str) for each piece of visible reply text,
# ("tool_call", ToolCall) for each tool the model wants to use (after the text),
# then exactly one ("message", dict): the assistant message to append to history.
StreamEvent = tuple[str, object]


class LLMError(Exception):
    """A model call failed. The message is safe to show to the user."""


class LLM(Protocol):
    async def complete(
        self, system: str, messages: list, max_tokens: int | None = None
    ) -> tuple[str, dict]:
        """Return (reply text, assistant message to append to history). No tools."""
        ...

    def stream(
        self, system: str, messages: list, max_tokens: int | None = None, tools: list[dict] | None = None
    ) -> AsyncIterator[StreamEvent]:
        """Yield the reply as it is generated. tools are provider-neutral definitions
        (name, description, parameters). Raises LLMError on failure."""
        ...

    def tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> list[dict]:
        """History messages carrying (call, result text, is_error) back to the model."""
        ...
