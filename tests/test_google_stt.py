import sys
from types import SimpleNamespace

import numpy as np

from app.agents.nodes import stt
from app.audio import google_stt


def test_google_transcription_preserves_both_languages_and_covers_long_recordings(monkeypatch):
    samples = np.ones(90 * google_stt.RATE, dtype=np.float32)
    audio_module = SimpleNamespace(decode_audio=lambda *_args, **_kwargs: samples)
    monkeypatch.setitem(sys.modules, 'faster_whisper.audio', audio_module)
    settings = SimpleNamespace(CONVERSATION_STT_PROVIDER='google_chirp3',
                               GOOGLE_CLOUD_PROJECT='test-project', MAX_USER_TEXT_CHARS=4000)
    monkeypatch.setattr(stt, 'get_settings', lambda: settings)
    monkeypatch.setattr(google_stt, 'get_settings', lambda: settings)
    monkeypatch.setattr(google_stt, 'validate_audio', lambda _: 90)
    calls = []
    results = iter([('I like the weather today.', 'en-US'), ('오늘 날씨가 좋아요.', 'ko-KR')])

    def recognize(chunk, project):
        assert project == 'test-project'
        calls.append(len(chunk))
        text, language = next(results)
        return [{'alternatives': [{'transcript': text}], 'languageCode': language}]

    monkeypatch.setattr(google_stt, '_recognize', recognize)
    assert stt.stt_node({'audio_bytes': b'audio'}) == {
        'user_text': 'I like the weather today. 오늘 날씨가 좋아요.',
        'language': 'en',
    }
    assert len(calls) == 2
    assert sum(calls) == len(samples)
    assert all(size <= google_stt.MAX_SAMPLES for size in calls)
