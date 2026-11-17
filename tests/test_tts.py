from app.agents.nodes import tts
from types import SimpleNamespace


def test_tts_synthesizes_text_turns_too(monkeypatch):
    monkeypatch.setattr(tts, "synthesize", lambda text, lang: f"{lang}:{text}".encode())

    result = tts.tts_node({"ai_reply": "Hello there"})

    assert result == {"audio_reply": b"en:Hello there"}


def test_voice_reuses_one_model_with_bounded_cpu_threads(monkeypatch, tmp_path):
    import sys
    from app.audio import tts_engine
    model = tmp_path / 'voice.onnx'
    model.write_bytes(b'test-model')
    model.with_suffix('.onnx.json').write_text('{"sample_rate":22050}')
    sessions = []

    def session(path, *, sess_options, providers):
        assert path == str(model)
        assert sess_options.intra_op_num_threads == 1
        assert providers == ['CPUExecutionProvider']
        sessions.append(object())
        return sessions[-1]

    monkeypatch.setitem(sys.modules, 'onnxruntime', SimpleNamespace(SessionOptions=SimpleNamespace, InferenceSession=session))
    monkeypatch.setitem(sys.modules, 'piper', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'piper.config', SimpleNamespace(PiperConfig=SimpleNamespace(from_dict=lambda config: config)))
    monkeypatch.setitem(sys.modules, 'piper.voice', SimpleNamespace(PiperVoice=lambda **kwargs: SimpleNamespace(**kwargs)))
    monkeypatch.setattr(tts_engine, '_ensure_voice_files', lambda *_: model)
    tts_engine._voice.cache_clear()
    try:
        voice = tts_engine._voice('en')
        assert voice.config == {'sample_rate': 22050}
        assert tts_engine._voice('en') is voice
        assert len(sessions) == 1
    finally:
        tts_engine._voice.cache_clear()
