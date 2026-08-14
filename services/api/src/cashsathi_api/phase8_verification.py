from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

REQUIRED_HEADINGS = (
    "## Release Candidate Freeze",
    "## Repository and Judge Access",
    "## Architecture",
    "## Testing Instructions",
    "## 500\u20131,000-Word Narrative Template",
    "## Screenshot Shot List",
    "## Sub-Three-Minute Demo Script",
    "## Evidence Checklist",
    "## Judging-Period Operations",
    "## Known Limitations",
    "## TODO Official Form Fields",
)
REQUIRED_EVIDENCE_FILES = {
    "manifest.json",
    "scoreboard.json",
    "submission_metrics.json",
    "README.txt",
    "businesses.csv",
    "agent_runs.csv",
    "actions.csv",
    "payments.csv",
    "founder_plans.csv",
    "ledger.csv",
    "pnl_by_month.csv",
    "customer_breakdown.csv",
    "testimonials.csv",
    "identities.csv",
}
EXPECTED_PLACEHOLDERS = {
    "TODO_REPOSITORY_URL",
    "TODO_PUBLIC_DEMO_URL",
    "TODO_VIDEO_URL",
    "TODO_SCREENSHOT_DASHBOARD",
    "TODO_SCREENSHOT_EXTRACTION",
    "TODO_SCREENSHOT_DECISION",
    "TODO_SCREENSHOT_ACTIVITY",
    "TODO_SCREENSHOT_IMPACT",
    "VERIFY_FROM_EVIDENCE_EXPORT",
}
UNRESOLVED_PATTERN = re.compile(r"TODO_[A-Z0-9_]+|VERIFY_FROM_EVIDENCE_EXPORT")
NARRATIVE_PATTERN = re.compile(r"<!-- NARRATIVE_START -->(.*?)<!-- NARRATIVE_END -->", re.DOTALL)
TIMECODE_PATTERN = re.compile(
    r"(?P<start>\d+):(?P<start_seconds>\d{2})[\u2013-](?P<end>\d+):(?P<end_seconds>\d{2})"
)
IMAGE_PATTERN = re.compile(r"!\[[^]]+\]\(([^)]+)\)")


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def _check(name: str, passed: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=passed, detail=detail)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _word_count(value: str) -> int:
    return len(re.findall(r"\b[\w₹-]+\b", value, flags=re.UNICODE))


def check_package(path: Path, *, expect_placeholders: bool) -> list[CheckResult]:
    if not path.is_file():
        return [_check("package_exists", False, f"Missing package: {path}")]
    text = path.read_text(encoding="utf-8")
    missing_headings = [heading for heading in REQUIRED_HEADINGS if heading not in text]
    results = [
        _check(
            "required_sections",
            not missing_headings,
            "All required sections are present."
            if not missing_headings
            else f"Missing: {', '.join(missing_headings)}",
        )
    ]

    narrative_match = NARRATIVE_PATTERN.search(text)
    narrative_words = _word_count(narrative_match.group(1)) if narrative_match else 0
    results.append(
        _check(
            "narrative_length",
            narrative_match is not None and 500 <= narrative_words <= 1000,
            f"Narrative word count: {narrative_words}; required: 500-1000.",
        )
    )

    intervals = [
        (
            int(match["start"]) * 60 + int(match["start_seconds"]),
            int(match["end"]) * 60 + int(match["end_seconds"]),
        )
        for match in TIMECODE_PATTERN.finditer(text)
    ]
    demo_valid = bool(intervals) and min(start for start, _ in intervals) == 0
    demo_valid = demo_valid and max(end for _, end in intervals) < 180
    demo_valid = demo_valid and all(start < end for start, end in intervals)
    results.append(
        _check(
            "demo_duration",
            demo_valid,
            "Demo starts at 0:00 and ends before 3:00."
            if demo_valid
            else "Demo timecodes must start at 0:00 and end before 3:00.",
        )
    )

    evidence_names = {
        "pnl_by_month.csv",
        "customer_breakdown.csv",
        "submission_metrics.json",
        "cashsathi-evidence.zip",
    }
    missing_evidence = sorted(name for name in evidence_names if name not in text)
    results.append(
        _check(
            "evidence_references",
            not missing_evidence,
            "Submission evidence artifacts are referenced."
            if not missing_evidence
            else f"Missing references: {', '.join(missing_evidence)}",
        )
    )
    if expect_placeholders:
        missing_placeholders = sorted(token for token in EXPECTED_PLACEHOLDERS if token not in text)
        results.append(
            _check(
                "truthful_placeholders",
                not missing_placeholders,
                "Expected unverified fields remain explicit."
                if not missing_placeholders
                else f"Missing placeholders: {', '.join(missing_placeholders)}",
            )
        )
    else:
        unresolved = sorted(set(UNRESOLVED_PATTERN.findall(text)))
        results.append(
            _check(
                "resolved_release_fields",
                not unresolved,
                "No unresolved release fields remain."
                if not unresolved
                else f"Unresolved fields: {', '.join(unresolved)}",
            )
        )
        results.extend(_check_release_assets(path, text))
    return results


