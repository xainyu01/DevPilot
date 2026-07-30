"""Policy-bounded LLM selection for one Agent invocation."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from packages.contracts import ChatMessage, ChatRequest, TokenUsage
from packages.local_settings import LocalSettings, ModelTarget

from .gateway import ModelGateway


@dataclass(frozen=True)
class RuntimeModelSelection:
    target: ModelTarget
    mode: str
    reason: str
    selector: ModelTarget | None = None
    fallback_used: bool = False
    selector_usage: TokenUsage = field(default_factory=TokenUsage)
    selector_call: dict[str, object] | None = None


class ModelChoiceError(ValueError):
    """Raised when a requested target is outside the configured policy."""


class ModelChoiceService:
    """Resolve manual/default choices or ask an allowed model to choose."""

    def __init__(self, gateway: ModelGateway, settings: LocalSettings) -> None:
        self.gateway = gateway
        self.settings = settings

    async def choose(
        self,
        *,
        messages: list[ChatMessage],
        mode: str | None = None,
        requested: ModelTarget | None = None,
        allowed: tuple[ModelTarget, ...] | None = None,
    ) -> RuntimeModelSelection:
        effective_mode = mode or self.settings.agent_model_policy.mode
        policy_permitted = self.settings.agent_model_policy.allowed_models
        if allowed is not None and not set(allowed).issubset(policy_permitted):
            raise ModelChoiceError("requested allowed range exceeds the configured policy")
        permitted = allowed or policy_permitted
        configured = set(self.settings.available_targets())
        if not permitted or not set(permitted).issubset(configured):
            raise ModelChoiceError("allowed model range contains an unavailable model")

        if requested is not None:
            if requested not in permitted:
                raise ModelChoiceError("requested model is outside the allowed model range")
            return RuntimeModelSelection(
                target=requested,
                mode="manual",
                reason="explicit model selected by the user",
            )
        if effective_mode == "manual":
            target = self.settings.default_model
            if target not in permitted:
                target = permitted[0]
            return RuntimeModelSelection(
                target=target,
                mode="manual",
                reason="configured default model selected within the allowed range",
            )
        if effective_mode != "auto":
            raise ModelChoiceError("model selection mode must be manual or auto")
        if len(permitted) == 1:
            return RuntimeModelSelection(
                target=permitted[0],
                mode="auto",
                reason="the allowed range contains one model",
            )
        return await self._choose_with_model(messages=messages, permitted=permitted)

    async def _choose_with_model(
        self,
        *,
        messages: list[ChatMessage],
        permitted: tuple[ModelTarget, ...],
    ) -> RuntimeModelSelection:
        selector = (
            self.settings.default_model
            if self.settings.default_model in permitted
            else permitted[0]
        )
        candidates = [
            {"endpoint_id": target.endpoint_id, "model": target.model} for target in permitted
        ]
        task_text = "\n".join(message.text_content() for message in messages)[-8_000:]
        prompt = (
            "You are DevPilot's model router. Select the best model for the task from the "
            "exact allowed candidates. Do not answer the task. Return only one JSON object "
            'with keys "endpoint_id", "model", and "reason". Do not invent a candidate.\n'
            f"Allowed candidates: {json.dumps(candidates, ensure_ascii=False)}\n"
            f"Task: {task_text}"
        )
        started = time.perf_counter()
        try:
            response = await self.gateway.invoke(
                ChatRequest(
                    provider=selector.endpoint_id,
                    model=selector.model,
                    messages=[ChatMessage.from_text("user", prompt)],
                    temperature=0,
                    metadata={"purpose": "policy_bounded_model_selection"},
                )
            )
            payload = _parse_selection_json(response.text)
            selected = ModelTarget(
                endpoint_id=str(payload.get("endpoint_id", "")).strip().lower(),
                model=str(payload.get("model", "")).strip(),
            )
            if selected not in permitted:
                raise ModelChoiceError("selector returned a model outside the allowed range")
            reason = str(payload.get("reason", "")).strip() or "selected by the routing model"
            return RuntimeModelSelection(
                target=selected,
                mode="auto",
                reason=reason,
                selector=selector,
                selector_usage=response.usage,
                selector_call={
                    "kind": "selector",
                    "endpoint_id": selector.endpoint_id,
                    "requested_model": selector.model,
                    "returned_model": response.response_metadata.get(
                        "provider_model", response.model
                    ),
                    "provider_request_id": response.response_metadata.get(
                        "provider_request_id"
                    ),
                    "usage": response.usage.model_dump(mode="json"),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "finish_reason": response.finish_reason,
                    "stop_reason": response.stop_reason.value,
                    "retry_count": 0,
                    "error": None,
                    "tool_call_count": len(response.tool_calls),
                    "estimated_cost_usd": None,
                },
            )
        except Exception as exc:
            # Selection must stay available even if a vendor does not support strict JSON output.
            # The fallback is deterministic and remains inside the user-approved range.
            reason = (
                "routing model selection failed; used allowed fallback "
                f"({type(exc).__name__})"
            )
            return RuntimeModelSelection(
                target=selector,
                mode="auto",
                reason=reason,
                selector=selector,
                fallback_used=True,
                selector_call={
                    "kind": "selector",
                    "endpoint_id": selector.endpoint_id,
                    "requested_model": selector.model,
                    "returned_model": None,
                    "provider_request_id": None,
                    "usage": TokenUsage().model_dump(mode="json"),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "finish_reason": None,
                    "stop_reason": "provider_error",
                    "retry_count": 0,
                    "error": {"type": type(exc).__name__},
                    "tool_call_count": 0,
                    "estimated_cost_usd": None,
                },
            )


def _parse_selection_json(text: str) -> dict[str, object]:
    candidate = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise ModelChoiceError("selector response must be a JSON object")
    return payload


__all__ = [
    "ModelChoiceError",
    "ModelChoiceService",
    "RuntimeModelSelection",
]
