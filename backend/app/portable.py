"""Single-process entry point for a future Windows packaged release.

Build-time tooling supplies the compiled frontend and bundles Python. Runtime
data remains outside the bundle under the per-user data root selected by
``app.core.paths``. This module never starts a recruitment task or browser.
"""

from __future__ import annotations

import os
import threading
import time
import urllib.request
import webbrowser


def _open_when_ready(url: str) -> None:
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"{url}health", timeout=1) as response:
                if 200 <= response.status < 500:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.25)


def main() -> None:
    os.environ.setdefault("JOBAGENT_SERVE_FRONTEND", "true")
    os.environ.setdefault("APP_HOST", "127.0.0.1")
    os.environ.setdefault("APP_PORT", "8000")

    # Import only after packaged-mode defaults are fixed in the environment.
    import uvicorn
    from app.main import app

    url = "http://127.0.0.1:8000/"
    threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
