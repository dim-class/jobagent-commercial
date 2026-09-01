from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER = REPO_ROOT / "scripts" / "build-extension-store.py"


def _build(output: Path) -> tuple[Path, dict[str, object]]:
    subprocess.run(
        [sys.executable, str(BUILDER), "--repo", str(REPO_ROOT), "--output", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads((output / "EXTENSION-STORE-MANIFEST.json").read_text(encoding="utf-8"))
    return output / str(metadata["package"]), metadata


def _png_size(payload: bytes) -> tuple[int, int]:
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", payload[16:24])


@pytest.fixture
def store_root():
    base = REPO_ROOT / ".tmp"
    base.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="extension-store-", dir=base))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_store_package_is_deterministic_and_matches_integrity_metadata(store_root: Path):
    first, first_meta = _build(store_root / "first")
    second, second_meta = _build(store_root / "second")
    first_bytes = first.read_bytes()
    second_bytes = second.read_bytes()

    assert first_bytes == second_bytes
    assert first_meta == second_meta
    assert hashlib.sha256(first_bytes).hexdigest() == first_meta["sha256"]
    assert first_meta["distribution_tier"] == "store_submission_candidate"
    assert first_meta["store_published"] is False
    assert first_meta["privacy_review_required"] is True


def test_store_manifest_has_minimum_expected_permissions_and_no_dev_port(store_root: Path):
    package, metadata = _build(store_root / "package")
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))

    assert manifest["manifest_version"] == 3
    assert manifest["permissions"] == ["activeTab", "scripting", "storage"]
    assert manifest["host_permissions"] == [
        "http://127.0.0.1:8000/*",
        "http://localhost:8000/*",
        "https://www.zhipin.com/*",
    ]
    assert "5173" not in json.dumps(manifest)
    assert metadata["permissions"] == manifest["permissions"]
    assert metadata["host_permissions"] == manifest["host_permissions"]


def test_store_archive_is_an_exact_runtime_allow_list(store_root: Path):
    package, metadata = _build(store_root / "package")
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        assert names == sorted(metadata["files"])
        assert names[0].startswith("dist/")
        assert "manifest.json" in names
        assert all(not name.startswith(("src/", "tests/", "node_modules/", "assets/")) for name in names)
        assert all(not name.endswith((".ts", ".map", "package.json", "package-lock.json")) for name in names)
        for size in (16, 32, 48, 128):
            assert _png_size(archive.read(f"icons/icon{size}.png")) == (size, size)


def test_store_archive_excludes_remote_code_and_suspended_chat_scanner(store_root: Path):
    package, _ = _build(store_root / "package")
    with zipfile.ZipFile(package) as archive:
        runtime = "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith((".js", ".html"))
        )

    for marker in (
        "eval(",
        "new Function",
        "chrome.debugger",
        "<all_urls>",
        "jobagent:m7-scan-current-chat",
        "scanCurrentBossChat",
        "scanCurrentBossConversation",
        "/api/recruiter-conversations/boss-current-scan",
    ):
        assert marker not in runtime


def test_store_workflow_is_artifact_only_and_has_no_publish_credentials():
    workflow = (REPO_ROOT / ".github" / "workflows" / "chrome-web-store-candidate.yml").read_text(
        encoding="utf-8"
    )
    assert "build-extension-store.py" in workflow
    assert "actions/upload-artifact" in workflow
    assert "chrome-webstore-upload" not in workflow
    assert "client_secret" not in workflow.lower()
    assert "refresh_token" not in workflow.lower()
    assert "publish" not in workflow.lower()