def _check_release_assets(package_path: Path, text: str) -> list[CheckResult]:
    urls: dict[str, str] = {}
    for label in ("Public repository", "Public demo", "Demo video"):
        match = re.search(rf"^- {re.escape(label)}:\s*`?([^`\s]+)`?\s*$", text, re.MULTILINE)
        if match:
            urls[label] = match.group(1)
    valid_urls = len(urls) == 3 and all(
        value.startswith("https://") and "localhost" not in value for value in urls.values()
    )
    results = [
        _check(
            "public_urls",
            valid_urls,
            "Repository, demo, and video use public HTTPS URLs."
            if valid_urls
            else "Repository, demo, and video must each use a non-local HTTPS URL.",
        )
    ]

    image_targets = IMAGE_PATTERN.findall(text)
    valid_images = 3 <= len(image_targets) <= 5
    missing_local: list[str] = []
    for target in image_targets:
        if target.startswith(("https://", "http://")):
            continue
        resolved = (package_path.parent / target).resolve()
        if not resolved.is_file():
            missing_local.append(target)
    valid_images = valid_images and not missing_local
    results.append(
        _check(
            "screenshots",
            valid_images,
            (
                f"Screenshot count: {len(image_targets)}; "
                f"missing local files: {missing_local or 'none'}."
            ),
        )
    )
    return results


def check_evidence_zip(path: Path) -> tuple[list[CheckResult], str | None]:
    if not path.is_file():
        return [_check("evidence_zip_exists", False, f"Missing evidence ZIP: {path}")], None
    checksum = _sha256(path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            missing = sorted(REQUIRED_EVIDENCE_FILES - names)
            manifest = json.loads(archive.read("manifest.json")) if "manifest.json" in names else {}
            metrics = (
                json.loads(archive.read("submission_metrics.json"))
                if "submission_metrics.json" in names
                else {}
            )
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError) as exc:
        return [_check("evidence_zip_valid", False, f"Evidence ZIP is invalid: {exc}")], checksum
    return [
        _check(
            "evidence_files",
            not missing,
            "All schema-v2 evidence files are present."
            if not missing
            else f"Missing files: {', '.join(missing)}",
        ),
        _check(
            "evidence_manifest",
            manifest.get("schema_version") == 2 and manifest.get("complete") is True,
            "Manifest is complete schema version 2."
            if manifest.get("schema_version") == 2 and manifest.get("complete") is True
            else "Manifest must report schema_version=2 and complete=true.",
        ),
        _check(
            "submission_metrics",
            metrics.get("schema_version") == 1
            and isinstance(metrics.get("claim_boundaries"), list),
            "Submission metrics include versioned claim boundaries."
            if metrics.get("schema_version") == 1
            and isinstance(metrics.get("claim_boundaries"), list)
            else "Submission metrics are missing version or claim boundaries.",
        ),
    ], checksum


def check_clean_git(repository_root: Path) -> tuple[CheckResult, str | None]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    commit_sha = commit.stdout.strip() if commit.returncode == 0 else None
    clean = commit.returncode == 0 and status.returncode == 0 and not status.stdout.strip()
    return (
        _check(
            "clean_git_commit",
            clean,
            f"Clean commit: {commit_sha}." if clean else "Commit all intended changes first.",
        ),
        commit_sha,
    )


def write_release_manifest(
    output_dir: Path,
    *,
    checks: list[CheckResult],
    commit_sha: str | None,
    package_path: Path,
    evidence_path: Path | None,
    evidence_sha256: str | None,
    quality_gates_passed: bool,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "phase8-release-manifest.json"
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "ready": all(check.passed for check in checks),
        "git_commit": commit_sha,
        "quality_gates": {
            "passed": quality_gates_passed,
            "commands": ["npm run lint", "npm run typecheck", "npm test", "npm run build"],
        },
        "artifacts": {
            "submission_package": {
                "path": str(package_path),
                "sha256": _sha256(package_path) if package_path.is_file() else None,
            },
            "evidence_zip": {
                "path": str(evidence_path) if evidence_path else None,
                "sha256": evidence_sha256,
            },
        },
        "checks": [asdict(check) for check in checks],
    }
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return output_path


def repository_root_from_module() -> Path:
    return Path(__file__).resolve().parents[4]


def main(argv: list[str] | None = None) -> int:
    root_default = repository_root_from_module()
    parser = argparse.ArgumentParser(description="Verify the Phase 8 repository package.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--structure-only", action="store_true")
    mode.add_argument("--release", action="store_true")
    parser.add_argument("--repository-root", type=Path, default=root_default)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--evidence-zip", type=Path)
    parser.add_argument("--quality-gates-passed", action="store_true")
    args = parser.parse_args(argv)

    repository_root = args.repository_root.resolve()
    package_path = (
        args.package or repository_root / "docs/submission/phase-8-package.md"
    ).resolve()
    checks = check_package(package_path, expect_placeholders=args.structure_only)
    evidence_sha256: str | None = None
    commit_sha: str | None = None

    if args.release:
        checks.append(
            _check(
                "quality_gates",
                args.quality_gates_passed,
                "Lint, typecheck, tests, and build passed in this command chain."
                if args.quality_gates_passed
                else "Run release verification through npm run verify:phase8.",
            )
        )
        evidence_path = args.evidence_zip.resolve() if args.evidence_zip else Path("missing")
        evidence_checks, evidence_sha256 = check_evidence_zip(evidence_path)
        checks.extend(evidence_checks)
        git_check, commit_sha = check_clean_git(repository_root)
        checks.append(git_check)
        manifest_path = write_release_manifest(
            repository_root / "release-artifacts",
            checks=checks,
            commit_sha=commit_sha,
            package_path=package_path,
            evidence_path=args.evidence_zip.resolve() if args.evidence_zip else None,
            evidence_sha256=evidence_sha256,
            quality_gates_passed=args.quality_gates_passed,
        )
        print(f"Release manifest: {manifest_path}")

    for check in checks:
        marker = "PASS" if check.passed else "FAIL"
        print(f"[{marker}] {check.name}: {check.detail}")
    return 0 if all(check.passed for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
