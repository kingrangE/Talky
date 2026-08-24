"""faster-whisper 기반 STT 엔진. 모델은 lazy 싱글톤."""

from __future__ import annotations

import io
from functools import lru_cache
from typing import Any, Tuple

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


def transcribe_detailed(audio_bytes: bytes) -> dict[str, Any]:
    """Transcribe one exam response and expose timing-based fluency evidence.

    These metrics are intentionally described as fluency/intelligibility proxies;
    they are not phoneme-level pronunciation scores.
    """
    model = _model()
    segments_iter, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=1,
        vad_filter=True,
        word_timestamps=True,
        language="en",
        condition_on_previous_text=False,
    )
    segments = list(segments_iter)
    words = [
        {
            "word": word.word.strip(),
            "start": float(word.start),
            "end": float(word.end),
            "probability": float(word.probability),
        }
        for segment in segments
        for word in (segment.words or [])
        if word.word.strip() and word.start is not None and word.end is not None
    ]
    text = "".join(segment.text for segment in segments).strip()
    duration = max((float(segment.end) for segment in segments), default=0.0)
    speech_seconds = sum(max(0.0, word["end"] - word["start"]) for word in words)
    pauses = [
        max(0.0, words[index]["start"] - words[index - 1]["end"])
        for index in range(1, len(words))
    ]
    long_pauses = [pause for pause in pauses if pause >= 0.8]
    avg_probability = (
        sum(word["probability"] for word in words) / len(words) if words else 0.0
    )
    articulation_minutes = speech_seconds / 60 if speech_seconds else 0.0
    return {
        "text": text,
        "language": "en" if not (info.language or "en").lower().startswith("ko") else "ko",
        "duration_seconds": round(duration, 3),
        "speech_seconds": round(speech_seconds, 3),
        "word_count": len(words),
        "words_per_minute": round(len(words) / articulation_minutes, 1) if articulation_minutes else 0.0,
        "pause_ratio": round(max(0.0, duration - speech_seconds) / duration, 3) if duration else 1.0,
        "long_pause_count": len(long_pauses),
        "mean_word_probability": round(avg_probability, 3),
        "words": words,
    }
