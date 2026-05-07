"""LLM 팩토리.

`LLM_PROVIDER` + `OPENAI_API_KEY` 에 따라 ChatOllama 또는 ChatOpenAI 반환.
"""

from __future__ import annotations

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
