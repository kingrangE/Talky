"""Local accounts; session identity is set only after password verification."""

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import User
from app.db.postgres import session_scope

ITERATIONS = 600000


def _login_name(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", value):
        raise ValueError("아이디는 영문 소문자·숫자·_.-로 3~64자여야 합니다.")
    return value


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("비밀번호는 12~128자여야 합니다.")
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


def authenticate(login_name: str, password: str) -> str | None:
    try:
        name = _login_name(login_name)
    except ValueError:
        verify_password(password, None)
        return None
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        user = session.scalars(select(User).where(User.login_name == name).with_for_update()).first()
        valid = verify_password(password, user.password_hash if user else None)
        if user is None:
            return None
        locked_until = user.locked_until
        if locked_until and (locked_until if locked_until.tzinfo else locked_until.replace(tzinfo=timezone.utc)) > now:
            return None
        if valid:
            user.failed_logins = 0
            user.locked_until = None
            return str(user.id)
        user.failed_logins += 1
        if user.failed_logins >= 5:
            user.locked_until = now + timedelta(minutes=15)
            user.failed_logins = 0
        return None


if __name__ == "__main__":
    import argparse
    from getpass import getpass

    parser = argparse.ArgumentParser(description="Create an account or attach login to a legacy user.")
    parser.add_argument("login_name")
    parser.add_argument("--existing-user-id")
    args = parser.parse_args()
    password = getpass("Password (12+ characters): ")
    if getpass("Repeat password: ") != password:
        parser.error("Passwords do not match")
    print(register_user(args.login_name, password, existing_user_id=args.existing_user_id))
