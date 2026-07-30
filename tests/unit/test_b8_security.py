from __future__ import annotations

from pathlib import Path

import pytest

from packages.security import (
    AuthenticatedUser,
    AuthenticationConfigurationError,
    AuthenticationService,
    AuthSettings,
    LoginRateLimiter,
    hash_password,
)
from packages.security.auth import verify_password


def test_b7_fixed_users_issue_signed_jwt_that_survives_service_recreation() -> None:
    settings = AuthSettings.development()
    issuer = AuthenticationService(settings)
    token = issuer.issue_access_token("admin1", now=1_000)

    assert token.count(".") == 2
    assert AuthenticationService(settings).authenticate_access_token(token, now=1_001) == "admin1"
    assert issuer.authenticate_access_token(f"{token}x", now=1_001) is None
    assert issuer.authenticate_access_token(token, now=4_600) is None


def test_host_token_cannot_be_used_as_a_user_token() -> None:
    service = AuthenticationService(AuthSettings.development())
    host_token = service.issue_host_token("host-1", now=1_000)

    assert service.authenticate_host_token(host_token, "host-1", now=1_001)
    assert service.authenticate_access_token(host_token, now=1_001) is None


def test_login_rate_limiter_expires_old_failures() -> None:
    limiter = LoginRateLimiter(maximum_failures=2, window_seconds=10)
    limiter.record_failure("client", now=0)
    limiter.record_failure("client", now=1)

    assert limiter.retry_after("client", now=2) is not None
    assert limiter.retry_after("client", now=12) is None
    limiter.clear("client")
    assert limiter.retry_after("client", now=12) is None


def test_production_auth_settings_accept_secret_file_and_limits(tmp_path: Path) -> None:
    secret_file = tmp_path / "auth.secret"
    secret_file.write_text("x" * 40, encoding="utf-8")
    extra = AuthenticatedUser(
        user_id="developer",
        display_name="Developer",
        password_hash=hash_password("safe-password", iterations=10_000),
    )

    settings = AuthSettings.from_environment(
        {
            "DEVPILOT_ENV": "production",
            "DEVPILOT_AUTH_SECRET_FILE": str(secret_file),
            "DEVPILOT_AUTH_TOKEN_TTL_SECONDS": "120",
            "DEVPILOT_HOST_TOKEN_TTL_SECONDS": "240",
            "DEVPILOT_MAX_ATTACHMENT_BYTES": "2048",
        },
        additional_users=(extra,),
    )

    assert settings.environment == "production"
    assert settings.token_secret == b"x" * 40
    assert settings.token_ttl_seconds == 120
    assert settings.host_token_ttl_seconds == 240
    assert settings.max_attachment_bytes == 2048
    assert {user.user_id for user in settings.users} == {
        "admin",
        "admin1",
        "admin2",
        "admin3",
        "developer",
    }


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"DEVPILOT_ENV": "staging"}, "development or production"),
        ({"DEVPILOT_ENV": "production"}, "is required"),
        (
            {"DEVPILOT_ENV": "production", "DEVPILOT_AUTH_SECRET": "short"},
            "at least 32 bytes",
        ),
        (
            {
                "DEVPILOT_ENV": "production",
                "DEVPILOT_AUTH_SECRET": "x" * 40,
                "DEVPILOT_AUTH_TOKEN_TTL_SECONDS": "bad",
            },
            "must be an integer",
        ),
        (
            {
                "DEVPILOT_ENV": "production",
                "DEVPILOT_AUTH_SECRET": "x" * 40,
                "DEVPILOT_AUTH_TOKEN_TTL_SECONDS": "10",
            },
            "between 60",
        ),
        (
            {
                "DEVPILOT_ENV": "production",
                "DEVPILOT_AUTH_SECRET": "x" * 40,
                "DEVPILOT_MAX_ATTACHMENT_BYTES": "bad",
            },
            "must be an integer",
        ),
        (
            {
                "DEVPILOT_ENV": "production",
                "DEVPILOT_AUTH_SECRET": "x" * 40,
                "DEVPILOT_MAX_ATTACHMENT_BYTES": "10",
            },
            "between 1024",
        ),
    ],
)
def test_invalid_production_auth_configuration_is_rejected(
    environment: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(AuthenticationConfigurationError, match=message):
        AuthSettings.from_environment(environment)


def test_unreadable_secret_file_and_fixed_user_conflict_are_rejected(
    tmp_path: Path,
) -> None:
    with pytest.raises(AuthenticationConfigurationError, match="cannot read"):
        AuthSettings.from_environment(
            {
                "DEVPILOT_ENV": "production",
                "DEVPILOT_AUTH_SECRET_FILE": str(tmp_path / "missing"),
            }
        )
    conflicting = AuthenticatedUser(
        user_id="admin",
        display_name="Other Admin",
        password_hash=hash_password("different", iterations=10_000),
    )
    with pytest.raises(AuthenticationConfigurationError, match="conflicts"):
        AuthSettings.from_environment({}, additional_users=(conflicting,))


def test_password_and_token_malformed_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        hash_password("")
    with pytest.raises(ValueError, match="at least 10000"):
        hash_password("password", iterations=9999)
    encoded = hash_password("password", iterations=10_000)
    assert verify_password("password", encoded)
    assert not verify_password("wrong", encoded)
    assert not verify_password("password", "not-a-hash")
    assert not verify_password("password", "pbkdf2_sha256$1$00$00")

    service = AuthenticationService(AuthSettings.development())
    assert service.authenticate_access_token(None) is None
    assert service.authenticate_access_token("not.a.jwt.extra") is None
    assert service.authenticate_access_token("%%%.%%%.%%%") is None
    with pytest.raises(ValueError, match="unknown user"):
        service.issue_access_token("missing")


def test_replace_users_invalidates_existing_subject() -> None:
    service = AuthenticationService(AuthSettings.development())
    token = service.issue_access_token("admin2", now=1_000)
    service.replace_users(tuple(user for user in service.users if user.user_id != "admin2"))

    assert service.authenticate_access_token(token, now=1_001) is None
