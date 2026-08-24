"""Filesystem layout helpers.

Every path in the application is derived from :data:`PROJECT_ROOT` so the app
behaves identically no matter which directory the process was started from.
"""

from __future__ import annotations

from pathlib import Path

# .../backend/app/core/paths.py -> .../backend/app/core -> app -> backend -> root
BACKEND_DIR: Path = Path(__file__).resolve().parents[2]
PROJECT_ROOT: Path = BACKEND_DIR.parent

CONFIG_DIR: Path = PROJECT_ROOT / "config"
DATA_DIR: Path = PROJECT_ROOT / "data"
UPLOADS_DIR: Path = DATA_DIR / "uploads"
#: Persistent Playwright profiles, one subdirectory per recruitment site.
BROWSER_PROFILES_DIR: Path = DATA_DIR / "browser_profiles"
CAREER_STRATEGY_PATH: Path = CONFIG_DIR / "career_strategy.yaml"


def ensure_runtime_dirs() -> None:
    """Create the directories the app writes to at runtime."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def resolve_relative(path_like: str) -> Path:
    """Resolve a possibly-relative path against the project root."""
    p = Path(path_like)
    return p if p.is_absolute() else (PROJECT_ROOT / p)
