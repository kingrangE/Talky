import pytest

from app.mock_exam.privacy import EncryptedAudioStore, InvalidRecoveryToken


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
