"""Piper 기반 TTS 엔진. 언어별 voice 모델 lazy 로드.

Piper 의 공식 voices 저장소(`rhasspy/piper-voices`) 에는 한국어 voice 가 없다.
다운로드/로드 실패 시 graceful 하게 None 을 반환해 호출자가 TTS 를 skip 하도록 한다.
한국어 voice 가 필요하면 비공식 ONNX/JSON 을 수동으로
`{AUDIO_CACHE_DIR}/tts/{voice_name}.onnx` (+ `.onnx.json`) 위치에 두면 된다.
"""

from __future__ import annotations

import io
import logging
import urllib.request
import wave
from functools import lru_cache
from pathlib import Path

from app.settings import get_settings

log = logging.getLogger(__name__)

PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _voice_paths(voice_name: str, cache_dir: Path) -> tuple[Path, Path]:
    target_dir = cache_dir / "tts"
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"{voice_name}.onnx", target_dir / f"{voice_name}.onnx.json"


def _voice_url(voice_name: str, suffix: str) -> str:
    parts = voice_name.split("-")
    if len(parts) != 3:
        raise ValueError(f"invalid Piper voice name: {voice_name!r}")
    locale, _name, _quality = parts
    lang_part = locale.split("_")[0]
    return f"{PIPER_BASE}/{lang_part}/{locale}/{_name}/{_quality}/{voice_name}.onnx{suffix}"


def _ensure_voice_files(voice_name: str, cache_dir: Path) -> Path | None:
    onnx_path, json_path = _voice_paths(voice_name, cache_dir)
    try:
        if not onnx_path.exists():
            urllib.request.urlretrieve(_voice_url(voice_name, ""), onnx_path)
        if not json_path.exists():
            urllib.request.urlretrieve(_voice_url(voice_name, ".json"), json_path)
    except Exception as exc:
        log.warning("piper voice %s 다운로드 실패: %s", voice_name, exc)
        for p in (onnx_path, json_path):
            if p.exists() and p.stat().st_size == 0:
                p.unlink(missing_ok=True)
        return None
    return onnx_path


@lru_cache(maxsize=4)
def _voice(lang: str):
    cfg = get_settings()
    voice_name = cfg.TTS_VOICE_KO if lang == "ko" else cfg.TTS_VOICE_EN
    if not voice_name:
        return None
    onnx_path = _ensure_voice_files(voice_name, Path(cfg.AUDIO_CACHE_DIR))
    if onnx_path is None:
        return None
    try:
        from piper.voice import PiperVoice

        return PiperVoice.load(str(onnx_path))
    except Exception as exc:
        log.warning("piper voice %s 로드 실패: %s", voice_name, exc)
        return None


def synthesize(text: str, lang: str) -> bytes | None:
    """텍스트 → wav 바이트. voice 가 없으면 None."""
    voice = _voice(lang)
    if voice is None:
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(voice.config.sample_rate)
        voice.synthesize(text, wf)
    return buf.getvalue()
