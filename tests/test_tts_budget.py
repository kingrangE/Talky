from concurrent.futures import ThreadPoolExecutor
import json

from fastapi.testclient import TestClient
import pytest

from app import api
from app.audio import usage_budget as budget
from app.errors import UserFacingError
from app.settings import get_settings


def test_budget_preserves_existing_counts_and_reserves_atomically(pg_db, monkeypatch, tmp_path):
    monkeypatch.setenv('MOCK_EXAM_DATA_DIR', str(tmp_path)); get_settings.cache_clear()
    path = tmp_path/'tts-comparison-usage.json'
    path.write_text(json.dumps({'chirp': 723, 'neural2': 49900}))
    def reserve():
        try:
            budget.reserve(100)
            return True
        except UserFacingError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: reserve(), range(2))) == 1
    assert budget.usage() == {'chirp': 723, 'neural2': 50000}
    path.write_text('{"chirp":723,"neural2":-1}')
    with pytest.raises(UserFacingError):
        budget.usage()


def test_retired_comparison_routes_cannot_generate_audio():
    with TestClient(api.app) as web:
        assert web.get('/api/tts-comparison').status_code == 404
        assert web.post('/api/tts-comparison/chirp', json={'segments':['Hello.']}).status_code in {404, 405}
