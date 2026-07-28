"""Configuration-backed authentication for the B8 release boundary.

The module deliberately avoids a framework dependency.  The B7 fixed accounts
remain in place through the release-candidate period as requested, while API/
Host tokens are HMAC-signed JWTs so a restart does not silently invalidate
valid sessions. Replacing the fixed credentials is an explicit pre-public-
launch task, not an implicit B8 behavior.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


class AuthenticationConfigurationError(ValueError):
    """Raised when the release authentication configuration is unsafe or invalid."""


@dataclass(frozen=True)
class AuthenticatedUser:
    """A configured identity that can authenticate to the service."""

    user_id: str
    display_name: str
    password_hash: str = field(repr=False)


@dataclass(frozen=True)
class AuthSettings:
    """Authentication settings resolved once at process startup."""

    environment: Literal["development", "production"]
    token_secret: bytes = field(repr=False)
    users: tuple[AuthenticatedUser, ...]
    token_ttl_seconds: int = 3_600
    host_token_ttl_seconds: int = 2_592_000
    max_attachment_bytes: int = 10 * 1024 * 1024

    @property
    def allow_user_provisioning(self) -> bool:
        """Keep B7 user-record provisioning behavior through the release candidate."""
        return True

    @classmethod
    def development(cls) -> AuthSettings:
        """Return B7's fixed release-candidate identities for local development."""
        return cls(
            environment="development",
            token_secret=b"codeassist-development-only-signing-secret-not-for-production",
            users=_fixed_b7_users(),
        )

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> AuthSettings:
        """Load a deployment signing secret or fixed release-candidate defaults.

        ``CODEASSIST_ENV=production`` requires a signing secret. The B7 account/
        password set is intentionally retained until the public-launch credential
        replacement; only the signing secret is deployment-specific. ``*_FILE``
        supports Docker/Kubernetes secret mounts without copying a key into the image.
        """
        values = os.environ if environ is None else environ
        environment = values.get("CODEASSIST_ENV", "development").strip().lower()
        if environment == "development":
            return cls.development()
        if environment != "production":
            raise AuthenticationConfigurationError(
                "CODEASSIST_ENV must be either development or production"
            )

        secret = _read_secret_value("CODEASSIST_AUTH_SECRET", values)
        if len(secret.encode("utf-8")) < 32:
            raise AuthenticationConfigurationError(
                "CODEASSIST_AUTH_SECRET must contain at least 32 bytes"
            )
        return cls(
            environment="production",
            token_secret=secret.encode("utf-8"),
            users=_fixed_b7_users(),
            token_ttl_seconds=_read_duration(values, "CODEASSIST_AUTH_TOKEN_TTL_SECONDS", 3_600),
            host_token_ttl_seconds=_read_duration(
                values, "CODEASSIST_HOST_TOKEN_TTL_SECONDS", 2_592_000
            ),
            max_attachment_bytes=_read_attachment_limit(values),
        )


