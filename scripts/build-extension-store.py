"""Build a deterministic, reviewable Chrome Web Store submission candidate.

This script never uploads or publishes.  It derives the store manifest from
the checked-in development manifest, removes development-only port 5173, and
packages an exact allow-list of compiled runtime files.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import struct
import zipfile
from pathlib import Path


STORE_PERMISSIONS = ["activeTab", "scripting", "storage"]
STORE_HOSTS = [
    "http://127.0.0.1:8000/*",
    "http://localhost:8000/*",
    "https://www.zhipin.com/*",
]
RUNTIME_FILES = [
    "popup.html",
    "popup.css",
    "dist/background.js",
    "dist/config.js",
    "dist/console-bridge.js",
    "dist/content.js",
    "dist/overlay.js",
    "dist/popup.js",
    "dist/runner.js",
    "dist/session.js",
    "dist/boss/extract.js",
    "dist/boss/selectors.js",
    "icons/icon16.png",
    "icons/icon32.png",
    "icons/icon48.png",
    "icons/icon128.png",
]
ICON_SIZES = {f"icons/icon{size}.png": size for size in (16, 32, 48, 128)}
FORBIDDEN_TEXT = {
    "eval(": "dynamic evaluation",
    "new Function": "dynamic function construction",
    "chrome.debugger": "debugger permission surface",
    "<all_urls>": "broad host access",
    "jobagent:m7-scan-current-chat": "suspended M7 chat runtime",
    "scanCurrentBossChat": "suspended M7 chat runtime",
    "scanCurrentBossConversation": "suspended M7 chat parser",
    "/api/recruiter-conversations/boss-current-scan": "suspended M7 transport",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def png_dimensions(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


def store_manifest(source: dict[str, object]) -> dict[str, object]:
    manifest = copy.deepcopy(source)
    manifest["permissions"] = STORE_PERMISSIONS
    manifest["host_permissions"] = STORE_HOSTS
    scripts = manifest.get("content_scripts")
    if not isinstance(scripts, list):
        raise ValueError("manifest content_scripts must be a list")
    for script in scripts:
        if not isinstance(script, dict) or not isinstance(script.get("matches"), list):
            raise ValueError("invalid content-script entry")
        script["matches"] = [match for match in script["matches"] if ":5173/" not in match]
        if not script["matches"]:
            raise ValueError("store transform produced an empty content-script match list")
    icons = {str(size): f"icons/icon{size}.png" for size in (16, 32, 48, 128)}
    manifest["icons"] = icons
    action = manifest.get("action")
    if not isinstance(action, dict):
        raise ValueError("manifest action must be an object")
    action["default_icon"] = icons
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    if "5173" in encoded:
        raise ValueError("development-only port 5173 remained in store manifest")
    if manifest.get("manifest_version") != 3:
        raise ValueError("only Manifest V3 is accepted")
    if not isinstance(manifest.get("version"), str):
        raise ValueError("manifest version is missing")
    if len(str(manifest.get("description", ""))) > 132:
        raise ValueError("manifest description exceeds Chrome's 132-character limit")
    return manifest


def audit_runtime(files: dict[str, bytes]) -> None:
    for path, size in ICON_SIZES.items():
        try:
            width, height = png_dimensions(files[path])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid required icon {path}: {exc}") from exc
        if (width, height) != (size, size):
            raise ValueError(f"{path} must be {size}x{size}, got {width}x{height}")
    for path, data in files.items():
        if not path.endswith((".js", ".html")):
            continue
        text = data.decode("utf-8")
        for needle, reason in FORBIDDEN_TEXT.items():
            if needle in text:
                raise ValueError(f"{path} contains {reason}: {needle}")
        if "<script" in text.lower() and "src=\"http" in text.lower():
            raise ValueError(f"{path} contains a remotely hosted script")


def zip_bytes(files: dict[str, bytes]) -> bytes:
    from io import BytesIO

    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, files[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def build(repo: Path, output_dir: Path) -> tuple[Path, Path, Path]:
    extension = repo / "extension"
    source_manifest_bytes = (extension / "manifest.json").read_bytes()
    manifest = store_manifest(json.loads(source_manifest_bytes.decode("utf-8")))
    files = {name: (extension / name).read_bytes() for name in RUNTIME_FILES}
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    audit_runtime(files)
    payload = zip_bytes(files)
    version = manifest["version"]
    output_dir.mkdir(parents=True, exist_ok=True)
    package = output_dir / f"JobAgent-BOSS-Detector-{version}.zip"
    package.write_bytes(payload)
    digest = sha256(payload)
    sums = output_dir / "EXTENSION-SHA256SUMS.txt"
    sums.write_text(f"{digest}  {package.name}\n", encoding="utf-8", newline="\n")
    metadata = {
        "schema_version": 1,
        "distribution_tier": "store_submission_candidate",
        "store_published": False,
        "privacy_review_required": True,
        "version": version,
        "package": package.name,
        "sha256": digest,
        "source_manifest_sha256": sha256(source_manifest_bytes),
        "permissions": manifest["permissions"],
        "host_permissions": manifest["host_permissions"],
        "files": sorted(files),
    }
    metadata_path = output_dir / "EXTENSION-STORE-MANIFEST.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    with zipfile.ZipFile(package) as archive:
        if archive.namelist() != sorted(files):
            raise ValueError("archive entry list is not exact and sorted")
        if json.loads(archive.read("manifest.json")) != manifest:
            raise ValueError("archive manifest did not round-trip")
    return package, sums, metadata_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = (args.output or repo / ".artifacts" / "chrome-web-store").resolve()
    package, sums, metadata = build(repo, output)
    print(package)
    print(sums)
    print(metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
