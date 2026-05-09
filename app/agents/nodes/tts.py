"""TTS 노드. 음성 입력이었던 turn 에 대해서만 합성.

AI 응답은 항상 영어이므로 voice 도 항상 영어 (`en`) 로 합성한다.
"""

from __future__ import annotations

from app.agents.state import GraphState
from app.audio.tts_engine import synthesize


def tts_node(state: GraphState) -> dict:
    if not state.get("audio_bytes"):
        return {}
    reply = state.get("ai_reply")
    if not reply:
        return {}
    wav = synthesize(reply, "en")
    if wav is None:
        return {}
    return {"audio_reply": wav}
