"""TTS 노드. 음성 입력이었던 turn 에 대해서만 합성."""

from __future__ import annotations

from app.agents.state import GraphState
from app.audio.tts_engine import synthesize


def tts_node(state: GraphState) -> dict:
    if not state.get("audio_bytes"):
        return {}
    reply = state.get("ai_reply")
    if not reply:
        return {}
    lang = state.get("language") or "en"
    wav = synthesize(reply, lang)
    return {"audio_reply": wav}
