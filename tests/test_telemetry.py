import json
import logging
import pytest
from app.telemetry import timed_stage


def test_timing_preserves_results_errors_and_excludes_content(caplog):
    caplog.set_level(logging.INFO, logger='talky.metrics')
    payload = {'user_text': 'PRIVATE CONTENT'}
    assert timed_stage('reply', lambda state: state)(payload) is payload
    def fail(_):
        raise ValueError('PRIVATE ERROR')
    with pytest.raises(ValueError):
        timed_stage('reply', fail)(payload)
    records = [json.loads(r.message) for r in caplog.records]
    assert [r['outcome'] for r in records] == ['ok', 'error']
    assert all(r['duration_ms'] >= 0 for r in records)
    assert 'PRIVATE' not in caplog.text
