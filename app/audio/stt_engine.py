"""faster-whisper 기반 STT 엔진. 모델은 lazy 싱글톤."""

from __future__ import annotations

from app.runtime import audio_capacity

import io
import json
import logging
import time
from functools import lru_cache
from typing import Any, Tuple

from app.settings import get_settings
from app.audio.validation import validate_audio
from app.errors import UserFacingError

log = logging.getLogger('talky.metrics')


def _transcription_score(segments):
    segments = list(segments)
    tokens = sum(len(segment.tokens) for segment in segments)
    if (tokens < 3 or not any(segment.text.strip() for segment in segments)
            or any(segment.no_speech_prob >= 0.6 or segment.compression_ratio > 2.4 for segment in segments)):
        return None
    return sum(segment.avg_logprob * len(segment.tokens) for segment in segments) / tokens


def _recheck_language(model, audio_bytes, info):
    probabilities = dict(info.all_language_probs or [])
    initial = info.language
    alternative = 'en' if initial == 'ko' else 'ko'
    if (initial not in {'ko', 'en'} or info.language_probability >= 0.8
            or info.duration_after_vad < 4 or probabilities.get(alternative, 0) < 0.05):
        return initial
    started = time.perf_counter()
    selected = initial
    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import collect_chunks, get_speech_timestamps
        audio = decode_audio(io.BytesIO(audio_bytes))
        chunks = get_speech_timestamps(audio)
        if not chunks:
            return initial
        speech = collect_chunks(audio, chunks)
        middle = len(speech) // 2
        sample = speech[middle - 24000:middle + 24000]
        # transcribe returns language metadata before its lazy text decoder runs.
        probe_segments, probe = model.transcribe(sample, beam_size=1, vad_filter=False, language=None,
                                               task='transcribe', temperature=0, condition_on_previous_text=False)
        scores = dict(probe.all_language_probs or [])
        # shortcut: thresholds use synthetic checks; recalibrate with labeled human recordings.
        if (probe.language == alternative and probe.language_probability >= 0.6
                and scores.get(alternative, 0) - scores.get(initial, 0) >= 0.4
                and probabilities[alternative] + scores.get(alternative, 0)
                - probabilities.get(initial, 1) - scores.get(initial, 0) >= 0.3):
            selected = alternative
        elif scores.get(alternative, 0) >= 0.05:
            alternative_segments = probe_segments
            if probe.language != alternative:
                alternative_segments, _ = model.transcribe(sample, beam_size=1, vad_filter=False, language=alternative,
                                                           task='transcribe', temperature=0, condition_on_previous_text=False)
            alternative_score = _transcription_score(alternative_segments)
            original_segments = probe_segments
            if probe.language != initial:
                original_segments, _ = model.transcribe(sample, beam_size=1, vad_filter=False, language=initial,
                                                       task='transcribe', temperature=0, condition_on_previous_text=False)
            original_score = _transcription_score(original_segments)
            if (alternative_score is not None and original_score is not None
                    and alternative_score >= -1 and alternative_score - original_score >= 0.15):
                selected = alternative
    except Exception as exc:
        log.warning('language_recheck_unavailable error_type=%s', type(exc).__name__)
    finally:
        log.info(json.dumps({'event': 'stt_language_recheck', 'initial_language': initial,
                            'selected_language': selected, 'duration_ms': round((time.perf_counter() - started) * 1000, 2)}))
    return selected


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


@audio_capacity
def transcribe(audio_bytes: bytes) -> Tuple[str, str]:
    """음성 바이트 → (텍스트, 언어). 언어는 ko/en 으로 정규화."""
    validate_audio(audio_bytes)
    model = _model()
    try:
        segments, info = model.transcribe(
            io.BytesIO(audio_bytes),
            beam_size=1,
            vad_filter=True,
            language=None,
            task='transcribe',
        )
    except ValueError as exc:
        # faster-whisper 1.0 can fail language detection after VAD removes all speech.
        if str(exc) == 'max() arg is an empty sequence':
            from faster_whisper.audio import decode_audio
            from faster_whisper.vad import get_speech_timestamps
            if not get_speech_timestamps(decode_audio(io.BytesIO(audio_bytes))):
                raise UserFacingError('speech_not_detected') from exc
        raise
    language = _recheck_language(model, audio_bytes, info)
    if language != info.language:
        segments, _ = model.transcribe(io.BytesIO(audio_bytes), beam_size=1, vad_filter=True,
                                      language=language, task='transcribe')
    text = "".join(seg.text for seg in segments).strip()
    lang_raw = (language or "en").lower()
    lang = "ko" if lang_raw.startswith("ko") else "en"
    return text, lang


@audio_capacity
def transcribe_detailed(audio_bytes: bytes) -> dict[str, Any]:
    """Transcribe one exam response and expose timing-based fluency evidence.

    These metrics are intentionally described as fluency/intelligibility proxies;
    they are not phoneme-level pronunciation scores.
    """
    validate_audio(audio_bytes)
    model = _model()
    segments_iter, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=1,
        vad_filter=True,
        word_timestamps=True,
        language="en",
        task='transcribe',
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
