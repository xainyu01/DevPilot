from __future__ import annotations

from packages.security import AuthenticationService, AuthSettings, LoginRateLimiter


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
