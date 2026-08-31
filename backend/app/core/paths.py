"""Filesystem layout helpers.

Every path in the application is derived from :data:`PROJECT_ROOT` so the app
behaves identically no matter which directory the process was started from.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# A frozen one-directory build places these folders under PyInstaller's bundle
# root. Source development keeps the historical repository-relative layout.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    PROJECT_ROOT: Path = Path(getattr(sys, "_MEIPASS")).resolve()
    BACKEND_DIR: Path = PROJECT_ROOT / "backend"
else:
    # .../backend/app/core/paths.py -> .../backend/app/core -> app -> backend -> root
    BACKEND_DIR = Path(__file__).resolve().parents[2]
    PROJECT_ROOT = BACKEND_DIR.parent

CONFIG_DIR: Path = PROJECT_ROOT / "config"


def _default_data_dir() -> Path:
    override = os.environ.get("JOBAGENT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return (base / "JobAgent").resolve()
    return PROJECT_ROOT / "data"


DATA_DIR: Path = _default_data_dir()
UPLOADS_DIR: Path = DATA_DIR / "uploads"
#: Persistent Playwright profiles, one subdirectory per recruitment site.
BROWSER_PROFILES_DIR: Path = DATA_DIR / "browser_profiles"
# Per-user mutable data belongs beside the local database, never in the
# tracked product template under ``config/``.
CAREER_STRATEGY_PATH: Path = DATA_DIR / "career_strategy.yaml"
ENV_FILE_PATH: Path = Path(
    os.environ.get(
        "JOBAGENT_ENV_FILE",
        str(
            DATA_DIR / ".env"
            if getattr(sys, "frozen", False) or os.environ.get("JOBAGENT_DATA_DIR", "").strip()
            else PROJECT_ROOT / ".env"
        ),
    )
).expanduser().resolve()
FRONTEND_DIST_DIR: Path = Path(
    os.environ.get("JOBAGENT_FRONTEND_DIR", str(PROJECT_ROOT / "frontend" / "dist"))
).expanduser().resolve()


def ensure_runtime_dirs() -> None:
    """Create the directories the app writes to at runtime."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    # A new user gets a neutral editable strategy. Existing files are never
    # overwritten, and a stripped package can still fall back to in-code
    # neutral defaults if its template is unexpectedly absent.
    template = CONFIG_DIR / "career_strategy.yaml"
    if not CAREER_STRATEGY_PATH.exists() and template.is_file():
        shutil.copyfile(template, CAREER_STRATEGY_PATH)


def resolve_relative(path_like: str) -> Path:
    """Resolve a possibly-relative path against the project root."""
    p = Path(path_like)
    return p if p.is_absolute() else (PROJECT_ROOT / p)
