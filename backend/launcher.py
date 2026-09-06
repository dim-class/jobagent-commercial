"""Entry point for the packaged single-process build.

Only used by `scripts/build-release.ps1`. Running from source is unchanged:
`scripts/dev.ps1 start` still runs uvicorn against `app.main` with Vite serving
the console on :5173, and nothing here is imported on that path.

What the packaged build does differently, and only this:

* it serves the console itself, from the `frontend-dist` folder shipped beside
  the executable, so the recipient needs neither Node nor a second process;
* it keeps its data next to the executable rather than in the repository, so
  unzipping a new version never touches an existing database.

Everything else - loopback-only binding, the migrations, every policy in
CLAUDE.md - is the same code.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _base_dir() -> Path:
    """Where the executable actually lives (PyInstaller unpacks elsewhere)."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> None:
    base = _base_dir()

    # Set before importing the app: `Settings` reads these at import time, and
    # every one of them is a default the user can still override in .env.
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{base / 'data' / 'jobagent.db'}")
    os.environ.setdefault("JOBAGENT_DATA_DIR", str(base / "data"))
    os.environ.setdefault("JOBAGENT_SERVE_FRONTEND", "true")
    os.environ.setdefault("JOBAGENT_FRONTEND_DIR", str(base / "frontend-dist"))
    data = base / "data"
    data.mkdir(parents=True, exist_ok=True)

    # A packaged build has no `config/` tree, so seed the neutral strategy from
    # the copy shipped beside the executable. Never overwrites an existing one -
    # that file is the user's, and it is the only place their own definition of
    # "a good job" lives.
    shipped = base / "career_strategy.default.yaml"
    target = data / "career_strategy.yaml"
    if shipped.is_file() and not target.exists():
        shutil.copyfile(shipped, target)

    # The console is served from the backend's own origin here, so that origin
    # is the one the extension bridge must trust. The dev origins stay listed:
    # a developer running Vite against this build is not a different product.
    os.environ.setdefault(
        "CORS_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000,"
        "http://127.0.0.1:5173,http://localhost:5173",
    )

    import uvicorn

    from app.main import app

    print("JobAgent 正在运行： http://127.0.0.1:8000")
    print("关闭这个窗口即可退出。")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
