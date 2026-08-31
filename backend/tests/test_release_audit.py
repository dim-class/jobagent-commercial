"""The distributable must be complete and contain no user/runtime residue."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release_audit.py"
SPEC = importlib.util.spec_from_file_location("release_audit", SCRIPT)
assert SPEC and SPEC.loader
release_audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_audit)


def complete_bundle(root: Path) -> None:
    for relative in release_audit.REQUIRED:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")


def test_complete_neutral_bundle_passes_and_has_stable_file_manifest(tmp_path):
    complete_bundle(tmp_path)
    result = release_audit.audit_bundle(tmp_path)
    assert result["ok"] is True
    assert result["missing"] == []
    assert result["forbidden"] == []
    assert result["file_count"] == len(release_audit.REQUIRED)
    assert [item["path"] for item in result["files"]] == sorted(release_audit.REQUIRED)


def test_personal_database_env_log_and_browser_profile_fail_closed(tmp_path):
    complete_bundle(tmp_path)
    for relative in (
        "jobagent.db", ".env", "runtime/app.log", "browser_profiles/cookies.txt",
        "runtime/jobagent_backend.dist-info/direct_url.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"private")
    result = release_audit.audit_bundle(tmp_path)
    assert result["ok"] is False
    assert set(result["forbidden"]) == {
        ".env", "browser_profiles/", "jobagent.db", "runtime/app.log",
        "runtime/jobagent_backend.dist-info/direct_url.json",
    }


def test_missing_executable_or_extension_fails_closed(tmp_path):
    complete_bundle(tmp_path)
    (tmp_path / "JobAgent.exe").unlink()
    (tmp_path / "extension/dist/background.js").unlink()
    result = release_audit.audit_bundle(tmp_path)
    assert result["ok"] is False
    assert result["missing"] == ["JobAgent.exe", "extension/dist/background.js"]
