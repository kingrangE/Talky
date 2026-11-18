from types import SimpleNamespace

import numpy as np
import pytest

from app.audio import stt_engine


def metadata(language='ko', probability=0.55, other=0.4, duration=8):
    alternative = 'en' if language == 'ko' else 'ko'
    return SimpleNamespace(language=language, language_probability=probability,
                           all_language_probs=[(language, probability), (alternative, other)],
                           duration_after_vad=duration)


@pytest.fixture
def engine(monkeypatch):
    import faster_whisper.audio
    import faster_whisper.vad
    audio = np.ones(8 * 16000, dtype=np.float32)
    monkeypatch.setattr(faster_whisper.audio, 'decode_audio', lambda _: audio)
    monkeypatch.setattr(faster_whisper.vad, 'get_speech_timestamps', lambda _: [{'start': 0, 'end': len(audio)}])
    monkeypatch.setattr(stt_engine, 'validate_audio', lambda _: 8)

    class Engine:
        initial = metadata()
        probe = metadata('en', 0.95, 0.03)
        calls = []
        decoded = []
        probe_error = None
        quality = {'en': -0.7, 'ko': -0.7}

        def transcribe(self, audio, **options):
            self.calls.append((audio, options))
            is_probe = isinstance(audio, np.ndarray)
            if is_probe and self.probe_error:
                raise self.probe_error
            info = self.probe if is_probe else self.initial
            language = options.get('language') or info.language

            def segments():
                self.decoded.append(language)
                yield SimpleNamespace(text='Hello there.' if language == 'en' else '안녕하세요.',
                                      tokens=[1, 2, 3, 4], avg_logprob=self.quality[language],
                                      compression_ratio=1, no_speech_prob=0.1)
            return segments(), info

    model = Engine()
    monkeypatch.setattr(stt_engine, '_model', lambda: model)
    return model


@pytest.mark.parametrize('language', ['en', 'ko'])
def test_clear_language_has_no_extra_detection_or_decoding(engine, language):
    engine.initial = metadata(language, 0.95, 0.03)
    assert stt_engine.transcribe.__wrapped__(b'fixture')[1] == language
    assert len(engine.calls) == 1
    assert engine.decoded == [language]


@pytest.mark.parametrize('initial,selected', [('ko', 'en'), ('en', 'ko')])
def test_ambiguous_language_changes_only_with_strong_opposite_evidence(engine, initial, selected):
    engine.initial = metadata(initial)
    engine.probe = metadata(selected, 0.95, 0.03)
    assert stt_engine.transcribe.__wrapped__(b'fixture')[1] == selected
    assert engine.decoded == [selected]
    assert len(engine.calls) == 3
    assert len(engine.calls[1][0]) == 3 * 16000
    assert engine.calls[1][1]['vad_filter'] is False
    assert engine.calls[2][1]['language'] == selected


def test_close_initial_vote_can_be_resolved_by_a_clear_probe_margin(engine):
    engine.initial = metadata('ko', 0.409, 0.402)
    engine.probe = metadata('en', 0.652, 0.201)
    assert stt_engine.transcribe.__wrapped__(b'fixture')[1] == 'en'
    assert engine.decoded == ['en']


@pytest.mark.parametrize('initial,selected', [('ko', 'en'), ('en', 'ko')])
def test_disagreement_uses_short_transcription_evidence_before_selecting_original_language(engine, initial, selected):
    engine.initial = metadata(initial, 0.531, 0.424)
    engine.probe = metadata(selected, 0.541, 0.394)
    engine.quality = {initial: -0.956, selected: -0.706}
    assert stt_engine.transcribe.__wrapped__(b'fixture')[1] == selected
    assert engine.decoded == [selected, initial, selected]
    assert len(engine.calls) == 4
    assert all(call[1]['task'] == 'transcribe' for call in engine.calls)


def test_disagreement_preserves_original_when_its_transcription_fits_better(engine):
    engine.initial = metadata('ko', 0.531, 0.424)
    engine.probe = metadata('en', 0.541, 0.394)
    engine.quality = {'ko': -0.5, 'en': -0.9}
    assert stt_engine.transcribe.__wrapped__(b'fixture')[1] == 'ko'


def test_uncertain_full_detection_is_checked_even_when_probe_prefers_the_same_language(engine):
    engine.initial = metadata('ko', 0.591, 0.127, duration=12)
    engine.probe = metadata('ko', 0.868, 0.084)
    engine.quality = {'ko': -0.803, 'en': -0.617}
    assert stt_engine.transcribe.__wrapped__(b'fixture')[1] == 'en'
    assert engine.decoded == ['en', 'ko', 'en']


@pytest.mark.parametrize('preferred', ['en', 'ko'])
def test_unrelated_middle_window_uses_candidate_transcriptions(engine, preferred):
    engine.initial = metadata('ko', 0.532, 0.314, duration=11)
    engine.probe = SimpleNamespace(language='bo', language_probability=0.7,
                                   all_language_probs=[('bo', 0.7), ('en', 0.043), ('ko', 0.016)])
    engine.quality = {lang: -0.778 if lang == preferred else -1.675 for lang in ('en', 'ko')}
    assert stt_engine.transcribe.__wrapped__(b'fixture')[1] == preferred
    assert engine.decoded == ['en', 'ko', preferred]


@pytest.mark.parametrize('probability,other,duration', [(0.55, 0.4, 2), (0.79, 0.01, 8)])
def test_short_or_uncompetitive_alternative_does_not_trigger_probe(engine, probability, other, duration):
    engine.initial = metadata(probability=probability, other=other, duration=duration)
    assert stt_engine.transcribe.__wrapped__(b'fixture')[1] == 'ko'
    assert len(engine.calls) == 1


@pytest.mark.parametrize('probe', [metadata('en', 0.65, 0.3), metadata('ko', 0.95, 0.03)])
def test_inconclusive_or_agreeing_probe_keeps_original(engine, probe):
    engine.probe = probe
    assert stt_engine.transcribe.__wrapped__(b'fixture')[1] == 'ko'
    if probe.language == 'en':
        assert len(engine.calls) == 3
        assert engine.decoded == ['en', 'ko', 'ko']
    else:
        assert len(engine.calls) == 2
        assert engine.decoded == ['ko']


@pytest.mark.parametrize('difference', [0, 0.1, 0.14])
def test_small_transcription_score_difference_does_not_change_language(engine, difference):
    engine.probe = metadata('en', 0.541, 0.394)
    engine.quality = {'ko': -0.7, 'en': -0.7 + difference}
    assert stt_engine.transcribe.__wrapped__(b'fixture')[1] == 'ko'


@pytest.mark.parametrize('updates', [{'text': ''}, {'no_speech_prob': 0.9}, {'compression_ratio': 3}, {'tokens': [1]}])
def test_empty_or_unreliable_probe_text_is_not_used_for_language_selection(updates):
    segment = SimpleNamespace(text='Hello there.', tokens=[1, 2, 3], avg_logprob=-0.5,
                              compression_ratio=1, no_speech_prob=0.1)
    for key, value in updates.items():
        setattr(segment, key, value)
    assert stt_engine._transcription_score([segment]) is None


def test_probe_failure_preserves_original_result(engine):
    engine.probe_error = RuntimeError('synthetic detection failure')
    assert stt_engine.transcribe.__wrapped__(b'fixture')[1] == 'ko'
    assert engine.decoded == ['ko']


def test_missing_probability_metadata_does_not_guess(engine):
    engine.initial.all_language_probs = None
    assert stt_engine.transcribe.__wrapped__(b'fixture')[1] == 'ko'
    assert len(engine.calls) == 1
