"""TTS 노드. 모든 AI 영어 응답을 Piper 음성으로 합성한다."""

from __future__ import annotations

from app.agents.state import GraphState
from app.audio.tts_engine import synthesize
import logging

log = logging.getLogger(__name__)


def tts_node(state: GraphState) -> dict:
    reply = state.get("ai_reply")
    if not reply:
        return {}
    try:
        wav = synthesize(reply, "en")
    except Exception as exc:
        log.warning('TTS unavailable; keeping committed text (%s)', type(exc).__name__)
        return {}
    if wav is None:
        return {}
    return {"audio_reply": wav}
