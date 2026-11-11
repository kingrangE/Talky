import pytest
import base64
import uuid

from app.mock_exam.privacy import EncryptedAudioStore, InvalidRecoveryToken
from app.mock_exam import privacy


def test_existing_token_format_round_trips_even_when_signature_contains_separator(monkeypatch):
    monkeypatch.setattr(privacy, '_signing_secret', lambda: b'test-only-recovery-key')
    monkeypatch.setattr(privacy.time, 'time', lambda: 2000000000)
    with_separator = 0
    for index in range(200):
        session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'talky-token:{index}'))
        token = privacy.sign_recovery_token(session_id, ttl_seconds=60)
        packed = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
        with_separator += b'.' in packed[-32:]
        assert privacy.verify_recovery_token(token) == session_id
    assert with_separator > 0


def test_recovery_tokens_reject_tampering_expiry_and_truncated_signatures(monkeypatch):
    monkeypatch.setattr(privacy, '_signing_secret', lambda: b'test-only-recovery-key')
    monkeypatch.setattr(privacy.time, 'time', lambda: 2000000000)
    token = privacy.sign_recovery_token(str(uuid.uuid4()), ttl_seconds=60)
    packed = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
    for invalid in [base64.urlsafe_b64encode(b'x' + packed[1:]).decode(), base64.urlsafe_b64encode(packed[:-1]).decode(), '###', '', None]:
        with pytest.raises(InvalidRecoveryToken):
            privacy.verify_recovery_token(invalid)
    monkeypatch.setattr(privacy.time, 'time', lambda: 2000000061)
    with pytest.raises(InvalidRecoveryToken, match='expired'):
        privacy.verify_recovery_token(token)


def test_audio_store_encrypts_and_round_trips(tmp_path):
    store = EncryptedAudioStore(tmp_path / "audio")
    relative = store.save("session-id", 1, b"not-real-audio")
    encrypted = (tmp_path / "audio" / relative).read_bytes()
    assert b"not-real-audio" not in encrypted
    assert store.load(relative) == b"not-real-audio"
    store.delete(relative)
    assert not (tmp_path / "audio" / relative).exists()


def test_audio_store_rejects_path_escape(tmp_path):
    store = EncryptedAudioStore(tmp_path / "audio")
    with pytest.raises(ValueError):
        store.load("../outside")
