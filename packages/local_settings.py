"""Local, cross-platform runtime settings stored outside the repository."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse


class LocalSettingsError(ValueError):
    """Raised when the local JSON configuration is invalid."""


_ENDPOINT_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,99}$")
_PROVIDER_ENV = {
    "openai": {
        "api_key": "OPENAI_API_KEY",
        "base_url": "OPENAI_BASE_URL",
        "models": "OPENAI_MODEL",
    },
    "anthropic": {
        "api_key": "ANTHROPIC_API_KEY",
        "base_url": "ANTHROPIC_BASE_URL",
        "models": "ANTHROPIC_MODEL",
    },
    "ollama": {
        "api_key": "OLLAMA_API_KEY",
        "base_url": "OLLAMA_BASE_URL",
        "models": "OLLAMA_MODEL",
    },
    "coding_plan": {
        "api_key": "CODING_PLAN_API_KEY",
        "base_url": "CODING_PLAN_BASE_URL",
        "models": "CODING_PLAN_MODELS",
    },
}


@dataclass(frozen=True)
class LocalUser:
    user_id: str
    display_name: str
    password_hash: str


@dataclass(frozen=True)
class ModelTarget:
    endpoint_id: str
    model: str

    @property
    def key(self) -> str:
        return f"{self.endpoint_id}:{self.model}"


@dataclass(frozen=True)
class ModelEndpoint:
    """One API connection that can expose several model names."""

    endpoint_id: str
    name: str
    provider: str
    models: tuple[str, ...]
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    enabled: bool = True

    def environment_names(self) -> dict[str, str]:
        prefix = re.sub(r"[^A-Z0-9]", "_", self.endpoint_id.upper())
        return {
            "api_key": f"DEVPILOT_MODEL_{prefix}_API_KEY",
            "base_url": f"DEVPILOT_MODEL_{prefix}_BASE_URL",
            "models": f"DEVPILOT_MODEL_{prefix}_MODELS",
        }

    def resolve_api_key(self, environment: Mapping[str, str] | None = None) -> str | None:
        values = environment if environment is not None else os.environ
        if self.api_key:
            return self.api_key
        names = self.environment_names()
        provider_name = _PROVIDER_ENV.get(self.provider, {}).get("api_key")
        return _first_environment_value(values, names["api_key"], provider_name)

    def resolve_base_url(self, environment: Mapping[str, str] | None = None) -> str | None:
        values = environment if environment is not None else os.environ
        if self.base_url:
            return self.base_url
        names = self.environment_names()
        provider_name = _PROVIDER_ENV.get(self.provider, {}).get("base_url")
        return _first_environment_value(values, names["base_url"], provider_name)

    def resolve_models(self, environment: Mapping[str, str] | None = None) -> tuple[str, ...]:
        if self.models:
            return self.models
        values = environment if environment is not None else os.environ
        names = self.environment_names()
        provider_name = _PROVIDER_ENV.get(self.provider, {}).get("models")
        raw = _first_environment_value(values, names["models"], provider_name)
        return _normalize_models(raw.split(",") if raw else ())

    def api_key_source(self, environment: Mapping[str, str] | None = None) -> str:
        values = environment if environment is not None else os.environ
        if self.api_key:
            return "saved"
        names = self.environment_names()
        if values.get(names["api_key"], "").strip():
            return names["api_key"]
        provider_name = _PROVIDER_ENV.get(self.provider, {}).get("api_key")
        if provider_name and values.get(provider_name, "").strip():
            return provider_name
        return "none"


@dataclass(frozen=True)
class AgentModelPolicy:
    mode: Literal["manual", "auto"] = "manual"
    allowed_models: tuple[ModelTarget, ...] = field(
        default_factory=lambda: (ModelTarget("fake", "fake-model"),)
    )


@dataclass(frozen=True)
class LocalSettings:
    idle_shutdown_minutes: int = 5
    model_endpoints: tuple[ModelEndpoint, ...] = field(
        default_factory=lambda: (
            ModelEndpoint("fake", "Fake model", "fake", ("fake-model",)),
        )
    )
    default_model: ModelTarget = field(
        default_factory=lambda: ModelTarget("fake", "fake-model")
    )
    agent_model_policy: AgentModelPolicy = field(default_factory=AgentModelPolicy)
    users: tuple[LocalUser, ...] = field(default_factory=tuple)

    @property
    def model_provider(self) -> str:
        """Backward-compatible name used by older API and CLI callers."""
        return self.default_model.endpoint_id

    @property
    def model_name(self) -> str:
        return self.default_model.model

    def endpoint(self, endpoint_id: str) -> ModelEndpoint:
        for endpoint in self.model_endpoints:
            if endpoint.endpoint_id == endpoint_id:
                return endpoint
        raise LocalSettingsError(f"unknown model endpoint: {endpoint_id}")

    def available_targets(
        self, environment: Mapping[str, str] | None = None
    ) -> tuple[ModelTarget, ...]:
        return tuple(
            ModelTarget(endpoint.endpoint_id, model)
            for endpoint in self.model_endpoints
            if endpoint.enabled
            for model in endpoint.resolve_models(environment)
        )

    def validate(self, environment: Mapping[str, str] | None = None) -> LocalSettings:
        if not 1 <= self.idle_shutdown_minutes <= 1_440:
            raise LocalSettingsError("idle_shutdown_minutes must be between 1 and 1440")
        if not self.model_endpoints:
            raise LocalSettingsError("at least one model endpoint is required")
        endpoint_ids: set[str] = set()
        for endpoint in self.model_endpoints:
            _validate_endpoint(endpoint)
            if endpoint.endpoint_id in endpoint_ids:
                raise LocalSettingsError(f"duplicate model endpoint: {endpoint.endpoint_id}")
            endpoint_ids.add(endpoint.endpoint_id)
        available = set(self.available_targets(environment))
        if self.default_model not in available:
            raise LocalSettingsError("default model must refer to an enabled configured model")
        if self.agent_model_policy.mode not in {"manual", "auto"}:
            raise LocalSettingsError("agent model mode must be manual or auto")
        if not self.agent_model_policy.allowed_models:
            raise LocalSettingsError("agent allowed model range cannot be empty")
        if not set(self.agent_model_policy.allowed_models).issubset(available):
            raise LocalSettingsError("agent allowed models must be enabled configured models")
        return self


class LocalSettingsStore:
    """Read and atomically save user-managed settings in ``.devpilot/settings.json``."""

    def __init__(self, workspace_root: Path) -> None:
        self.path = workspace_root / ".devpilot" / "settings.json"

    def load(self) -> LocalSettings:
        if not self.path.exists():
            return LocalSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LocalSettingsError(f"cannot read local settings: {exc}") from exc
        if not isinstance(raw, dict):
            raise LocalSettingsError("local settings must be a JSON object")
        settings = _parse_settings(raw)
        return settings.validate()

    def save(self, settings: LocalSettings) -> LocalSettings:
        settings.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "idle_shutdown_minutes": settings.idle_shutdown_minutes,
            "models": {
                "endpoints": [
                    {
                        "id": endpoint.endpoint_id,
                        "name": endpoint.name,
                        "provider": endpoint.provider,
                        "base_url": endpoint.base_url,
                        "api_key": endpoint.api_key,
                        "models": list(endpoint.models),
                        "enabled": endpoint.enabled,
                    }
                    for endpoint in settings.model_endpoints
                ],
                "default": _target_payload(settings.default_model),
                "agent": {
                    "mode": settings.agent_model_policy.mode,
                    "allowed_models": [
                        _target_payload(target)
                        for target in settings.agent_model_policy.allowed_models
                    ],
                },
            },
            "users": [
                {
                    "id": user.user_id,
                    "display_name": user.display_name,
                    "password_hash": user.password_hash,
                }
                for user in settings.users
            ],
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)
        return settings


def _parse_settings(raw: dict[str, object]) -> LocalSettings:
    idle = raw.get("idle_shutdown_minutes", 5)
    if not isinstance(idle, int):
        raise LocalSettingsError("idle_shutdown_minutes must be an integer")
    users = _parse_users(raw.get("users", []))
    models = raw.get("models")
    if models is None:
        return _parse_legacy_settings(raw, idle=idle, users=users)
    if not isinstance(models, dict):
        raise LocalSettingsError("models must be an object")
    endpoints_raw = models.get("endpoints")
    if not isinstance(endpoints_raw, list):
        raise LocalSettingsError("models.endpoints must be an array")
    endpoints = tuple(_parse_endpoint(item) for item in endpoints_raw)
    default = _parse_target(models.get("default"), "models.default")
    agent_raw = models.get("agent", {})
    if not isinstance(agent_raw, dict):
        raise LocalSettingsError("models.agent must be an object")
    mode = agent_raw.get("mode", "manual")
    if mode not in {"manual", "auto"}:
        raise LocalSettingsError("models.agent.mode must be manual or auto")
    allowed_raw = agent_raw.get("allowed_models", [_target_payload(default)])
    if not isinstance(allowed_raw, list):
        raise LocalSettingsError("models.agent.allowed_models must be an array")
    allowed = tuple(
        _parse_target(item, "models.agent.allowed_models") for item in allowed_raw
    )
    return LocalSettings(
        idle_shutdown_minutes=idle,
        model_endpoints=endpoints,
        default_model=default,
        agent_model_policy=AgentModelPolicy(mode=mode, allowed_models=allowed),
        users=users,
    )


def _parse_legacy_settings(
    raw: dict[str, object], *, idle: int, users: tuple[LocalUser, ...]
) -> LocalSettings:
    """Migrate the former single-provider shape without rewriting on read."""
    model = raw.get("model", {})
    if not isinstance(model, dict):
        raise LocalSettingsError("model must be an object")
    provider = model.get("provider", "fake")
    name = model.get("name", "fake-model")
    if not isinstance(provider, str) or not isinstance(name, str):
        raise LocalSettingsError("model provider and name must be strings")
    endpoint_id = provider.strip().lower()
    target = ModelTarget(endpoint_id, name.strip())
    return LocalSettings(
        idle_shutdown_minutes=idle,
        model_endpoints=(
            ModelEndpoint(endpoint_id, endpoint_id.title(), endpoint_id, (target.model,)),
        ),
        default_model=target,
        agent_model_policy=AgentModelPolicy(allowed_models=(target,)),
        users=users,
    )


def _parse_endpoint(raw: object) -> ModelEndpoint:
    if not isinstance(raw, dict):
        raise LocalSettingsError("each model endpoint must be an object")
    endpoint_id = raw.get("id")
    name = raw.get("name")
    provider = raw.get("provider")
    base_url = raw.get("base_url")
    api_key = raw.get("api_key")
    models = raw.get("models", [])
    enabled = raw.get("enabled", True)
    if not all(isinstance(value, str) for value in (endpoint_id, name, provider)):
        raise LocalSettingsError("each model endpoint requires string id, name and provider")
    if base_url is not None and not isinstance(base_url, str):
        raise LocalSettingsError("model endpoint base_url must be a string or null")
    if api_key is not None and not isinstance(api_key, str):
        raise LocalSettingsError("model endpoint api_key must be a string or null")
    if not isinstance(models, list) or not all(isinstance(item, str) for item in models):
        raise LocalSettingsError("model endpoint models must be an array of strings")
    if not isinstance(enabled, bool):
        raise LocalSettingsError("model endpoint enabled must be a boolean")
    return ModelEndpoint(
        endpoint_id.strip().lower(),
        name.strip(),
        provider.strip().lower(),
        _normalize_models(models),
        base_url.strip() if base_url and base_url.strip() else None,
        api_key.strip() if api_key and api_key.strip() else None,
        enabled,
    )


def _parse_target(raw: object, path: str) -> ModelTarget:
    if not isinstance(raw, dict):
        raise LocalSettingsError(f"{path} must be an object")
    endpoint_id = raw.get("endpoint_id")
    model = raw.get("model")
    if not isinstance(endpoint_id, str) or not isinstance(model, str):
        raise LocalSettingsError(f"{path} requires endpoint_id and model")
    return ModelTarget(endpoint_id.strip().lower(), model.strip())


def _parse_users(raw: object) -> tuple[LocalUser, ...]:
    if not isinstance(raw, list):
        raise LocalSettingsError("users must be an array")
    parsed_users: list[LocalUser] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise LocalSettingsError("each user must be an object")
        user_id = item.get("id")
        display_name = item.get("display_name")
        password_hash = item.get("password_hash")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (user_id, display_name, password_hash)
        ):
            raise LocalSettingsError("each user requires id, display_name and password_hash")
        assert isinstance(user_id, str)
        assert isinstance(display_name, str)
        assert isinstance(password_hash, str)
        if user_id in seen:
            raise LocalSettingsError(f"duplicate local user: {user_id}")
        seen.add(user_id)
        parsed_users.append(LocalUser(user_id, display_name, password_hash))
    return tuple(parsed_users)


def _validate_endpoint(endpoint: ModelEndpoint) -> None:
    if not _ENDPOINT_ID.fullmatch(endpoint.endpoint_id):
        raise LocalSettingsError(
            "model endpoint id must contain lowercase letters, numbers, dots, dashes or underscores"
        )
    if not endpoint.name:
        raise LocalSettingsError("model endpoint name cannot be empty")
    if endpoint.provider not in {
        "fake",
        "openai",
        "anthropic",
        "coding_plan",
        "ollama",
    }:
        raise LocalSettingsError(
            "model endpoint provider must be one of: "
            "fake, openai, anthropic, coding_plan, ollama"
        )
    if endpoint.base_url:
        parsed = urlparse(endpoint.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise LocalSettingsError("model endpoint URL must be an http(s) URL without userinfo")
    if endpoint.api_key is not None and not endpoint.api_key.strip():
        raise LocalSettingsError("saved API key cannot be blank")
    if any(not model for model in endpoint.models):
        raise LocalSettingsError("model names cannot be blank")


def _normalize_models(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if name and name not in result:
            result.append(name)
    return tuple(result)


def _first_environment_value(
    environment: Mapping[str, str], primary: str, fallback: str | None
) -> str | None:
    for name in (primary, fallback):
        if name:
            value = environment.get(name, "").strip()
            if value:
                return value
    return None


def _target_payload(target: ModelTarget) -> dict[str, str]:
    return {"endpoint_id": target.endpoint_id, "model": target.model}
