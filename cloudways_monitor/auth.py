from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

SESSION_COOKIE_NAME = "cloudways_monitor_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 12
PASSWORD_HASH_ITERATIONS = 100000


@dataclass(frozen=True)
class AuthenticatedUser:
    username: str


@dataclass(frozen=True)
class ParsedPasswordHash:
    algorithm: str
    iterations: int
    salt: str
    expected_hash: str


def hash_password(password: str) -> str:
    salt = secrets.token_urlsafe(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    )
    return (
        f"pbkdf2_sha256:{PASSWORD_HASH_ITERATIONS}:{salt}:"
        f"{_base64_urlencode(derived)}"
    )


def verify_password(*, password: str, password_hash: str) -> bool:
    parsed = _parse_password_hash(password_hash)
    if parsed is None:
        return False

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        parsed.salt.encode("utf-8"),
        parsed.iterations,
    )
    actual_hash = _base64_urlencode(derived)
    return hmac.compare_digest(actual_hash, parsed.expected_hash)


def create_session_token(*, username: str, secret: str) -> str:
    payload = _base64_urlencode(
        json.dumps(
            {"expires_at": int(time.time()) + SESSION_MAX_AGE_SECONDS, "username": username},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = _sign(payload=payload, secret=secret)
    return f"{payload}.{signature}"


def verify_session_token(*, token: str, secret: str) -> AuthenticatedUser | None:
    try:
        payload, signature = token.split(".", 1)
    except ValueError:
        return None
    expected_signature = _sign(payload=payload, secret=secret)
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        decoded = _base64_urldecode(payload)
        data = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return None
    expires_at = data.get("expires_at")
    if not isinstance(expires_at, int) or expires_at <= int(time.time()):
        return None
    username = data.get("username")
    if not isinstance(username, str) or not username:
        return None
    return AuthenticatedUser(username=username)


def _parse_password_hash(password_hash: str) -> ParsedPasswordHash | None:
    delimiter = ":" if ":" in password_hash else "$"
    parts = password_hash.split(delimiter, 3)
    if len(parts) != 4:
        return None

    algorithm, iterations_text, salt, expected_hash = parts
    if algorithm != "pbkdf2_sha256":
        return None
    try:
        iterations = int(iterations_text)
    except ValueError:
        return None
    if iterations <= 0 or not salt or not expected_hash:
        return None
    return ParsedPasswordHash(
        algorithm=algorithm,
        iterations=iterations,
        salt=salt,
        expected_hash=expected_hash,
    )


def _sign(*, payload: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _base64_urlencode(digest)


def _base64_urlencode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64_urldecode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def main() -> None:
    password = getpass.getpass("New dashboard password: ")
    confirmation = getpass.getpass("Confirm dashboard password: ")
    if password != confirmation:
        raise SystemExit("Passwords did not match")
    if not password:
        raise SystemExit("Password cannot be empty")
    print(hash_password(password))


if __name__ == "__main__":
    main()
