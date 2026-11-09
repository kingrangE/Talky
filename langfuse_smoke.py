"""Generate one synthetic reply and print its Langfuse trace URL."""

from app.llm.factory import get_chat_llm
from app.llm.prompt_registry import get_client, remote_prompt
from app.settings import get_settings


def main():
    client = get_client()
    if client is None:
        raise SystemExit("LANGFUSE_ENABLED=true로 실행하세요.")
    if not client.auth_check():
        raise SystemExit("Langfuse 인증에 실패했습니다. URL과 API 키 설정을 확인하세요.")
    print("Langfuse 인증 성공", flush=True)

    prompt = remote_prompt("talky/en-reply", version=1)
    print(f"프롬프트 조회 성공: {prompt.name} v{prompt.version}", flush=True)
    messages = [
        {"role": "system", "content": prompt.prompt},
        {"role": "user", "content": "I had a busy day at work."},
    ]
    settings = get_settings()
    try:
        with client.start_as_current_observation(
            name="english-reply-check",
            as_type="generation",
            prompt=prompt,
            model=settings.OPENAI_MODEL if settings.use_openai else settings.MODEL_NAME,
            input=messages,
        ) as generation:
            response = get_chat_llm(streaming=False).invoke(messages)
            if not isinstance(response.content, str) or not response.content.strip():
                raise ValueError("모델이 빈 답변 또는 텍스트가 아닌 답변을 반환했습니다.")
            generation.update(output=response.content)
            trace_id = generation.trace_id
        print(f"모델 답변: {response.content}", flush=True)
        print(f"Trace: {client.get_trace_url(trace_id=trace_id)}", flush=True)
        return trace_id
    finally:
        client.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"연결 확인 실패 ({type(exc).__name__}). 마지막 성공 단계와 연결 설정을 확인하세요.") from None
