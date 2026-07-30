"""Persistence-backed adapters for the ToolRuntime state ports."""

from __future__ import annotations

from packages.contracts import ApprovalDecision, ApprovalRequest
from packages.tool_runtime.approvals import ApprovalStore

from .repositories import ApprovalRepository


class PersistentApprovalStore(ApprovalStore):
    """Keep policy matching in the domain store while persisting every transition."""

    def __init__(
        self,
        repository: ApprovalRepository,
        *,
        session_id: str,
    ) -> None:
        super().__init__()
        self.repository = repository
        for request in repository.list(session_id=session_id):
            self._requests[request.request_id] = request

    def create(self, request: ApprovalRequest) -> ApprovalRequest:
        created = super().create(request)
        self.repository.save(created)
        return created

    def decide(self, decision: ApprovalDecision) -> ApprovalRequest:
        decided = super().decide(decision)
        self.repository.save_decision(decided, decision)
        return decided

    def consume(self, request_id: str) -> None:
        super().consume(request_id)
        request = self.get(request_id)
        if request is not None:
            self.repository.save(request)


__all__ = ["PersistentApprovalStore"]
