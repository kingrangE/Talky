"""Language router. ko 면 ko_coach, en 면 en_reply 로 분기."""

from __future__ import annotations

from app.agents.state import GraphState


def route_by_lang(state: GraphState) -> str:
    return "ko" if state.get("language") == "ko" else "en"
