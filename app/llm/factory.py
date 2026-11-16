"""LLM 팩토리.

`LLM_PROVIDER` 에 따라 Ollama, NVIDIA 또는 OpenAI를 명시적으로 선택한다.
구조화 출력은 `get_structured_llm` 으로 받아야 — Ollama 의 작은 모델(A.X-4.0-Light 등)은
tool calling 을 지원하지 않으므로 json_schema 모드로 강제한다.
"""

from __future__ import annotations

from typing import Any
from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel

from app.settings import get_settings


@lru_cache(maxsize=16)
def get_chat_llm(temperature: float = 0.7, streaming: bool = True) -> BaseChatModel:
    cfg = get_settings()
    if cfg.LLM_PROVIDER in ("openai", "nvidia"):
        from app.llm.capacity import SlotChatNVIDIA, SlotChatOpenAI

        nvidia = cfg.LLM_PROVIDER == "nvidia"
        api_key = cfg.NVIDIA_API_KEY if nvidia else cfg.OPENAI_API_KEY
        if not api_key.strip():
            raise ValueError(f"{'NVIDIA' if nvidia else 'OPENAI'}_API_KEY is required for LLM_PROVIDER={cfg.LLM_PROVIDER}")
        return (SlotChatNVIDIA if nvidia else SlotChatOpenAI)(
            api_key=api_key,
            base_url="https://integrate.api.nvidia.com/v1" if nvidia else "https://api.openai.com/v1",
            model=cfg.llm_model,
            temperature=temperature,
            streaming=streaming,
            timeout=cfg.LLM_TIMEOUT_SECONDS,
            max_retries=0,
            max_tokens=cfg.LLM_MAX_OUTPUT_TOKENS,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}} if nvidia else None,
        )

    from app.llm.capacity import SlotChatOllama as ChatOllama

    return ChatOllama(
        base_url=cfg.OLLAMA_BASE_URL,
        model=cfg.MODEL_NAME,
        temperature=temperature,
        num_predict=cfg.LLM_MAX_OUTPUT_TOKENS,
        num_ctx=cfg.LLM_CONTEXT_TOKENS,
        client_kwargs={"timeout": cfg.LLM_TIMEOUT_SECONDS},
    )


def get_structured_llm(schema: Any, temperature: float = 0.7) -> Any:
    """tool-calling 미지원 Ollama 모델에서도 동작하도록 method 를 강제."""
    base = get_chat_llm(temperature=temperature)
    cfg = get_settings()
    if cfg.LLM_PROVIDER == "nvidia":
        return base.with_structured_output(schema, method="function_calling")
    if cfg.use_openai:
        return base.with_structured_output(schema)
    try:
        return base.with_structured_output(schema, method="json_schema")
    except (TypeError, ValueError):
        return base.with_structured_output(schema, method="json_mode")


def get_local_structured_llm(
    schema: Any, *, model_name: str, temperature: float = 0.1
) -> Any:
    """Use a named local Ollama model for independent offline review agents."""
    from app.llm.capacity import SlotChatOllama as ChatOllama

    base = ChatOllama(
        base_url=get_settings().OLLAMA_BASE_URL,
        model=model_name,
        temperature=temperature,
        num_predict=get_settings().LLM_MAX_OUTPUT_TOKENS,
        num_ctx=get_settings().LLM_CONTEXT_TOKENS,
        client_kwargs={"timeout": get_settings().LLM_TIMEOUT_SECONDS},
    )
    try:
        return base.with_structured_output(schema, method="json_schema")
    except (TypeError, ValueError):
        return base.with_structured_output(schema, method="json_mode")


@lru_cache(maxsize=1)
def get_memory_gate_llm() -> BaseChatModel:
    """Local-only binary model; the caller owns the short inference-slot wait."""
    from langchain_ollama import ChatOllama

    cfg = get_settings()
    return ChatOllama(
        base_url=cfg.OLLAMA_BASE_URL,
        model=cfg.MEMORY_GATE_MODEL or cfg.MODEL_NAME,
        temperature=0,
        num_predict=4,
        num_ctx=cfg.LLM_CONTEXT_TOKENS,
        format={'type': 'integer', 'enum': [0, 1]},
        client_kwargs={'timeout': cfg.MEMORY_GATE_TIMEOUT_SECONDS},
    )
