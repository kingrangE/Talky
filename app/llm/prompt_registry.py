"""Langfuse owns prompt bodies; relational records hold immutable references only."""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from pathlib import Path

from app.settings import get_settings

log = logging.getLogger(__name__)
PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"
COMMON_PROMPTS = ("talky/ko-coach", "talky/en-reply", "talky/better-expression")
CATALOG = {
    **{name: f"{name.split('/')[-1]}.md" for name in COMMON_PROMPTS},
    "talky/system": "seed_v1.md",
    "talky/evaluation-judge": "evaluation-judge.md",
    "talky/dialogue-judge": "dialogue-judge.md",
    "talky/expression-judge": "expression-judge.md",
}


class PromptUnavailable(RuntimeError):
    pass


def builtin_text(name: str) -> str:
    return (PROMPT_DIR / CATALOG[name]).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def get_client():
    cfg = get_settings()
    if not cfg.LANGFUSE_ENABLED:
        return None
    if not cfg.LANGFUSE_PUBLIC_KEY or not cfg.LANGFUSE_SECRET_KEY:
        raise PromptUnavailable("LANGFUSE_PUBLIC_KEY와 LANGFUSE_SECRET_KEY를 .env에 설정하세요.")
    from langfuse import Langfuse

    return Langfuse(
        public_key=cfg.LANGFUSE_PUBLIC_KEY, secret_key=cfg.LANGFUSE_SECRET_KEY,
        base_url=cfg.LANGFUSE_BASE_URL, timeout=5,
        release=cfg.LANGFUSE_RELEASE or None,
    )


def builtin_reference(name: str) -> dict:
    digest = hashlib.sha256(builtin_text(name).encode()).hexdigest()
    return {"source": "builtin", "name": name, "version": digest}


def remote_prompt(name: str, *, version: int | None = None, label: str | None = None, cache_ttl_seconds: int = 300):
    client = get_client()
    if client is None:
        raise PromptUnavailable("Langfuse가 비활성화되어 있습니다. LANGFUSE_ENABLED=true를 설정하세요.")
    try:
        prompt = client.get_prompt(
            name, version=version, label=label, type="text",
            cache_ttl_seconds=cache_ttl_seconds, max_retries=0, fetch_timeout_seconds=5,
        )
        if not isinstance(prompt.prompt, str) or not prompt.prompt.strip() or prompt.is_fallback:
            raise ValueError("Expected a nonempty managed text prompt")
        if version is not None and prompt.version != version:
            raise ValueError("Prompt version mismatch")
        if get_settings().PROMPT_CACHE_ENABLED:
            from app.llm import prompt_cache
            try:
                prompt_cache.save(name, prompt.version, prompt.prompt)
            except ValueError:
                raise
            except Exception as exc:
                log.warning('Prompt cache write unavailable (%s)', type(exc).__name__)
        return prompt
    except Exception as exc:
        raise PromptUnavailable(f"Langfuse 프롬프트 조회 실패: {name}. 연결·버전·라벨을 확인하세요.") from exc


def resolve_reference(name: str, *, version: int | None = None) -> dict:
    cfg = get_settings()
    if set(cfg.LANGFUSE_PROMPT_VERSIONS) - CATALOG.keys():
        raise PromptUnavailable("LANGFUSE_PROMPT_VERSIONS에 등록되지 않은 프롬프트 이름이 있습니다.")
    version = version if version is not None else cfg.LANGFUSE_PROMPT_VERSIONS.get(name)
    if not cfg.LANGFUSE_ENABLED:
        if version is not None:
            raise PromptUnavailable("숫자 버전 평가는 Langfuse 연결이 필요합니다.")
        return builtin_reference(name)
    if version is not None and cfg.PROMPT_CACHE_ENABLED:
        reference = {"source": "langfuse", "name": name, "version": version}
        read_reference(reference)
        return reference
    prompt = remote_prompt(name, version=version, label=None if version else cfg.LANGFUSE_PROMPT_LABEL)
    return {"source": "langfuse", "name": name, "version": prompt.version}


