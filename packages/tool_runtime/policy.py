"""Workspace boundary and capability policy checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from packages.contracts import (
    ApprovalRequest,
    PolicyDecision,
    ToolCall,
    ToolDefinition,
    ToolRisk,
)

from .approvals import ApprovalStore
from .context import ToolExecutionContext


def argument_fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        {"tool": tool_name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def command_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    command = arguments.get("command")
    if isinstance(command, list) and command and all(isinstance(item, str) for item in command):
        return " ".join(command)
    operation = arguments.get("operation")
    if isinstance(operation, str):
        return f"{tool_name} {operation}"
    return tool_name


def redact_arguments(value: Any, *, key: str = "") -> Any:
    """Remove obvious secret-shaped values from audit and approval payloads."""
    sensitive = ("key", "token", "secret", "password", "credential", "authorization")
    if any(word in key.lower() for word in sensitive):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(name): redact_arguments(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [redact_arguments(item, key=key) for item in value]
    return value


class PolicyEngine:
    """Default-deny policy for capabilities, paths and side effects."""

    def validate_path(
        self,
        context: ToolExecutionContext,
        candidate: str | Path,
        *,
        allow_missing: bool = True,
    ) -> Path:
        root = context.workspace_root.expanduser().resolve()
        path = Path(candidate)
        resolved = (root / path if not path.is_absolute() else path).expanduser().resolve(
            strict=False
        )
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("path is outside the configured workspace") from exc
        if not allow_missing and not resolved.exists():
            raise FileNotFoundError(str(resolved))
        return resolved

    def evaluate(
        self,
        definition: ToolDefinition,
        call: ToolCall,
        context: ToolExecutionContext,
        *,
        approval_store: ApprovalStore | None = None,
        risk: ToolRisk | None = None,
        required_capabilities: list[str] | None = None,
    ) -> PolicyDecision:
        selected_risk = risk or definition.risk
        required = required_capabilities or definition.required_capabilities
        for key in ("path", "file_path", "cwd", "worktree_path"):
            candidate = call.arguments.get(key)
            if candidate is None:
                continue
            candidates = candidate if isinstance(candidate, list) else [candidate]
            for item in candidates:
                if not isinstance(item, (str, Path)):
                    return PolicyDecision(
                        reason=f"{key} must be a path inside the workspace",
                        risk=selected_risk,
                    )
                try:
                    self.validate_path(context, item)
                except (ValueError, FileNotFoundError) as exc:
                    return PolicyDecision(reason=str(exc), risk=selected_risk)
        missing = sorted(set(required) - context.capabilities)
        if missing:
            return PolicyDecision(
                reason=f"missing capabilities: {', '.join(missing)}",
                risk=selected_risk,
            )

        if selected_risk in {ToolRisk.HIGH_RISK, ToolRisk.EXTERNAL_SIDE_EFFECT}:
            if approval_store is None:
                return PolicyDecision(
                    reason="human approval is required before this tool can execute",
                    risk=selected_risk,
                    requires_approval=True,
                )
            fingerprint = argument_fingerprint(call.name, call.arguments)
            approved = approval_store.find_approved(
                call,
                session_id=context.session_id,
                fingerprint=fingerprint,
                command_signature=command_signature(call.name, call.arguments),
            )
            if approved is not None:
                return PolicyDecision(
                    allowed=True,
                    reason="matching human approval found",
                    risk=selected_risk,
                    approval_request_id=approved.request_id,
                    approval_scope=approved.scope,
                )
            return PolicyDecision(
                reason="human approval is required before this tool can execute",
                risk=selected_risk,
                requires_approval=True,
            )

        return PolicyDecision(
            allowed=True,
            reason="policy allows this operation",
            risk=selected_risk,
        )

    def make_approval_request(
        self,
        definition: ToolDefinition,
        call: ToolCall,
        context: ToolExecutionContext,
        *,
        risk: ToolRisk | None = None,
        required_capabilities: list[str] | None = None,
        request_id: str | None = None,
    ) -> ApprovalRequest:
        selected_risk = risk or definition.risk
        return ApprovalRequest(
            request_id=request_id or str(uuid4()),
            call_id=call.call_id,
            tool_name=call.name,
            risk=selected_risk,
            arguments=redact_arguments(call.arguments),
            required_capabilities=required_capabilities or definition.required_capabilities,
            reason=(
                f"Tool {call.name} is classified as {selected_risk.value} and may change state"
            ),
            session_id=context.session_id,
            fingerprint=argument_fingerprint(call.name, call.arguments),
        )
