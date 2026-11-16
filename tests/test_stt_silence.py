import io
import wave

import pytest

from app.audio import stt_engine
from app.errors import UserFacingError, error_code, failure_message


@pytest.fixture
def silent_wav():
    recording = io.BytesIO()
    with wave.open(recording, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 16000 * 2)
    return recording.getvalue()


def broken_language_detection(monkeypatch, message='max() arg is an empty sequence'):
    class Model:
        def transcribe(self, *_args, **_kwargs):
            raise ValueError(message)
    monkeypatch.setattr(stt_engine, '_model', Model)


def test_silent_recording_language_detection_failure_has_speech_guidance(monkeypatch, silent_wav):
    broken_language_detection(monkeypatch)
    with pytest.raises(UserFacingError) as caught:
        stt_engine.transcribe.__wrapped__(silent_wav)
    assert error_code(caught.value) == 'speech_not_detected'
    assert '목소리를 인식하지 못했어요' in failure_message(caught.value)


def test_language_detection_failure_with_speech_is_not_reported_as_silence(monkeypatch, silent_wav):
    import faster_whisper.vad
    broken_language_detection(monkeypatch)
    monkeypatch.setattr(faster_whisper.vad, 'get_speech_timestamps', lambda _: [{'start': 0, 'end': 16000}])
    with pytest.raises(ValueError) as caught:
        stt_engine.transcribe.__wrapped__(silent_wav)
    assert not isinstance(caught.value, UserFacingError)


def test_unrelated_recognition_failure_is_not_reported_as_silence(monkeypatch, silent_wav):
    broken_language_detection(monkeypatch, 'unrelated model failure')
    with pytest.raises(ValueError, match='unrelated model failure') as caught:
        stt_engine.transcribe.__wrapped__(silent_wav)
    assert not isinstance(caught.value, UserFacingError)
