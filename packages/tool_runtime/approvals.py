"""Short-lived approval state for high-risk tool calls."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from threading import RLock

from packages.contracts import ApprovalDecision, ApprovalRequest, ApprovalScope, ToolCall


class ApprovalStore:
    """In-memory approval store with once/session/command scope.

    The store deliberately has no permanent grant operation.  A later
    persistence package can implement the same interface with expiring rows.
    """

    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = RLock()

    def create(self, request: ApprovalRequest) -> ApprovalRequest:
        with self._lock:
            self._requests[request.request_id] = request
        return deepcopy(request)

    def get(self, request_id: str) -> ApprovalRequest | None:
        with self._lock:
            request = self._requests.get(request_id)
            return deepcopy(request) if request is not None else None

    def pending(self, *, session_id: str | None = None) -> list[ApprovalRequest]:
        with self._lock:
            return [
                deepcopy(request)
                for request in self._requests.values()
                if request.status == "pending"
                and (session_id is None or request.session_id == session_id)
            ]

    def find_denied(self, call: ToolCall, *, session_id: str) -> ApprovalRequest | None:
        with self._lock:
            for request in self._requests.values():
                if (
                    request.status == "denied"
                    and request.call_id == call.call_id
                    and request.tool_name == call.name
                    and request.session_id == session_id
                ):
                    return deepcopy(request)
        return None

    def decide(self, decision: ApprovalDecision) -> ApprovalRequest:
        with self._lock:
            request = self._requests.get(decision.request_id)
            if request is None:
                raise KeyError(f"Unknown approval request: {decision.request_id}")
            if request.status in {"approved", "denied", "consumed"}:
                return deepcopy(request)
            status = "approved" if decision.approved else "denied"
            updated = request.model_copy(
                update={
                    "status": status,
                    "scope": decision.scope if decision.approved else None,
                    "command_pattern": decision.command_pattern if decision.approved else None,
                    "decided_by": decision.decided_by,
                    "decided_at": datetime.now(UTC),
                }
            )
            self._requests[decision.request_id] = updated
            return deepcopy(updated)

    def find_approved(
        self,
        call: ToolCall,
        *,
        session_id: str,
        fingerprint: str,
        command_signature: str,
    ) -> ApprovalRequest | None:
        with self._lock:
            candidates = [
                request
                for request in self._requests.values()
                if request.status == "approved"
                and request.tool_name == call.name
                and request.session_id == session_id
                and (
                    (request.scope == ApprovalScope.ONCE and request.call_id == call.call_id)
                    or (
                        request.scope == ApprovalScope.SESSION
                        and request.fingerprint == fingerprint
                    )
                    or (
                        request.scope == ApprovalScope.COMMAND
                        and request.command_pattern is not None
                        and fnmatchcase(command_signature, request.command_pattern)
                    )
                )
            ]
            if not candidates:
                return None
            return deepcopy(max(candidates, key=lambda item: item.created_at))

    def consume(self, request_id: str) -> None:
        with self._lock:
            request = self._requests.get(request_id)
            if request is not None and request.scope == ApprovalScope.ONCE:
                self._requests[request_id] = request.model_copy(update={"status": "consumed"})
