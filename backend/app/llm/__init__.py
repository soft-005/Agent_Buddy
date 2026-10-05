"""LLM backends. create_llm() picks one from the model's "api" field."""

from ..providers import ModelConfig
from .base import LLM, LLMError


def create_llm(cfg: ModelConfig) -> LLM:
    if cfg.api == "openai-completions":
        from .openai_compat import OpenAICompatLLM

        return OpenAICompatLLM(cfg)
    if cfg.api == "anthropic-messages":
        from .anthropic_chat import AnthropicLLM

        return AnthropicLLM(cfg)
    raise ValueError(f"Unsupported api '{cfg.api}' for provider '{cfg.provider}'")


__all__ = ["LLM", "LLMError", "create_llm"]
