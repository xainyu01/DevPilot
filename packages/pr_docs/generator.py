"""Generate reviewable Markdown from structured workflow facts."""

from __future__ import annotations

import subprocess
from pathlib import Path

from packages.contracts import (
    PRDocument,
    RepositoryProfile,
    ReviewStatus,
    TestResult,
    TestTaskStatus,
    WorkflowRun,
)


class PRDocumentGenerator:
    """Only quote facts present in the workflow snapshot or repository diff."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def generate(
        self,
        workflow: WorkflowRun,
        *,
        profile: RepositoryProfile | None = None,
        test_results: list[TestResult] | None = None,
        diff_root: Path | None = None,
    ) -> PRDocument:
        changed_files = sorted(set(profile.changed_files if profile else []))
        if diff_root is not None:
            changed_files = _diff_files(diff_root) or changed_files
        tests = test_results if test_results is not None else workflow.test_results
        passed = sum(result.status == TestTaskStatus.PASSED for result in tests)
        failed = sum(result.status not in {TestTaskStatus.PASSED} for result in tests)
        hypothesis = workflow.hypotheses[0] if workflow.hypotheses else None
        title = "Investigate reported repository failure"
        if hypothesis and hypothesis.file_path:
            title = f"Fix reported behavior in {hypothesis.file_path}"
        summary = (
            f"Collected {len(workflow.evidence)} evidence item(s), "
            f"identified {len(workflow.hypotheses)} hypothesis/hypotheses, "
            f"and recorded {passed} passing test task(s)."
        )
        risks = [
            "The root-cause hypothesis still requires reviewer confirmation.",
            "Generated output describes proposed changes; it does not merge or push code.",
        ]
        if failed:
            risks.append("One or more selected test tasks did not pass.")
        checklist = [
            "[ ] Confirm the evidence and suspected location.",
            "[ ] Review the isolated Worktree diff.",
            "[ ] Confirm regression coverage and test output.",
            "[ ] Approve or request changes before merging.",
        ]
        body = _render_body(
            workflow,
            changed_files=changed_files,
            tests=tests,
            risks=risks,
            hypothesis=hypothesis,
        )
        document = PRDocument(
            title=title,
            summary=summary,
            body=body,
            changed_files=changed_files,
            test_result_ids=[result.id for result in tests],
            evidence_ids=[item.id for item in workflow.evidence],
            risks=risks,
            checklist=checklist,
            review_status=ReviewStatus.DRAFT,
        )
        return document.model_copy(update={"body": f"# {title}\n\n{summary}\n\n{body}"})

    def export(self, document: PRDocument, *, filename: str | None = None) -> PRDocument:
        target = self.root / ".devpilot" / "artifacts" / (filename or f"pr-{document.id}.md")
        target = target.resolve()
        target.relative_to((self.root / ".devpilot" / "artifacts").resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document.body, encoding="utf-8")
        return document.model_copy(update={"markdown_path": str(target)})


def _render_body(
    workflow: WorkflowRun,
    *,
    changed_files: list[str],
    tests: list[TestResult],
    risks: list[str],
    hypothesis: object,
) -> str:
    changed_lines = [f"- `{path}`" for path in changed_files] or ["- No diff was recorded."]
    test_lines = [
        f"- `{result.task_id}`: **{result.status.value}**, attempts={result.attempts}, "
        f"exit_code={result.exit_code}"
        for result in tests
    ] or ["- No test task was executed."]
    evidence_lines = [
        f"- `{item.id}`: {item.kind.value} from `{item.source}`"
        for item in workflow.evidence
    ] or ["- No evidence recorded."]
    lines = [
        "## Background",
        "",
        workflow.issue.description,
        "",
        "## Suspected root cause",
        "",
        (
            f"- `{hypothesis.file_path}:{hypothesis.line_start}` — {hypothesis.root_cause}"
            if hypothesis and getattr(hypothesis, "file_path", None)
            else "- No repository location has enough evidence yet."
        ),
        "",
        "## Changed files",
        "",
        *changed_lines,
        "",
        "## Tests",
        "",
        *test_lines,
        "",
        "## Evidence references",
        "",
        *evidence_lines,
        "",
        "## Risks",
        "",
        *(f"- {risk}" for risk in risks),
        "",
        "## Reviewer checklist",
        "",
        "- [ ] Confirm the proposed fix is restricted to the approved Worktree.",
        "- [ ] Confirm no credentials or unrelated files are included.",
        "- [ ] Confirm the test result artifacts are attached.",
    ]
    return "\n".join(lines)


def _diff_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


__all__ = ["PRDocumentGenerator"]