class AuthenticationService:
    """Verify configured passwords and issue restart-stable signed bearer tokens."""

    def __init__(self, settings: AuthSettings) -> None:
        self.settings = settings
        self._users = {user.user_id: user for user in settings.users}

    @property
    def users(self) -> tuple[AuthenticatedUser, ...]:
        return tuple(self._users.values())

    def authenticate_password(self, user_id: str, password: str) -> AuthenticatedUser | None:
        user = self._users.get(user_id)
        if user is None or not verify_password(password, user.password_hash):
            return None
        return user

    def issue_access_token(self, user_id: str, *, now: int | None = None) -> str:
        if user_id not in self._users:
            raise ValueError("cannot issue a token for an unknown user")
        return self._issue(
            subject=user_id,
            purpose="access",
            ttl=self.settings.token_ttl_seconds,
            now=now,
        )

    def authenticate_access_token(self, token: str | None, *, now: int | None = None) -> str | None:
        subject = self._verify(token, purpose="access", now=now)
        return subject if subject in self._users else None

    def issue_host_token(self, host_id: str, *, now: int | None = None) -> str:
        return self._issue(
            subject=host_id,
            purpose="remote_host",
            ttl=self.settings.host_token_ttl_seconds,
            now=now,
        )

    def authenticate_host_token(
        self, token: str | None, host_id: str, *, now: int | None = None
    ) -> bool:
        return self._verify(token, purpose="remote_host", now=now) == host_id

    def _issue(self, *, subject: str, purpose: str, ttl: int, now: int | None) -> str:
        issued_at = int(time.time()) if now is None else now
        payload = {"sub": subject, "purpose": purpose, "iat": issued_at, "exp": issued_at + ttl}
        encoded_header = _b64encode(_encode_json({"alg": "HS256", "typ": "JWT"}))
        encoded_payload = _b64encode(_encode_json(payload))
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        signature = hmac.new(self.settings.token_secret, signing_input, hashlib.sha256).digest()
        return f"{encoded_header}.{encoded_payload}.{_b64encode(signature)}"

    def _verify(self, token: str | None, *, purpose: str, now: int | None) -> str | None:
        if not token:
            return None
        parts = token.split(".")
        if len(parts) != 3:
            return None
        try:
            header = json.loads(_b64decode(parts[0]))
            encoded_payload = _b64decode(parts[1])
            provided_signature = _b64decode(parts[2])
            payload = json.loads(encoded_payload)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return None
        if header != {"alg": "HS256", "typ": "JWT"}:
            return None
        expected_signature = hmac.new(
            self.settings.token_secret, f"{parts[0]}.{parts[1]}".encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(provided_signature, expected_signature):
            return None
        current_time = int(time.time()) if now is None else now
        subject = payload.get("sub")
        if (
            not isinstance(subject, str)
            or payload.get("purpose") != purpose
            or not isinstance(payload.get("exp"), int)
            or payload["exp"] <= current_time
        ):
            return None
        return subject


class LoginRateLimiter:
    """Small in-process failed-login limiter that does not retain passwords or tokens."""

    def __init__(self, *, maximum_failures: int = 5, window_seconds: int = 60) -> None:
        self.maximum_failures = maximum_failures
        self.window_seconds = window_seconds
        self._failures: defaultdict[str, deque[float]] = defaultdict(deque)

    def retry_after(self, key: str, *, now: float | None = None) -> int | None:
        current_time = time.monotonic() if now is None else now
        failures = self._failures[key]
        while failures and failures[0] <= current_time - self.window_seconds:
            failures.popleft()
        if len(failures) < self.maximum_failures:
            return None
        return max(1, int(self.window_seconds - (current_time - failures[0])) + 1)

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        self._failures[key].append(time.monotonic() if now is None else now)

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)


def hash_password(password: str, *, iterations: int = 600_000) -> str:
    """Return a portable PBKDF2-SHA256 password hash for the credentials secret."""
    if not password:
        raise ValueError("password must not be empty")
    if iterations < 10_000:
        raise ValueError("PBKDF2 iterations must be at least 10000")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify the supported hash format without ever comparing plaintext values."""
    try:
        algorithm, iteration_text, salt_hex, digest_hex = encoded_hash.split("$", 3)
        iterations = int(iteration_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (TypeError, ValueError):
        return False
    if algorithm != "pbkdf2_sha256" or iterations < 10_000 or not salt or len(expected) != 32:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _read_secret_value(name: str, values: Mapping[str, str]) -> str:
    file_name = values.get(f"{name}_FILE", "").strip()
    if file_name:
        try:
            return Path(file_name).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise AuthenticationConfigurationError(f"cannot read {name}_FILE") from exc
    value = values.get(name, "").strip()
    if not value:
        raise AuthenticationConfigurationError(f"{name} or {name}_FILE is required in production")
    return value


def _fixed_b7_users() -> tuple[AuthenticatedUser, ...]:
    """Create B7's approved fixed users with hashed, not plaintext, comparisons."""
    return tuple(
        AuthenticatedUser(
            user_id=user_id,
            display_name=user_id,
            password_hash=hash_password(user_id, iterations=10_000),
        )
        for user_id in ("admin", "admin1", "admin2", "admin3")
    )


def _read_duration(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise AuthenticationConfigurationError(f"{name} must be an integer") from exc
    if not 60 <= value <= 31_536_000:
        raise AuthenticationConfigurationError(f"{name} must be between 60 and 31536000")
    return value


def _read_attachment_limit(values: Mapping[str, str]) -> int:
    raw = values.get("CODEASSIST_MAX_ATTACHMENT_BYTES")
    if raw is None:
        return 10 * 1024 * 1024
    try:
        value = int(raw)
    except ValueError as exc:
        raise AuthenticationConfigurationError(
            "CODEASSIST_MAX_ATTACHMENT_BYTES must be an integer"
        ) from exc
    if not 1_024 <= value <= 100 * 1024 * 1024:
        raise AuthenticationConfigurationError(
            "CODEASSIST_MAX_ATTACHMENT_BYTES must be between 1024 and 104857600"
        )
    return value


def _encode_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
