"""STT 노드.

audio_bytes가 있으면 설정된 대화 음성 인식기로 텍스트와 언어를 얻는다.
없으면 user_text 의 한글 포함 여부로 언어를 휴리스틱 결정.
"""

from __future__ import annotations

import json
import logging
from time import monotonic

from app.agents.state import GraphState
from app.audio.stt_engine import transcribe
from app.settings import get_settings
from app.errors import UserFacingError

log = logging.getLogger('talky.metrics')


def _has_hangul(text: str) -> bool:
    for ch in text:
        if "가" <= ch <= "힣":
            return True
    return False


def stt_node(state: GraphState) -> dict:
    audio = state.get("audio_bytes")
    if audio:
        started = monotonic()
        if get_settings().CONVERSATION_STT_PROVIDER == "google_chirp3":
            from app.audio.google_stt import transcribe as google_transcribe
            text, lang = google_transcribe(audio)
        else:
            text, lang = transcribe(audio)
        recognized = monotonic()
        text = text.strip()
        if not text:
            raise UserFacingError('speech_not_detected')
        if len(text) > get_settings().MAX_USER_TEXT_CHARS:
            raise UserFacingError('text_too_long')
        if state.get('request_id'):
            from app.conversation import record_transcript
            record_transcript(state['request_id'], state['user_id'], state['turn_attempt'], text)
            log.info(json.dumps({
                'event': 'transcript_saved',
                'request_id': state['request_id'],
                'attempt': state['turn_attempt'],
                'recognition_ms': round((recognized - started) * 1000, 2),
                'validate_and_save_ms': round((monotonic() - recognized) * 1000, 2),
            }))
        return {"user_text": text, "language": lang}
    if state.get("language"):
        return {}
    text = state.get("user_text") or ""
    return {"language": "ko" if _has_hangul(text) else "en"}
