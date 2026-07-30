from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.local_settings import (
    AgentModelPolicy,
    LocalSettings,
    LocalSettingsError,
    LocalSettingsStore,
    ModelEndpoint,
    ModelTarget,
)


def test_legacy_single_model_settings_are_migrated(tmp_path: Path) -> None:
    path = tmp_path / ".devpilot" / "settings.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "idle_shutdown_minutes": 9,
                "model": {"provider": "openai", "name": "legacy-model"},
                "users": [],
            }
        ),
        encoding="utf-8",
    )

    settings = LocalSettingsStore(tmp_path).load()

    assert settings.default_model == ModelTarget("openai", "legacy-model")
    assert settings.model_endpoints[0].models == ("legacy-model",)
    assert settings.agent_model_policy.allowed_models == (
        ModelTarget("openai", "legacy-model"),
    )


def test_multiple_connections_save_keys_and_use_environment_fallbacks(tmp_path: Path) -> None:
    saved = ModelEndpoint(
        endpoint_id="coding-plan",
        name="Coding Plan",
        provider="coding_plan",
        models=("coder-large", "coder-fast"),
        base_url="https://coding.example.test/v1",
        api_key="unit-test-key",
        tool_capability="supported",
    )
    environment = ModelEndpoint(
        endpoint_id="team-gateway",
        name="Team gateway",
        provider="openai",
        models=(),
    )
    settings = LocalSettings(
        model_endpoints=(saved, environment),
        default_model=ModelTarget("coding-plan", "coder-large"),
        agent_model_policy=AgentModelPolicy(
            mode="auto",
            allowed_models=(
                ModelTarget("coding-plan", "coder-large"),
                ModelTarget("team-gateway", "env-model"),
            ),
        ),
    )
    values = {
        "DEVPILOT_MODEL_TEAM_GATEWAY_API_KEY": "environment-key",
        "DEVPILOT_MODEL_TEAM_GATEWAY_BASE_URL": "https://gateway.example.test/v1",
        "DEVPILOT_MODEL_TEAM_GATEWAY_MODELS": "env-model,env-fast",
    }

    assert environment.resolve_api_key(values) == "environment-key"
    assert environment.resolve_base_url(values) == "https://gateway.example.test/v1"
    assert environment.resolve_models(values) == ("env-model", "env-fast")
    settings.validate(values)

    store = LocalSettingsStore(tmp_path)
    store.save(
        LocalSettings(
            model_endpoints=(saved,),
            default_model=ModelTarget("coding-plan", "coder-large"),
            agent_model_policy=AgentModelPolicy(
                mode="auto",
                allowed_models=(ModelTarget("coding-plan", "coder-fast"),),
            ),
        )
    )
    loaded = store.load()

    assert loaded.model_endpoints[0].api_key == "unit-test-key"
    assert loaded.model_endpoints[0].models == ("coder-large", "coder-fast")
    assert loaded.model_endpoints[0].tool_capability == "supported"
    assert loaded.agent_model_policy.mode == "auto"


def test_invalid_custom_model_url_is_rejected() -> None:
    settings = LocalSettings(
        model_endpoints=(
            ModelEndpoint(
                "unsafe",
                "Unsafe",
                "openai",
                ("model",),
                base_url="file:///private/model",
            ),
        ),
        default_model=ModelTarget("unsafe", "model"),
        agent_model_policy=AgentModelPolicy(
            allowed_models=(ModelTarget("unsafe", "model"),)
        ),
    )

    with pytest.raises(LocalSettingsError, match=r"http\(s\)"):
        settings.validate()


