"""Setting your own AI credentials from the app.

CLAUDE.md's rule is unchanged and is what these tests pin: the key lives only
in the backend process, read from `.env`. It is never returned by an API, never
written to SQLite, and never logged. What this feature adds is only that it can
be *put* there without editing a file and restarting.

The fixture points `Settings` itself at a temporary file, rather than pointing
this module at a file of its own. The old fixture did the second, which is how
`ai_settings` wrote `backend/.env` while the app read the project-root `.env`
with every test here green: none read a saved value back through
`get_settings()`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: The heredoc-safe newline these fixtures write with.
NL = chr(10)

from app.core import paths
from app.core.config import Settings, get_settings
from app.services import ai_settings


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(NL.join(["DATABASE_URL=sqlite:///./x.db", "OPENAI_API_KEY=old-key-value", ""]), encoding="utf-8")
    monkeypatch.setitem(Settings.model_config, "env_file", str(path))
    get_settings.cache_clear()
    yield path
    # A cached Settings would otherwise carry this file's key into every test
    # that runs next, and conftest exists so that none of them has one.
    get_settings.cache_clear()


def test_the_key_is_never_returned(client, env_file, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value-1234")
    get_settings.cache_clear()

    body = client.get("/api/console/ai-settings").json()

    assert body["configured"] is True
    assert "sk-secret-value-1234" not in str(body)
    assert body["hint"] == "…1234", "only enough to tell two keys apart"


def test_a_saved_key_is_the_key_the_app_then_uses(env_file, monkeypatch):
    """The property the feature exists for, and the one no test used to check.

    conftest sets OPENAI_API_KEY to an empty string for every test, and an
    environment variable outranks the file - so it is removed here, as it is
    absent in a normal launch.
    """

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    assert get_settings().openai_api_key == "old-key-value", "Settings reads the fixture file"

    ai_settings.save({"OPENAI_API_KEY": "another-key-5678"})

    assert get_settings().openai_api_key == "another-key-5678"


def test_it_writes_the_file_settings_reads_not_one_of_its_own():
    """The regression, in the real configuration and with no fixture: the path
    written and the path read are one path."""

    assert ai_settings.env_path() == Path(str(Settings.model_config["env_file"])).resolve()
    assert ai_settings.env_path() == paths.ENV_FILE_PATH
    assert ai_settings.env_path() != (paths.BACKEND_DIR / ".env").resolve()


def test_saving_takes_effect_without_a_restart(env_file):
    """`get_settings` is cached for the life of the process.

    Without clearing it, a saved key would only appear after a restart - which
    is the entire reason this endpoint exists rather than "edit .env yourself".
    """

    before = get_settings()
    ai_settings.save({"OPENAI_API_KEY": "another-key-5678"})

    assert get_settings() is not before, "the cached settings were not cleared"
    assert "OPENAI_API_KEY=another-key-5678" in env_file.read_text(encoding="utf-8")


def test_other_env_lines_are_left_exactly_as_written(env_file):
    env_file.write_text(
        NL.join(["# my notes", "DATABASE_URL=sqlite:///./x.db", "OPENAI_API_KEY=old", ""]),
        encoding="utf-8",
    )
    ai_settings.save({"OPENAI_API_KEY": "replacement-key"})

    text = env_file.read_text(encoding="utf-8")
    assert "# my notes" in text
    assert "DATABASE_URL=sqlite:///./x.db" in text
    assert "OPENAI_API_KEY=replacement-key" in text
    assert "OPENAI_API_KEY=old" not in text


def test_a_second_provider_is_written_and_actually_reached(env_file, monkeypatch):
    """One protocol, not several SDKs: another provider is another base URL."""

    ai_settings.save(
        {"OPENAI_BASE_URL": "https://api.deepseek.com/v1", "OPENAI_MODEL_FAST": "deepseek-chat"}
    )
    text = env_file.read_text(encoding="utf-8")
    assert "OPENAI_BASE_URL=https://api.deepseek.com/v1" in text
    assert "OPENAI_MODEL_FAST=deepseek-chat" in text

    # ...and read back from that file, not re-supplied through the environment:
    # the old version of this test set both as variables, so it could not tell
    # a setting the app reads from one sitting in a file nothing reads.
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL_FAST", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "key-for-the-client")
    get_settings.cache_clear()
    assert get_settings().openai_model_fast == "deepseek-chat"
    from app.agents.openai_client import build_client

    assert str(build_client(get_settings()).base_url).startswith("https://api.deepseek.com")


def test_a_value_the_environment_shadows_is_named(env_file, monkeypatch):
    """pydantic-settings reads the process environment before the file, so a key
    saved while OPENAI_API_KEY is set there is written and then ignored. The page
    must say so rather than report the save as in effect."""

    monkeypatch.setenv("OPENAI_API_KEY", "from-the-environment")
    described = ai_settings.save({"OPENAI_API_KEY": "saved-from-the-page"})

    assert "OPENAI_API_KEY" in described["overridden"]
    assert get_settings().openai_api_key == "from-the-environment"

    monkeypatch.delenv("OPENAI_API_KEY")
    assert "OPENAI_API_KEY" not in ai_settings.describe()["overridden"]


def test_no_base_url_still_talks_to_openai(env_file, monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "key-for-the-client")
    get_settings.cache_clear()
    from app.agents.openai_client import build_client

    assert "openai.com" in str(build_client(get_settings()).base_url)


def test_a_key_that_would_corrupt_the_env_file_is_refused(client, env_file):
    from app.core.errors import ValidationError

    for bad in ("has space", 'has"quote', "short"):
        with pytest.raises(ValidationError):
            ai_settings.save({"OPENAI_API_KEY": bad})


def test_a_plain_http_endpoint_is_refused(env_file):
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        ai_settings.save({"OPENAI_BASE_URL": "http://api.example.com/v1"})


def test_nothing_is_written_to_the_database(client, env_file, db):
    from app.models import Job

    before = db.query(Job).count()
    client.put("/api/console/ai-settings", json={"model_fast": "gpt-x"})
    db.expire_all()
    assert db.query(Job).count() == before


def test_the_saved_key_never_reaches_the_log(env_file, caplog):
    with caplog.at_level("INFO"):
        ai_settings.save({"OPENAI_API_KEY": "sk-should-not-appear-9999"})

    assert "sk-should-not-appear-9999" not in caplog.text
    assert "settings.ai_saved" in caplog.text
