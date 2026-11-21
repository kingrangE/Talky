import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import api, exam_api
from app.db.models import MockExamSession
from app.mock_exam.privacy import visitor_fingerprint
from app.settings import get_settings


@pytest.fixture
def alb(monkeypatch):
    monkeypatch.setenv('ALB_SUBNET_CIDRS', '["10.0.1.0/24", "10.0.2.0/24"]')
    monkeypatch.setenv('MOCK_EXAM_FINGERPRINT_SALT', 'synthetic-test-only')
    monkeypatch.setenv('MOCK_EXAM_SIGNING_SECRET', 'synthetic-test-only')
    get_settings.cache_clear()
    return TestClient(api.app, client=('10.0.1.10', 12345), base_url='http://exam.example')


@pytest.mark.parametrize('headers', [
    [], [('X-Forwarded-For', 'unknown')], [('X-Forwarded-For', '192.0.2.10:12345')],
    [('X-Forwarded-For', 'fe80::1%eth0')],
    [('X-Forwarded-For', '192.0.2.10'), ('X-Forwarded-For', '192.0.2.11')],
])
def test_alb_rejects_invalid_visitor_metadata(alb, headers):
    assert alb.get('/api/exam/quota', headers=headers).status_code == 400
    assert alb.post('/api/exam/session', headers=headers, json={'consent': True}).status_code == 400


@pytest.mark.parametrize(('subnets', 'peer'), [('[]', '127.0.0.1'), ('["10.0.1.0/24"]', '10.0.9.10')])
def test_direct_exam_requests_use_socket_address(monkeypatch, subnets, peer):
    monkeypatch.setenv('ALB_SUBNET_CIDRS', subnets)
    get_settings.cache_clear()
    monkeypatch.setattr(exam_api.repo, 'quota_status', lambda key: {'matches': key == visitor_fingerprint(peer, 'web')})
    with TestClient(api.app, client=(peer, 12345)) as direct:
        headers = {'X-Forwarded-For': '192.0.2.10', 'X-Forwarded-Proto': 'https'}
        assert direct.get('/api/exam/quota', headers=headers).json() == {'matches': True}
        assert direct.post('/api/auth/logout', headers={**headers, 'Origin': 'http://testserver'}).status_code == 200


def test_alb_exam_daily_and_global_limits_are_independent(alb, pg_db, monkeypatch):
    monkeypatch.setenv('MOCK_EXAM_SCORING_PROFILE', 'basic')
    monkeypatch.setenv('MOCK_EXAM_DAILY_LIMIT', '1')
    monkeypatch.setenv('MOCK_EXAM_GLOBAL_CONCURRENCY', '1')
    get_settings.cache_clear()
    _, scope = pg_db
    visitor_a = {'X-Forwarded-For': '2001:0db8:0:0:0:0:0:1'}
    visitor_a_normalized = {'X-Forwarded-For': '192.0.2.10, 2001:db8::1'}
    visitor_b = {'X-Forwarded-For': '192.0.2.10'}

    def quota(headers):
        result = alb.get('/api/exam/quota', headers=headers)
        assert result.status_code == 200
        return result.json()

    def start(headers):
        return alb.post('/api/exam/session', headers={**headers, 'Origin': 'https://exam.example'},
                        json={'consent': True})

    assert alb.get('/api/health').status_code == 200
    response = start(visitor_a)
    assert response.status_code == 201
    assert 'secure' in response.headers['set-cookie'].lower()
    assert quota(visitor_a_normalized)['daily_used'] == 1
    assert quota(visitor_b) == {'daily_used': 0, 'daily_limit': 1, 'active': 1, 'global_limit': 1, 'allowed': False}
    assert start(visitor_b).status_code == 429

    with scope() as session:
        row = session.scalars(select(MockExamSession)).one()
        assert row.visitor_hash == visitor_fingerprint('2001:db8::1', 'web')
        row.status = 'completed'

    assert quota(visitor_a_normalized) == {'daily_used': 1, 'daily_limit': 1, 'active': 0, 'global_limit': 1, 'allowed': False}
    assert start(visitor_a_normalized).status_code == 429
    assert quota(visitor_b)['allowed']
    assert start(visitor_b).status_code == 201
    assert quota(visitor_b)['daily_used'] == 1