def test_environment_sources_properties_and_endpoint_lookup() -> None:
    endpoint = ModelEndpoint("team-api", "Team", "openai", ())
    endpoint_values = {
        "DEVPILOT_MODEL_TEAM_API_API_KEY": "endpoint-key",
        "DEVPILOT_MODEL_TEAM_API_MODELS": " model-a,model-a,model-b ",
    }
    provider_values = {
        "OPENAI_API_KEY": "provider-key",
        "OPENAI_BASE_URL": "https://provider.example.test/v1",
        "OPENAI_MODEL": "provider-model",
    }
    settings = LocalSettings(
        model_endpoints=(ModelEndpoint("team-api", "Team", "openai", ("model-a",)),),
        default_model=ModelTarget("team-api", "model-a"),
        agent_model_policy=AgentModelPolicy(
            allowed_models=(ModelTarget("team-api", "model-a"),)
        ),
    )

    assert ModelTarget("team-api", "model-a").key == "team-api:model-a"
    assert endpoint.resolve_models(endpoint_values) == ("model-a", "model-b")
    assert endpoint.api_key_source(endpoint_values) == "DEVPILOT_MODEL_TEAM_API_API_KEY"
    assert endpoint.resolve_api_key(provider_values) == "provider-key"
    assert endpoint.resolve_base_url(provider_values) == "https://provider.example.test/v1"
    assert endpoint.resolve_models(provider_values) == ("provider-model",)
    assert endpoint.api_key_source(provider_values) == "OPENAI_API_KEY"
    assert endpoint.api_key_source({}) == "none"
    assert settings.model_provider == "team-api"
    assert settings.model_name == "model-a"
    assert settings.endpoint("team-api").name == "Team"
    with pytest.raises(LocalSettingsError, match="unknown"):
        settings.endpoint("missing")


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (LocalSettings(idle_shutdown_minutes=0), "idle_shutdown"),
        (
            LocalSettings(
                model_endpoints=(),
                agent_model_policy=AgentModelPolicy(allowed_models=()),
            ),
            "at least one",
        ),
        (
            LocalSettings(
                model_endpoints=(
                    ModelEndpoint("fake", "One", "fake", ("fake-model",)),
                    ModelEndpoint("fake", "Two", "fake", ("other",)),
                )
            ),
            "duplicate",
        ),
        (
            LocalSettings(default_model=ModelTarget("fake", "missing")),
            "default model",
        ),
        (
            LocalSettings(
                agent_model_policy=AgentModelPolicy(  # type: ignore[arg-type]
                    mode="invalid", allowed_models=(ModelTarget("fake", "fake-model"),)
                )
            ),
            "mode",
        ),
        (
            LocalSettings(agent_model_policy=AgentModelPolicy(allowed_models=())),
            "cannot be empty",
        ),
        (
            LocalSettings(
                agent_model_policy=AgentModelPolicy(
                    allowed_models=(ModelTarget("fake", "missing"),)
                )
            ),
            "allowed models",
        ),
    ],
)
def test_settings_policy_validation_errors(
    settings: LocalSettings, message: str
) -> None:
    with pytest.raises(LocalSettingsError, match=message):
        settings.validate()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not-json", "cannot read"),
        ("[]", "JSON object"),
        ('{"idle_shutdown_minutes":"five"}', "integer"),
        ('{"models":[]}', "models must"),
        ('{"models":{}}', "endpoints"),
        (
            '{"models":{"endpoints":[],"default":{},'
            '"agent":{"mode":"invalid","allowed_models":[]}}}',
            "requires endpoint_id",
        ),
        (
            '{"models":{"endpoints":[],"default":{"endpoint_id":"x","model":"m"},'
            '"agent":[]}}',
            "models.agent must",
        ),
        (
            '{"models":{"endpoints":[],"default":{"endpoint_id":"x","model":"m"},'
            '"agent":{"allowed_models":"x"}}}',
            "allowed_models must",
        ),
        (
            '{"models":{"endpoints":["bad"],'
            '"default":{"endpoint_id":"x","model":"m"}}}',
            "endpoint must",
        ),
        (
            '{"models":{"endpoints":[{"id":"x","name":"X","provider":"openai",'
            '"base_url":1,"models":["m"]}],"default":{"endpoint_id":"x","model":"m"}}}',
            "base_url must",
        ),
        (
            '{"models":{"endpoints":[{"id":"x","name":"X","provider":"openai",'
            '"api_key":1,"models":["m"]}],"default":{"endpoint_id":"x","model":"m"}}}',
            "api_key must",
        ),
        (
            '{"models":{"endpoints":[{"id":"x","name":"X","provider":"openai",'
            '"models":"m"}],"default":{"endpoint_id":"x","model":"m"}}}',
            "models must be an array",
        ),
        (
            '{"models":{"endpoints":[{"id":"x","name":"X","provider":"openai",'
            '"models":["m"],"enabled":"yes"}],'
            '"default":{"endpoint_id":"x","model":"m"}}}',
            "enabled must",
        ),
        (
            '{"models":{"endpoints":[{"id":"x","name":"X","provider":"openai",'
            '"models":["m"],"tool_capability":"maybe"}],'
            '"default":{"endpoint_id":"x","model":"m"}}}',
            "tool_capability must",
        ),
        ('{"model":[]}', "model must"),
        ('{"model":{"provider":1,"name":"model"}}', "must be strings"),
    ],
)
def test_malformed_settings_documents_are_rejected(
    tmp_path: Path, raw: str, message: str
) -> None:
    path = tmp_path / ".devpilot" / "settings.json"
    path.parent.mkdir()
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(LocalSettingsError, match=message):
        LocalSettingsStore(tmp_path).load()


@pytest.mark.parametrize(
    ("endpoint", "message"),
    [
        (ModelEndpoint("Bad ID", "Name", "fake", ("model",)), "id must"),
        (ModelEndpoint("valid", "", "fake", ("model",)), "name cannot"),
        (ModelEndpoint("valid", "Name", "unknown", ("model",)), "provider must"),
        (ModelEndpoint("valid", "Name", "fake", ("model",), api_key=" "), "cannot be blank"),
        (ModelEndpoint("valid", "Name", "fake", ("",)), "model names"),
    ],
)
def test_model_endpoint_validation_errors(
    endpoint: ModelEndpoint, message: str
) -> None:
    settings = LocalSettings(
        model_endpoints=(endpoint,),
        default_model=ModelTarget(endpoint.endpoint_id, endpoint.models[0]),
        agent_model_policy=AgentModelPolicy(
            allowed_models=(ModelTarget(endpoint.endpoint_id, endpoint.models[0]),)
        ),
    )
    with pytest.raises(LocalSettingsError, match=message):
        settings.validate()
