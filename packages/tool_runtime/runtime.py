"""Execution coordinator that applies policy, approval and audit uniformly."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from threading import Lock, RLock
from typing import Any

from packages.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    AuditRecord,
    ToolCall,
    ToolResult,
    ToolRisk,
)

from .approvals import ApprovalStore
from .audit import AuditLog
from .context import ToolExecutionContext
from .errors import ToolCommandError
from .policy import PolicyEngine
from .registry import ToolRegistry


class ToolRuntime:
    """Run only registered tools after capability and approval checks."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        registry: ToolRegistry | None = None,
        policy: PolicyEngine | None = None,
        approvals: ApprovalStore | None = None,
        audit_log: AuditLog | None = None,
    ) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()
        if registry is None:
            from .tools import create_default_registry

            registry = create_default_registry(self.workspace_root)
        self.registry = registry
        self.policy = policy or PolicyEngine()
        self.approvals = approvals or ApprovalStore()
        self.audit_log = audit_log or AuditLog()

    def default_context(
        self,
        *,
        actor_id: str = "agent",
        session_id: str = "local-session",
        run_id: str | None = None,
        capabilities: set[str] | None = None,
    ) -> ToolExecutionContext:
        return ToolExecutionContext(
            workspace_root=self.workspace_root,
            actor_id=actor_id,
            session_id=session_id,
            run_id=run_id,
            capabilities=capabilities or {"workspace.read"},
        )

    def workspace_status(self) -> dict[str, list[str]]:
        """Return changes relative to this runtime's protected baseline."""
        tool = self.registry.get("workspace.status")
        tracker = getattr(tool, "tracker", None)
        if tracker is None:
            return {"added": [], "modified": [], "deleted": []}
        return tracker.status()

    def workspace_diff(self) -> str:
        """Return a bounded textual diff relative to this runtime's baseline."""
        tool = self.registry.get("file.diff")
        tracker = getattr(tool, "tracker", None)
        return tracker.diff() if tracker is not None else ""

    async def execute(
        self,
        call: ToolCall,
        *,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        tool = self.registry.get(call.name)
        current = context or self.default_context()
        definition = tool.definition
        risk = tool.risk_level(call.arguments)
        required = tool.required_capabilities(call.arguments)
        decision = self.policy.evaluate(
            definition,
            call,
            current,
            approval_store=self.approvals,
            risk=risk,
            required_capabilities=required,
        )
        denied_approval = self.approvals.find_denied(call, session_id=current.session_id)
        if denied_approval is not None:
            reason = "human approval was denied for this tool call"
            self._audit(
                current,
                event_type="policy.denied",
                tool_name=call.name,
                risk=risk,
                outcome="approval_denied",
                detail={"request_id": denied_approval.request_id},
            )
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status="denied",
                error={"code": "approval_denied", "message": reason},
            )
        if not decision.allowed and not decision.requires_approval:
            self._audit(
                current,
                event_type="policy.denied",
                tool_name=call.name,
                risk=risk,
                outcome="denied",
                detail={"reason": decision.reason},
            )
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status="denied",
                error={"code": "policy_denied", "message": decision.reason},
            )

        if decision.requires_approval:
            existing = next(
                (
                    request
                    for request in self.approvals.pending(session_id=current.session_id)
                    if request.call_id == call.call_id
                ),
                None,
            )
            approval = existing or self.policy.make_approval_request(
                definition,
                call,
                current,
                risk=risk,
                required_capabilities=required,
            )
            if existing is None:
                self.approvals.create(approval)
            self._audit(
                current,
                event_type="approval.requested",
                tool_name=call.name,
                risk=risk,
                outcome="pending",
                detail={"request_id": approval.request_id},
            )
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status="pending_approval",
                approval_request=approval,
                error={
                    "code": "approval_required",
                    "message": "human approval is required before execution",
                },
            )

        started = time.perf_counter()
        self._audit(
            current,
            event_type="policy.allowed",
            tool_name=call.name,
            risk=risk,
            outcome="allowed",
            detail={"reason": decision.reason},
        )
        self._audit(
            current,
            event_type="tool.started",
            tool_name=call.name,
            risk=risk,
            outcome="started",
            detail={"call_id": call.call_id},
        )
        write_lease = None
        if risk != ToolRisk.READ_ONLY:
            with self._lease_guard:
                write_lease = self._write_leases.setdefault(
                    str(current.workspace_root.resolve()).casefold(),
                    Lock(),
                )
            if not write_lease.acquire(blocking=False):
                return ToolResult(
                    call_id=call.call_id,
                    tool_name=call.name,
                    status="failed",
                    error={
                        "code": "workspace_write_locked",
                        "message": "another writer currently owns this workspace",
                    },
                )
        try:
            output = await asyncio.wait_for(
                tool.execute(call.arguments, current), timeout=definition.timeout_seconds
            )
            text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
            if len(text) > definition.max_output_chars:
                text = text[: definition.max_output_chars] + "\n[output truncated]"
            elapsed = int((time.perf_counter() - started) * 1000)
            approval_id = decision.approval_request_id
            if approval_id:
                self.approvals.consume(approval_id)
            self._audit(
                current,
                event_type="tool.completed",
                tool_name=call.name,
                risk=risk,
                outcome="succeeded",
                detail={"call_id": call.call_id, "duration_ms": elapsed},
            )
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status="succeeded",
                output=text,
                duration_ms=elapsed,
            )
        except TimeoutError:
            elapsed = int((time.perf_counter() - started) * 1000)
            message = f"tool timed out after {definition.timeout_seconds:g}s"
            self._audit(
                current,
                event_type="tool.failed",
                tool_name=call.name,
                risk=risk,
                outcome="timeout",
                detail={"call_id": call.call_id, "duration_ms": elapsed},
            )
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status="failed",
                error={"code": "tool_timeout", "message": message},
                duration_ms=elapsed,
            )
        except ToolCommandError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            self._audit(
                current,
                event_type="tool.failed",
                tool_name=call.name,
                risk=risk,
                outcome=exc.code,
                detail={"call_id": call.call_id, "duration_ms": elapsed},
            )
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status="failed",
                output=exc.output,
                error={"code": exc.code, "message": str(exc)},
                duration_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            self._audit(
                current,
                event_type="tool.failed",
                tool_name=call.name,
                risk=risk,
                outcome="failed",
                detail={"call_id": call.call_id, "message": str(exc)},
            )
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                status="failed",
                error={"code": "tool_execution_error", "message": str(exc)},
                duration_ms=elapsed,
            )
        finally:
            if write_lease is not None:
                write_lease.release()

    def decide_approval(
        self,
        decision: ApprovalDecision,
        *,
        actor_id: str = "user",
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> ApprovalRequest:
        request = self.approvals.decide(decision)
        self._audit(
            self.default_context(
                actor_id=actor_id,
                session_id=session_id or request.session_id,
                run_id=run_id,
            ),
            event_type="approval.decided",
            tool_name=request.tool_name,
            risk=request.risk,
            outcome=request.status,
            detail={"request_id": request.request_id, "scope": decision.scope.value},
        )
        return request

    def _audit(
        self,
        context: ToolExecutionContext,
        *,
        event_type: Any,
        tool_name: str,
        risk: Any,
        outcome: str,
        detail: dict[str, Any],
    ) -> None:
        self.audit_log.record(
            AuditRecord(
                event_type=event_type,
                actor_id=context.actor_id,
                session_id=context.session_id,
                run_id=context.run_id,
                tool_name=tool_name,
                risk=risk,
                outcome=outcome,
                detail=detail,
            )
        )
    _lease_guard = RLock()
    _write_leases: dict[str, Lock] = {}
