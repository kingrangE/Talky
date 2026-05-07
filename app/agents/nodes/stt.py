"""STT 노드.

audio_bytes 가 있으면 faster-whisper 로 텍스트 + 언어 검출.
없으면 user_text 의 한글 포함 여부로 언어를 휴리스틱 결정.
"""

from __future__ import annotations

from app.agents.state import GraphState
from app.audio.stt_engine import transcribe


def _has_hangul(text: str) -> bool:
    for ch in text:
        if "가" <= ch <= "힣":
            return True
    return False


def stt_node(state: GraphState) -> dict:
    audio = state.get("audio_bytes")
    if audio:
        text, lang = transcribe(audio)
        return {"user_text": text, "language": lang}
    if state.get("language"):
        return {}
    text = state.get("user_text") or ""
    return {"language": "ko" if _has_hangul(text) else "en"}
