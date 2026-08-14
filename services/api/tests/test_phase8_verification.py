from __future__ import annotations

import json
import zipfile
from pathlib import Path

from cashsathi_api.phase8_verification import (
    REQUIRED_EVIDENCE_FILES,
    CheckResult,
    check_evidence_zip,
    check_package,
    repository_root_from_module,
    write_release_manifest,
)


def failures(checks: list[CheckResult]) -> set[str]:
    return {check.name for check in checks if not check.passed}


def test_repository_phase8_package_passes_structure_validation() -> None:
    package = repository_root_from_module() / "docs/submission/phase-8-package.md"
    assert failures(check_package(package, expect_placeholders=True)) == set()


def test_release_validation_rejects_placeholders_and_missing_assets() -> None:
    package = repository_root_from_module() / "docs/submission/phase-8-package.md"
    failed = failures(check_package(package, expect_placeholders=False))
    assert {"resolved_release_fields", "public_urls", "screenshots"} <= failed


def test_structure_validation_rejects_short_narrative(tmp_path: Path) -> None:
    source = repository_root_from_module() / "docs/submission/phase-8-package.md"
    text = source.read_text(encoding="utf-8")
    start = text.index("<!-- NARRATIVE_START -->")
    end = text.index("<!-- NARRATIVE_END -->")
    invalid = text[:start] + "<!-- NARRATIVE_START -->Too short.\n" + text[end:]
    package = tmp_path / "phase-8-package.md"
    package.write_text(invalid, encoding="utf-8")
    assert "narrative_length" in failures(check_package(package, expect_placeholders=True))


def test_evidence_validation_accepts_complete_schema_v2_archive(tmp_path: Path) -> None:
    evidence = tmp_path / "cashsathi-evidence.zip"
    with zipfile.ZipFile(evidence, "w") as archive:
        for name in REQUIRED_EVIDENCE_FILES:
            if name == "manifest.json":
                content = json.dumps({"schema_version": 2, "complete": True})
            elif name == "submission_metrics.json":
                content = json.dumps({"schema_version": 1, "claim_boundaries": []})
            else:
                content = ""
            archive.writestr(name, content)
    checks, checksum = check_evidence_zip(evidence)
    assert failures(checks) == set()
    assert checksum is not None and len(checksum) == 64


def test_evidence_validation_rejects_incomplete_archive(tmp_path: Path) -> None:
    evidence = tmp_path / "cashsathi-evidence.zip"
    with zipfile.ZipFile(evidence, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"schema_version": 1, "complete": False}))
    checks, _ = check_evidence_zip(evidence)
    assert {"evidence_files", "evidence_manifest", "submission_metrics"} <= failures(checks)


def test_evidence_validation_rejects_missing_archive(tmp_path: Path) -> None:
    checks, checksum = check_evidence_zip(tmp_path / "missing.zip")
    assert failures(checks) == {"evidence_zip_exists"}
    assert checksum is None


def test_release_manifest_records_readiness_and_checksums(tmp_path: Path) -> None:
    package = tmp_path / "package.md"
    evidence = tmp_path / "evidence.zip"
    package.write_text("package", encoding="utf-8")
    evidence.write_bytes(b"evidence")
    output = write_release_manifest(
        tmp_path / "output",
        checks=[CheckResult("example", True, "passed")],
        commit_sha="a" * 40,
        package_path=package,
        evidence_path=evidence,
        evidence_sha256="b" * 64,
        quality_gates_passed=True,
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["ready"] is True
    assert manifest["git_commit"] == "a" * 40
    assert manifest["quality_gates"]["passed"] is True
    assert len(manifest["artifacts"]["submission_package"]["sha256"]) == 64
