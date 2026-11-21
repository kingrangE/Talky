"""Local accounts; session identity is set only after password verification."""

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import timedelta
from ipaddress import IPv6Address, ip_address, ip_network

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.db.models import LoginLimit, User
from app.db.postgres import session_scope
from app.settings import get_settings

ITERATIONS = 600000
SOURCE_WINDOW_SECONDS = 60


class LoginRateLimited(Exception):
    pass


def _login_name(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", value):
        raise ValueError("아이디는 영문 소문자·숫자·_.-로 3~64자여야 합니다.")
    return value


def hash_password(password: str) -> str:
    if not 8 <= len(password) <= 24:
        raise ValueError("비밀번호는 8~24자여야 합니다.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    if len(password) > 128:
        return False
    # Unknown users take the same password derivation path to limit enumeration.
    encoded = encoded or f"pbkdf2_sha256${ITERATIONS}${'00' * 16}${'00' * 32}"
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def register_user(login_name: str, password: str, *, existing_user_id: str | None = None) -> str:
    name = _login_name(login_name)
    password_hash = hash_password(password)
    try:
        with session_scope() as session:
            if existing_user_id is None:
                user = User(login_name=name, display_name=name, password_hash=password_hash)
                session.add(user)
            else:
                user = session.scalars(select(User).where(
                    User.id == uuid.UUID(existing_user_id)
                ).with_for_update()).first()
                if user is None or user.login_name is not None or user.password_hash is not None:
                    raise ValueError("연결할 수 없는 기존 사용자입니다.")
                user.login_name = name
                user.password_hash = password_hash
            session.flush()
            return str(user.id)
    except IntegrityError as exc:
        raise ValueError("사용할 수 없는 아이디입니다.") from exc


def _limit_key(scope: str, source: str, name: str = "") -> str:
    return hmac.digest(
        get_settings().MOCK_EXAM_FINGERPRINT_SALT.encode(),
        f"login:{scope}:{source}:{name}".encode(),
        "sha256",
    ).hex()


def _consume(session, key: str, limit: int, seconds: int) -> bool:
    now = func.statement_timestamp()
    expiry = now + timedelta(seconds=seconds)
    expired = LoginLimit.expires_at <= now
    claim = insert(LoginLimit).values(key=key, attempts=1, expires_at=expiry)
    claim = claim.on_conflict_do_update(
        index_elements=[LoginLimit.key],
        set_=dict(
            attempts=case((expired, 1), else_=LoginLimit.attempts + 1),
            expires_at=case((expired, expiry), else_=LoginLimit.expires_at),
        ),
        where=or_(expired, LoginLimit.attempts < limit),
    ).returning(LoginLimit.attempts)
    return session.scalar(claim) is not None


def authenticate(login_name: str, password: str, *, client_host: str | None) -> str | None:
    try:
        address = ip_address(client_host)
        if isinstance(address, IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        source = str(ip_network(f"{address}/64", strict=False)) if address.version == 6 else str(address)
    except ValueError:
        source = "unknown"
    with session_scope() as session:
        if not _consume(session, _limit_key("source", source), 30, SOURCE_WINDOW_SECONDS):
            raise LoginRateLimited
    try:
        name = _login_name(login_name)
    except ValueError:
        verify_password(password, None)
        return None
    key = _limit_key("pair", source, name)
    with session_scope() as session:
        # Hold the pair row until verification finishes so concurrent failures count once each.
        if not _consume(session, key, 5, 15 * 60):
            return None
        user = session.scalar(select(User).where(User.login_name == name))
        valid = verify_password(password, user.password_hash if user else None)
        if user and user.password_hash and valid:
            session.execute(delete(LoginLimit).where(LoginLimit.key == key))
            return str(user.id)
        return None


if __name__ == "__main__":
    import argparse
    from getpass import getpass

    parser = argparse.ArgumentParser(description="Create an account or attach login to a legacy user.")
    parser.add_argument("login_name")
    parser.add_argument("--existing-user-id")
    args = parser.parse_args()
    password = getpass("Password (8–24 characters): ")
    if getpass("Repeat password: ") != password:
        parser.error("Passwords do not match")
    print(register_user(args.login_name, password, existing_user_id=args.existing_user_id))
