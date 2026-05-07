"""STT 노드. audio_bytes 가 있으면 텍스트 + 언어 채움."""

from __future__ import annotations

from app.agents.state import GraphState
from app.audio.stt_engine import transcribe


def stt_node(state: GraphState) -> dict:
    audio = state.get("audio_bytes")
    if not audio:
        # 텍스트 입력 모드. user_text 가 이미 채워져 있다고 가정.
        return {}
    text, lang = transcribe(audio)
    return {"user_text": text, "language": lang}