def read_reference(reference: dict) -> str:
    if reference["source"] == "langfuse":
        try:
            return remote_prompt(reference["name"], version=reference["version"]).prompt
        except PromptUnavailable as exc:
            from app.llm import prompt_cache
            if not get_settings().PROMPT_CACHE_ENABLED or not prompt_cache.transient(exc.__cause__):
                raise
            try:
                cached = prompt_cache.load(reference['name'], reference['version'])
            except Exception:
                raise exc
            if cached is None:
                raise
            log.warning('Using verified managed prompt cache: %s v%s', reference['name'], reference['version'])
            return cached
    if reference["source"] != "builtin" or reference != builtin_reference(reference["name"]):
        raise PromptUnavailable("고정된 로컬 프롬프트와 코드가 다릅니다. 기존 코드로 재개하거나 새 대화를 시작하세요.")
    return builtin_text(reference["name"])


def common_references(*, local: bool = False) -> dict:
    resolve = builtin_reference if local else resolve_reference
    return {name: resolve(name) for name in COMMON_PROMPTS}


def publish_user_prompt(user_id: str, content: str, *, record_id: str) -> dict:
    """The DB activates references; remote writes alone never activate a user's prompt."""
    client = get_client()
    if client is None:
        raise PromptUnavailable("사용자 프롬프트를 게시하려면 Langfuse 연결이 필요합니다.")
    name = f"talky/users/{user_id}/system"
    from langfuse.api import NotFoundError

    try:
        latest = remote_prompt(name, label="latest")
    except PromptUnavailable as exc:
        if not isinstance(exc.__cause__, NotFoundError):
            raise
        latest = None
    if latest is not None and latest.config.get("postgres_prompt_id") == record_id:
        if latest.prompt != content:
            raise ValueError("Existing remote prompt does not match the migration source")
        return {"source": "langfuse", "name": name, "version": latest.version}
    prompt = client.create_prompt(
        name=name, prompt=content, type="text", labels=[],
        config={"postgres_prompt_id": record_id},
        commit_message=f"User prompt reference {record_id}",
    )
    reference = {"source": "langfuse", "name": name, "version": prompt.version}
    if read_reference(reference) != content:
        raise PromptUnavailable("게시한 사용자 프롬프트의 원문 검증에 실패했습니다.")
    return reference


def invoke_with_prompt(llm, messages: list, state: dict, name: str):
    """Telemetry failures cannot retry, suppress or replace the actual LLM call."""
    generation = None
    try:
        client = get_client()
        if client is not None:
            reference = state.get("common_prompt_refs", {}).get(name)
            prompt = remote_prompt(reference["name"], version=reference["version"]) if reference and reference["source"] == "langfuse" else None
            cfg = get_settings()
            generation = client.start_observation(
                name=name, as_type="generation", prompt=prompt,
                trace_context={"trace_id": state["trace_id"]} if state.get("trace_id") else None,
                model=cfg.OPENAI_MODEL if cfg.use_openai else cfg.MODEL_NAME,
                model_parameters={"temperature": 0.7, "max_output_tokens": cfg.LLM_MAX_OUTPUT_TOKENS},
                input=[{"role": {"human": "user", "ai": "assistant"}.get(m.type, m.type), "content": m.content} for m in messages],
                metadata={
                    "conversation_id": state.get("conversation_id"),
                    "db_prompt_id": state.get("prompt_version_id"),
                    "system_prompt_ref": state.get("system_prompt_ref"),
                    "common_prompt_ref": reference,
                },
            )
    except Exception as exc:
        log.warning("Langfuse trace setup failed (%s)", type(exc).__name__)
    try:
        result = llm.invoke(messages)
    except Exception as exc:
        _update_generation(generation, level="ERROR", status_message=type(exc).__name__)
        raise
    else:
        try:
            output = result.model_dump(mode="json") if hasattr(result, "model_dump") else str(result)
            usage = getattr(result, "usage_metadata", None) or {}
            _update_generation(generation, output=output, usage_details={
                key: usage[source] for key, source in (("input", "input_tokens"), ("output", "output_tokens"), ("total", "total_tokens")) if source in usage
            } or None)
        except Exception as exc:
            log.warning("Langfuse output recording failed (%s)", type(exc).__name__)
        return result
    finally:
        if generation is not None:
            try:
                generation.end()
            except Exception as exc:
                log.warning("Langfuse trace end failed (%s)", type(exc).__name__)


def _update_generation(generation, **kwargs):
    if generation is not None:
        try:
            generation.update(**kwargs)
        except Exception as exc:
            log.warning("Langfuse trace update failed (%s)", type(exc).__name__)
