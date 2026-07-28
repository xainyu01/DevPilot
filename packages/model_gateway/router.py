"""Deterministic, policy-bounded model selection for workflow agents."""

from __future__ import annotations

from packages.contracts import AgentRole, ModelProfile, ModelSelection


class ModelRoutingError(ValueError):
    """Raised when no model profile satisfies the effective policy."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ModelRouter:
    """Filter first, then sort with a stable cost/latency/quality policy."""

    def __init__(self, profiles: list[ModelProfile]) -> None:
        self._profiles = {profile.id: profile for profile in profiles}

    def route(
        self,
        *,
        role: AgentRole,
        required_capabilities: set[str] | None = None,
        allowed_profiles: list[str] | None = None,
        max_tokens: int | None = None,
        privacy_level: str | None = None,
    ) -> ModelSelection:
        required = required_capabilities or set()
        permitted = set(allowed_profiles or self._profiles)
        candidates: list[ModelProfile] = []
        for profile in self._profiles.values():
            if profile.id not in permitted or not profile.healthy:
                continue
            if profile.allowed_roles and role not in profile.allowed_roles:
                continue
            if not required.issubset(profile.capabilities):
                continue
            if max_tokens is not None and profile.max_tokens < max_tokens:
                continue
            if privacy_level is not None and profile.privacy_level != privacy_level:
                continue
            candidates.append(profile)
        if not candidates:
            code = "model_budget_exceeded" if max_tokens else "model_unavailable"
            raise ModelRoutingError(
                code,
                f"no approved model satisfies role={role.value}, "
                f"capabilities={sorted(required)}, max_tokens={max_tokens}",
            )
        ordered = sorted(
            candidates,
            key=lambda item: (
                item.fallback_rank,
                item.cost_per_1k_tokens,
                item.latency_ms,
                -item.quality_rank,
                item.id,
            ),
        )
        selected = ordered[0]
        return ModelSelection(
            selected=selected,
            fallback_candidates=[item.id for item in ordered[1:]],
            reason=(
                "selected by approved role, capability, health and budget filters; "
                "ordered deterministically by fallback, cost, latency and quality"
            ),
        )


def default_model_router() -> ModelRouter:
    return ModelRouter(
        [
            ModelProfile(
                id="fake-default",
                provider="fake",
                model="fake-model",
                capabilities=["text", "workspace.read"],
                max_tokens=16_000,
                quality_rank=1,
            )
        ]
    )


__all__ = ["ModelRouter", "ModelRoutingError", "default_model_router"]
