"""Local, cross-platform runtime settings stored outside the repository."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


class LocalSettingsError(ValueError):
    """Raised when the local JSON configuration is invalid."""


@dataclass(frozen=True)
class LocalUser:
    user_id: str
    display_name: str
    password_hash: str


@dataclass(frozen=True)
class LocalSettings:
    idle_shutdown_minutes: int = 5
    model_provider: str = "fake"
    model_name: str = "fake-model"
    users: tuple[LocalUser, ...] = field(default_factory=tuple)


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
        idle = raw.get("idle_shutdown_minutes", 5)
        model = raw.get("model", {})
        users = raw.get("users", [])
        if not isinstance(idle, int) or not 1 <= idle <= 1_440:
            raise LocalSettingsError("idle_shutdown_minutes must be between 1 and 1440")
        if not isinstance(model, dict):
            raise LocalSettingsError("model must be an object")
        provider = model.get("provider", "fake")
        name = model.get("name", "fake-model")
        if (
            not isinstance(provider, str)
            or not provider.strip()
            or not isinstance(name, str)
            or not name.strip()
        ):
            raise LocalSettingsError("model provider and name must be non-empty strings")
        if not isinstance(users, list):
            raise LocalSettingsError("users must be an array")
        parsed_users: list[LocalUser] = []
        seen: set[str] = set()
        for item in users:
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
            if user_id in seen:
                raise LocalSettingsError(f"duplicate local user: {user_id}")
            seen.add(user_id)
            parsed_users.append(LocalUser(user_id, display_name, password_hash))
        return LocalSettings(idle, provider.strip().lower(), name.strip(), tuple(parsed_users))

    def save(self, settings: LocalSettings) -> LocalSettings:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "idle_shutdown_minutes": settings.idle_shutdown_minutes,
            "model": {"provider": settings.model_provider, "name": settings.model_name},
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
