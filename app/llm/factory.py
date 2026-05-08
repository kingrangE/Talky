"""LLM 팩토리.

`LLM_PROVIDER` + `OPENAI_API_KEY` 에 따라 ChatOllama 또는 ChatOpenAI 반환.
구조화 출력은 `get_structured_llm` 으로 받아야 — Ollama 의 작은 모델(A.X-4.0-Light 등)은
tool calling 을 지원하지 않으므로 json_schema 모드로 강제한다.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from app.settings import get_settings


def get_chat_llm(temperature: float = 0.7, streaming: bool = True) -> BaseChatModel:
    cfg = get_settings()
    if cfg.use_openai:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=cfg.OPENAI_API_KEY,
            model=cfg.OPENAI_MODEL,
            temperature=temperature,
            streaming=streaming,
        )

    from langchain_ollama import ChatOllama

    return ChatOllama(
        base_url=cfg.OLLAMA_BASE_URL,
        model=cfg.MODEL_NAME,
        temperature=temperature,
    )


def get_structured_llm(schema: Any, temperature: float = 0.7) -> Any:
    """tool-calling 미지원 Ollama 모델에서도 동작하도록 method 를 강제."""
    base = get_chat_llm(temperature=temperature)
    cfg = get_settings()
    if cfg.use_openai:
        return base.with_structured_output(schema)
    try:
        return base.with_structured_output(schema, method="json_schema")
    except (TypeError, ValueError):
        return base.with_structured_output(schema, method="json_mode")
