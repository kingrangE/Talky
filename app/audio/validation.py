"""Bound compressed uploads and decoded duration before expensive recognition."""

import io
import base64
import binascii

from app.settings import get_settings


def decode_audio_upload(encoded: str) -> bytes:
    limit = get_settings().MAX_AUDIO_BYTES
    if not isinstance(encoded, str) or len(encoded) > 4 * ((limit + 2) // 3):
        raise ValueError("음성 업로드가 너무 큽니다.")
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("음성 업로드 형식이 올바르지 않습니다.") from exc
    if len(audio) > limit:
        raise ValueError("음성 업로드가 너무 큽니다.")
    return audio


def validate_audio(audio: bytes, *, max_seconds: float | None = None) -> float:
    cfg = get_settings()
    if not 128 <= len(audio) <= cfg.MAX_AUDIO_BYTES:
        raise ValueError("음성 파일 크기가 허용 범위를 벗어났습니다.")
    import av

    limit = min(max_seconds or cfg.MAX_AUDIO_SECONDS, cfg.MAX_AUDIO_SECONDS)
    duration = 0.0
    try:
        with av.open(io.BytesIO(audio)) as container:
            for frame in container.decode(audio=0):
                duration += frame.samples / frame.sample_rate
                if duration > limit:
                    raise ValueError("음성이 허용된 녹음 시간보다 깁니다.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("음성 파일을 읽을 수 없습니다.") from exc
    if duration <= 0:
        raise ValueError("녹음된 음성이 없습니다.")
    return duration
