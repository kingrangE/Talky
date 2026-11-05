"""마이크 입력 위젯. st.audio_input 래퍼."""

from __future__ import annotations

import streamlit as st
from app.settings import get_settings


def mic_input(key: str) -> bytes | None:
    """현재 turn 의 녹음 결과 바이트. 없으면 None.

    호출자가 turn 마다 다른 key 를 넘겨 widget 을 새로 마운트 → 이전 녹음 잔존 방지.
    """
    audio = st.audio_input("🎤 클릭해서 녹음 → 다시 클릭으로 중지", key=key)
    st.caption(
        "마이크가 동작하지 않으면 브라우저 주소창 좌측 자물쇠 → 마이크 권한 '허용' 으로 변경 후 새로고침 하세요."
    )
    if audio is None:
        return None
    if audio.size > get_settings().MAX_AUDIO_BYTES:
        st.error("녹음이 너무 큽니다. 짧게 나눠 말씀해 주세요.")
        return None
    return audio.getvalue()
