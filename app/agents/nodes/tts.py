"""TTS node; durable Google speech publishes independently of coaching."""

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
        from app.settings import get_settings
        if get_settings().CONVERSATION_TTS_PROVIDER == 'google_neural2' and state.get('request_id'):
            from app.conversation_audio import generate
            generate(state)
            return {}
        wav = synthesize(reply, "en")
    except Exception as exc:
        log.warning('TTS unavailable; keeping committed text (%s)', type(exc).__name__)
        return {}
    if wav is None:
        return {}
    return {"audio_reply": wav}
