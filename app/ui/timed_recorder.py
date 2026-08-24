"""Strict, browser-side timed microphone recorder for the mock exam."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import streamlit.components.v1 as components


_COMPONENT_DIR = Path(__file__).parent / "components" / "timed_recorder"
_component = components.declare_component("talky_timed_recorder", path=str(_COMPONENT_DIR))


def timed_recorder(
    *,
    run_id: str,
    preparation_seconds: int,
    response_seconds: int,
    group_read_seconds: int = 0,
    prompt_audio: bytes | None = None,
    prompt_repeat_count: int = 1,
    key: str,
) -> dict[str, Any] | None:
    """Start automatically, record for the exact response window, and return one upload."""
    value = _component(
        run_id=run_id,
        preparation_seconds=preparation_seconds,
        response_seconds=response_seconds,
        group_read_seconds=group_read_seconds,
        prompt_audio_base64=(base64.b64encode(prompt_audio).decode() if prompt_audio else ""),
        prompt_repeat_count=prompt_repeat_count,
        key=key,
        default=None,
    )
    return value if isinstance(value, dict) else None
