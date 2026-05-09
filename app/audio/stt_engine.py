"""faster-whisper 기반 STT 엔진. 모델은 lazy 싱글톤."""

from __future__ import annotations

import io
from functools import lru_cache
from typing import Tuple

from app.settings import get_settings


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel

    cfg = get_settings()
    return WhisperModel(
        cfg.WHISPER_MODEL,
        device=cfg.WHISPER_DEVICE,
        compute_type=cfg.WHISPER_COMPUTE_TYPE,
        download_root=cfg.AUDIO_CACHE_DIR + "/whisper",
    )


def transcribe(audio_bytes: bytes) -> Tuple[str, str]:
    """음성 바이트 → (텍스트, 언어). 언어는 ko/en 으로 정규화."""
    model = _model()
    segments, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=1,
        vad_filter=True,
        language=None,
    )
    text = "".join(seg.text for seg in segments).strip()
    lang_raw = (info.language or "en").lower()
    lang = "ko" if lang_raw.startswith("ko") else "en"
    return text, lang
