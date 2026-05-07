"""Piper 기반 TTS 엔진. 언어별 voice 모델 lazy 로드."""

from __future__ import annotations

import io
import urllib.request
import wave
from functools import lru_cache
from pathlib import Path

from app.settings import get_settings

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


def _ensure_voice_files(voice_name: str, cache_dir: Path) -> Path:
    onnx_path, json_path = _voice_paths(voice_name, cache_dir)
    if not onnx_path.exists():
        urllib.request.urlretrieve(_voice_url(voice_name, ""), onnx_path)
    if not json_path.exists():
        urllib.request.urlretrieve(_voice_url(voice_name, ".json"), json_path)
    return onnx_path


@lru_cache(maxsize=4)
def _voice(lang: str):
    from piper.voice import PiperVoice

    cfg = get_settings()
    voice_name = cfg.TTS_VOICE_KO if lang == "ko" else cfg.TTS_VOICE_EN
    onnx_path = _ensure_voice_files(voice_name, Path(cfg.AUDIO_CACHE_DIR))
    return PiperVoice.load(str(onnx_path))


def synthesize(text: str, lang: str) -> bytes:
    """텍스트 → wav 바이트."""
    voice = _voice(lang)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(voice.config.sample_rate)
        voice.synthesize(text, wf)
    return buf.getvalue()
