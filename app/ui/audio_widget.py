"""마이크 입력 위젯. st.audio_input 래퍼."""

from __future__ import annotations

import streamlit as st


def mic_input(key: str = "voice_input") -> bytes | None:
    """현재 turn 의 녹음 결과 바이트. 없으면 None."""
    audio = st.audio_input("🎤 Tap to speak", key=key)
    if audio is None:
        return None
    return audio.getvalue()
