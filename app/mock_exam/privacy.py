from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.settings import get_settings


class InvalidRecoveryToken(ValueError):
    pass


def visitor_fingerprint(forwarded_for: str | None, browser_session_id: str) -> str:
    """Return a non-reversible quota key without retaining the raw IP address."""
    cfg = get_settings()
    raw_ip = (forwarded_for or "").split(",", 1)[0].strip()
    identity = raw_ip or browser_session_id
    return hmac.new(
        cfg.MOCK_EXAM_FINGERPRINT_SALT.encode(), identity.encode(), hashlib.sha256
    ).hexdigest()


def _secret_path() -> Path:
    cfg = get_settings()
    root = cfg.mock_exam_data_path
    root.mkdir(parents=True, exist_ok=True)
    return root / ".signing-key"


def _signing_secret() -> bytes:
    configured = get_settings().MOCK_EXAM_SIGNING_SECRET
    if configured:
        return configured.encode()
    path = _secret_path()
    if not path.exists():
        path.write_bytes(secrets.token_bytes(32))
        os.chmod(path, 0o600)
    return path.read_bytes()


def sign_recovery_token(session_id: str, *, ttl_seconds: int) -> str:
    expires = int(time.time()) + ttl_seconds
    payload = f"{session_id}:{expires}".encode()
    signature = hmac.new(_signing_secret(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + b"." + signature).decode().rstrip("=")


def verify_recovery_token(token: str) -> str:
    try:
        padded = token + "=" * (-len(token) % 4)
        packed = base64.urlsafe_b64decode(padded.encode())
        payload, signature = packed.rsplit(b".", 1)
        expected = hmac.new(_signing_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidRecoveryToken("invalid signature")
        session_id, expires = payload.decode().rsplit(":", 1)
        if int(expires) < int(time.time()):
            raise InvalidRecoveryToken("expired token")
        return session_id
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        if isinstance(exc, InvalidRecoveryToken):
            raise
        raise InvalidRecoveryToken("malformed token") from exc


class EncryptedAudioStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().mock_exam_data_path / "audio"
        self.root.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        key_path = self.root.parent / ".audio-key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if not key_path.exists():
            key_path.write_bytes(Fernet.generate_key())
            os.chmod(key_path, 0o600)
        return key_path.read_bytes()

    def save(self, session_id: str, question_number: int, audio: bytes) -> str:
        directory = self.root / session_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"q{question_number:02d}.fernet"
        temp = directory / f".{path.name}.tmp"
        temp.write_bytes(self._fernet.encrypt(audio))
        os.replace(temp, path)
        return str(path.relative_to(self.root))

    def load(self, relative_path: str) -> bytes:
        path = (self.root / relative_path).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("audio path escapes storage root")
        try:
            return self._fernet.decrypt(path.read_bytes())
        except InvalidToken as exc:
            raise ValueError("encrypted audio could not be decrypted") from exc

    def delete(self, relative_path: str | None) -> None:
        if not relative_path:
            return
        path = (self.root / relative_path).resolve()
        if self.root.resolve() not in path.parents:
            return
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
