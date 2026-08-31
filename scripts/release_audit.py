"""Fail closed if a Windows bundle contains personal/runtime residue."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REQUIRED = (
    "JobAgent.exe",
    "runtime/config/career_strategy.yaml",
    "runtime/frontend/dist/index.html",
    "runtime/backend/alembic.ini",
    "extension/manifest.json",
    "extension/dist/background.js",
    "extension/dist/console-bridge.js",
    "README-FIRST.txt",
    "Stop-JobAgent.cmd",
)
FORBIDDEN_RUNTIME_DIRECTORIES = frozenset({"data", "uploads", "browser_profiles", "logs"})
FORBIDDEN_FILE_NAMES = frozenset({
    ".env", ".env.local", "runtime-process.json", "direct_url.json"
})
FORBIDDEN_SUFFIXES = (
    ".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3", ".log"
)


def audit_bundle(root: Path) -> dict[str, object]:
    root = root.resolve()
    missing = [relative for relative in REQUIRED if not (root / relative).is_file()]
    forbidden: list[str] = []
    files: list[dict[str, object]] = []

    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            parts = relative.lower().split("/")
            product_relative = parts[1:] if parts[:1] == ["runtime"] else parts
            if len(product_relative) == 1 and product_relative[0] in FORBIDDEN_RUNTIME_DIRECTORIES:
                forbidden.append(relative + "/")
            continue
        lower_name = path.name.lower()
        if lower_name in FORBIDDEN_FILE_NAMES or lower_name.endswith(FORBIDDEN_SUFFIXES):
            forbidden.append(relative)
        if relative != "RELEASE-MANIFEST.json":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            files.append({"path": relative, "bytes": path.stat().st_size, "sha256": digest})

    return {
        "ok": not missing and not forbidden,
        "missing": missing,
        "forbidden": forbidden,
        "file_count": len(files),
        "files": files,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args(argv)
    result = audit_bundle(args.bundle)
    if args.write_manifest and result["ok"]:
        (args.bundle / "RELEASE-MANIFEST.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({key: value for key, value in result.items() if key != "files"}, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
