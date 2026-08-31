from app.agents.nodes import tts


def test_tts_synthesizes_text_turns_too(monkeypatch):
    monkeypatch.setattr(tts, "synthesize", lambda text, lang: f"{lang}:{text}".encode())

    result = tts.tts_node({"ai_reply": "Hello there"})

    assert result == {"audio_reply": b"en:Hello there"}
