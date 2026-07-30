"""Server-side evidence verification independent from model self-reporting."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts import ToolResult
from packages.tool_runtime import ToolRuntime


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    satisfied: bool
    outcome: Literal["completed", "retry", "partial", "failed"]
    issues: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str


class CompletionVerifier:
    """Accept completion only when runtime evidence satisfies server criteria."""

    def verify(
        self,
        *,
        task_text: str,
        final_text: str,
        acceptance_criteria: list[str],
        tool_results: list[ToolResult],
        tool_runtime: ToolRuntime | None,
    ) -> VerificationReport:
        workspace_status = (
            tool_runtime.workspace_status()
            if tool_runtime is not None
            else {"added": [], "modified": [], "deleted": []}
        )
        changed = [
            *workspace_status["added"],
            *workspace_status["modified"],
            *workspace_status["deleted"],
        ]
        successful_tools = [
            result.tool_name for result in tool_results if result.status == "succeeded"
        ]
        issues: list[str] = []
        evidence: dict[str, Any] = {
            "workspace": workspace_status,
            "successful_tools": successful_tools,
            "failed_tools": [
                result.tool_name for result in tool_results if result.status != "succeeded"
            ],
            "acceptance_criteria": acceptance_criteria,
        }

        coding_required = bool(acceptance_criteria) and tool_runtime is not None
        if coding_required and not changed:
            issues.append("no workspace change was produced for a coding task")

        unhandled = _unhandled_errors(tool_results)
        if unhandled:
            issues.append(
                "unhandled tool errors remain: "
                + ", ".join(f"{result.tool_name}/{result.status}" for result in unhandled)
            )

        # Only the user's task and server-owned criteria may make tests mandatory.
        # Model output is untrusted evidence: mentioning tests cannot invent a
        # requirement or satisfy one.
        combined = "\n".join([task_text, *acceptance_criteria]).lower()
        tests_required = bool(
            re.search(r"\b(test|tests|tested|pytest|unittest)\b|测试|验收", combined)
        )
        successful_tests = [
            result
            for result in tool_results
            if result.tool_name == "test.run" and result.status == "succeeded"
        ]
        evidence["tests_required"] = tests_required
        evidence["successful_test_runs"] = len(successful_tests)
        if tests_required and not successful_tests:
            issues.append("required tests have no successful test.run evidence")

        missing_paths = _missing_required_paths(
            acceptance_criteria,
            tool_runtime.workspace_root if tool_runtime is not None else None,
        )
        evidence["missing_required_paths"] = missing_paths
        if missing_paths:
            issues.append("required paths are missing: " + ", ".join(missing_paths))

        if not final_text.strip():
            issues.append("model did not provide a final task report")

        signature_value = {
            "issues": issues,
            "workspace": workspace_status,
            "successful_tools": successful_tools,
            "test_runs": len(successful_tests),
        }
        fingerprint = hashlib.sha256(
            json.dumps(signature_value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return VerificationReport(
            satisfied=not issues,
            outcome="completed" if not issues else "retry",
            issues=issues,
            evidence=evidence,
            fingerprint=fingerprint,
        )


def _unhandled_errors(results: list[ToolResult]) -> list[ToolResult]:
    unhandled: list[ToolResult] = []
    for index, result in enumerate(results):
        if result.status == "succeeded":
            continue
        recovered = any(
            later.tool_name == result.tool_name and later.status == "succeeded"
            for later in results[index + 1 :]
        )
        if not recovered:
            unhandled.append(result)
    return unhandled


def _missing_required_paths(criteria: list[str], root: Path | None) -> list[str]:
    if root is None:
        return []
    candidates: set[str] = set()
    for criterion in criteria:
        for value in re.findall(r"`([^`]+)`", criterion):
            normalized = value.strip().replace("\\", "/")
            if (
                normalized not in {".", ".."}
                and not normalized.startswith(("/", "~"))
                and ("/" in normalized or "." in Path(normalized).name)
                and " " not in normalized
            ):
                candidates.add(normalized)
    return sorted(
        candidate
        for candidate in candidates
        if not (root / Path(candidate)).resolve().is_relative_to(root)
        or not (root / Path(candidate)).exists()
    )


__all__ = ["CompletionVerifier", "VerificationReport"]
