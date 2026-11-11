from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import time
import tempfile
import uuid
from pathlib import Path
from functools import lru_cache

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
        if not isinstance(token, str) or not token or len(token) > 2048:
            raise InvalidRecoveryToken("malformed token")
        padded = token + "=" * (-len(token) % 4)
        packed = base64.b64decode(padded.encode(), altchars=b'-_', validate=True)
        # SHA-256 signatures contain arbitrary bytes, including the payload separator.
        size = hashlib.sha256().digest_size
        if len(packed) <= size + 1 or packed[-size-1:-size] != b'.':
            raise InvalidRecoveryToken("malformed token")
        payload, signature = packed[:-size-1], packed[-size:]
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
        configured = get_settings().MOCK_EXAM_AUDIO_KEY
        if configured:
            return configured.encode()
        key_path = self.root.parent / ".audio-key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if not key_path.exists():
            with tempfile.NamedTemporaryFile(dir=key_path.parent, delete=False) as temp:
                temporary = Path(temp.name)
                temp.write(Fernet.generate_key())
            try:
                try:
                    os.link(temporary, key_path)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        return key_path.read_bytes()

    def save(self, session_id: str, question_number: int, audio: bytes) -> str:
        if not 1 <= question_number <= 11:
            raise ValueError("invalid question number")
        directory = (self.root / session_id).resolve()
        if self.root.resolve() not in directory.parents:
            raise ValueError("audio path escapes storage root")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"q{question_number:02d}-{uuid.uuid4().hex}.fernet"
        temp = directory / f".{path.name}.tmp"
        temp.write_bytes(self._fernet.encrypt(audio))
        os.replace(temp, path)
        return str(path.relative_to(self.root.resolve()))

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


    def older_than(self, cutoff: float):
        for path in self.root.glob('*/*'):
            if path.is_file() and path.stat().st_mtime < cutoff:
                yield str(path.relative_to(self.root))


class S3EncryptedAudioStore:
    def __init__(self):
        import boto3
        from botocore.config import Config
        cfg = get_settings()
        if not cfg.MOCK_EXAM_AUDIO_KEY or not cfg.MOCK_EXAM_SIGNING_SECRET:
            raise ValueError('S3 storage requires shared audio and signing keys')
        self.bucket = cfg.AUDIO_S3_BUCKET
        self._fernet = Fernet(cfg.MOCK_EXAM_AUDIO_KEY.encode())
        self.client = boto3.client('s3', config=Config(connect_timeout=3, read_timeout=10,
                                                    retries={'max_attempts': 2, 'mode': 'standard'}))

    @staticmethod
    def _key(relative_path):
        from pathlib import PurePosixPath
        path = PurePosixPath(relative_path)
        if path.is_absolute() or '..' in path.parts or len(path.parts) != 2 or not relative_path.endswith('.fernet'):
            raise ValueError('invalid audio object key')
        return 'audio/' + relative_path

    def save(self, session_id, question_number, audio):
        if not 1 <= question_number <= 11:
            raise ValueError('invalid question number')
        path = f'{session_id}/q{question_number:02d}-{uuid.uuid4().hex}.fernet'
        key = self._key(path)
        self._call('put_object', Bucket=self.bucket, Key=key, Body=self._fernet.encrypt(audio),
                   ServerSideEncryption='AES256', ContentType='application/octet-stream')
        return path

    def load(self, relative_path):
        response = self._call('get_object', Bucket=self.bucket, Key=self._key(relative_path))
        try:
            with response['Body'] as body:
                return self._fernet.decrypt(body.read())
        except InvalidToken as exc:
            raise ValueError('encrypted audio could not be decrypted') from exc

    def delete(self, relative_path):
        if relative_path:
            self._call('delete_object', Bucket=self.bucket, Key=self._key(relative_path))

    def _call(self, operation, **kwargs):
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            return getattr(self.client, operation)(**kwargs)
        except (BotoCoreError, ClientError) as exc:
            raise OSError('audio object storage unavailable') from exc

    def older_than(self, cutoff):
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            pages = self.client.get_paginator('list_objects_v2').paginate(Bucket=self.bucket, Prefix='audio/')
            for page in pages:
                for obj in page.get('Contents', []):
                    path = obj['Key'][len('audio/'):]
                    if obj['LastModified'].timestamp() < cutoff and path.endswith('.fernet'):
                        self._key(path)
                        yield path
        except (BotoCoreError, ClientError) as exc:
            raise OSError('audio object listing unavailable') from exc


@lru_cache(maxsize=1)
def audio_store():
    return S3EncryptedAudioStore() if get_settings().AUDIO_S3_BUCKET else EncryptedAudioStore()
