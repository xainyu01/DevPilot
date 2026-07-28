"""Evidence-first bug location for issue, log and failed-test inputs."""

from __future__ import annotations

import json
import re
from pathlib import Path

from packages.contracts import (
    BugHypothesis,
    EvidenceItem,
    EvidenceKind,
    HypothesisStatus,
    IssueContext,
    RepositoryProfile,
)


class BugLocator:
    """Build a traceable hypothesis without pretending that a guess is proof."""

    def __init__(self, root: Path, *, max_matches: int = 24) -> None:
        self.root = root.expanduser().resolve()
        self.max_matches = max_matches

    def locate(
        self,
        issue: IssueContext,
        profile: RepositoryProfile,
    ) -> tuple[list[EvidenceItem], list[BugHypothesis]]:
        evidence: list[EvidenceItem] = [
            EvidenceItem(
                kind=EvidenceKind.ISSUE,
                source="issue.description",
                content=issue.description,
            )
        ]
        if issue.logs.strip():
            evidence.append(
                EvidenceItem(kind=EvidenceKind.LOG, source="issue.logs", content=issue.logs)
            )
        for failing_test in issue.failing_tests:
            evidence.append(
                EvidenceItem(
                    kind=EvidenceKind.FAILED_TEST,
                    source="issue.failing_tests",
                    locator=failing_test,
                    content=failing_test,
                )
            )
        evidence.append(
            EvidenceItem(
                kind=EvidenceKind.REPOSITORY,
                source="repository.profile",
                locator=profile.root_path,
                content=json.dumps(
                    {
                        "languages": profile.languages,
                        "frameworks": profile.frameworks,
                        "commands": profile.commands,
                        "index_version": profile.index_version,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        )
        trace_candidates = self._trace_candidates(issue, profile)
        for path, line, excerpt in trace_candidates:
            evidence.append(
                EvidenceItem(
                    kind=EvidenceKind.TRACEBACK,
                    source="issue.logs",
                    locator=path,
                    content=excerpt,
                    line_start=line,
                    line_end=line,
                    confidence=0.95,
                )
            )
        terms = _keywords(" ".join([issue.description, issue.logs, *issue.failing_tests]))
        matches = self._search(profile, terms)
        evidence.extend(
            EvidenceItem(
                kind=EvidenceKind.SEARCH,
                source="repository.search",
                locator=f"{path}:{line}",
                content=excerpt,
                line_start=line,
                line_end=line,
                confidence=0.65,
            )
            for path, line, excerpt in matches
        )
        target = trace_candidates[0] if trace_candidates else (matches[0] if matches else None)
        if target is None:
            hypothesis = BugHypothesis(
                root_cause="No repository location matched the supplied issue evidence.",
                confidence=0.2,
                status=HypothesisStatus.UNRESOLVED,
                verification_steps=[
                    "Add a reproducible failing test or a traceback with a file path."
                ],
                evidence_ids=[item.id for item in evidence],
                unresolved_questions=["Which repository symbol produces the reported behavior?"],
            )
            return evidence, [hypothesis]
        path, line, _ = target
        symbol = _symbol_at(profile, path, line)
        confidence = 0.95 if trace_candidates else 0.65
        hypothesis = BugHypothesis(
            root_cause=(
                f"The reported behavior is most strongly associated with "
                f"{path}" + (f"::{symbol}" if symbol else "") + "."
            ),
            confidence=confidence,
            status=HypothesisStatus.OPEN,
            file_path=path,
            line_start=line,
            symbol=symbol,
            verification_steps=[
                f"Reproduce the failure against {path}:{line}.",
                "Run the selected regression test and inspect its captured output.",
            ],
            evidence_ids=[item.id for item in evidence],
            unresolved_questions=["Does the proposed change preserve the surrounding behavior?"],
        )
        return evidence, [hypothesis]

    def _trace_candidates(
        self,
        issue: IssueContext,
        profile: RepositoryProfile,
    ) -> list[tuple[str, int, str]]:
        text = issue.logs + "\n" + issue.description
        found: list[tuple[str, int, str]] = []
        for match in re.finditer(r'File ["\'](.+?)["\'], line (\d+)', text):
            raw_path, raw_line = match.groups()
            path = _safe_relative(self.root, raw_path)
            if path in {item.path for item in profile.files}:
                line = int(raw_line)
                found.append((path, line, f"traceback points to {path}:{line}"))
        return found

    def _search(
        self,
        profile: RepositoryProfile,
        terms: set[str],
    ) -> list[tuple[str, int, str]]:
        if not terms:
            return []
        matches: list[tuple[str, int, str, int]] = []
        for item in profile.files:
            if item.language == "unknown" or item.size > 256_000:
                continue
            path = self.root / item.path
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, start=1):
                score = sum(term in line.lower() for term in terms)
                if score:
                    matches.append((item.path, number, line.strip()[:500], score))
        matches.sort(key=lambda item: (-item[3], item[0], item[1]))
        return [(path, line, excerpt) for path, line, excerpt, _ in matches[: self.max_matches]]


def _keywords(text: str) -> set[str]:
    stop_words = {
        "this",
        "that",
        "with",
        "from",
        "when",
        "then",
        "should",
        "does",
        "have",
        "expected",
        "actual",
        "error",
        "failed",
    }
    return {
        token
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text.lower())
        if token not in stop_words
    }


def _safe_relative(root: Path, raw_path: str) -> str:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve().relative_to(root).as_posix()
    except ValueError:
        return ""


def _symbol_at(profile: RepositoryProfile, path: str, line: int) -> str | None:
    symbols = profile.symbols.get(path, [])
    if not symbols:
        return None
    return symbols[0] if line >= 1 else None


__all__ = ["BugLocator"]
