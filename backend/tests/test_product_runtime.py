"""Clean-user runtime checks for the commercial single-process foundation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


BACKEND_DIR = Path(__file__).resolve().parents[1]


def clean_runtime_env(data_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in (
        "CAREER_STRATEGY_PATH",
        "DATABASE_URL",
        "JOBAGENT_ENV_FILE",
        "JOBAGENT_FRONTEND_DIR",
        "JOBAGENT_SERVE_FRONTEND",
    ):
        env.pop(name, None)
    env["JOBAGENT_DATA_DIR"] = str(data_dir)
    env["OPENAI_API_KEY"] = ""
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def test_doctor_initializes_an_isolated_neutral_user_directory(tmp_path):
    data_dir = tmp_path / "new-user"
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "doctor", "--json"],
        cwd=BACKEND_DIR,
        env=clean_runtime_env(data_dir),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["mode"] == "development"
    assert payload["openai_configured"] is False
    strategy_path = data_dir / "career_strategy.yaml"
    strategy = yaml.safe_load(strategy_path.read_text(encoding="utf-8"))
    assert strategy["preferred_roles"] == []
    assert strategy["target_cities"] == []
    assert strategy["early_career_policy"] == "include"


def test_opt_in_single_process_mode_serves_the_built_frontend(tmp_path):
    data_dir = tmp_path / "user-data"
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text(
        "<!doctype html><title>JobAgent packaged test</title>", encoding="utf-8"
    )
    env = clean_runtime_env(data_dir)
    env["JOBAGENT_SERVE_FRONTEND"] = "true"
    env["JOBAGENT_FRONTEND_DIR"] = str(frontend)

    script = """
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    root = client.get('/')
    health = client.get('/health')
    print(root.status_code, root.text, health.status_code)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "200 <!doctype html><title>JobAgent packaged test</title> 200" in result.stdout
    assert (data_dir / "jobagent.db").is_file()
