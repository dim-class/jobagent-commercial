"""Setting your own AI credentials from the app.

CLAUDE.md's rule is unchanged and is what these tests pin: the key lives only
in the backend process, read from `.env`. It is never returned by an API, never
written to SQLite, and never logged. What this feature adds is only that it can
be *put* there without editing a file and restarting.
"""

from __future__ import annotations

import pytest

#: The heredoc-safe newline these fixtures write with.
NL = chr(10)

from app.core.config import get_settings
from app.services import ai_settings


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(NL.join(["DATABASE_URL=sqlite:///./x.db", "OPENAI_API_KEY=old-key-value", ""]), encoding="utf-8")
    monkeypatch.setattr(ai_settings, "ENV_PATH", path)
    return path


def test_the_key_is_never_returned(client, env_file, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value-1234")
    get_settings.cache_clear()

    body = client.get("/api/console/ai-settings").json()

    assert body["configured"] is True
    assert "sk-secret-value-1234" not in str(body)
    assert body["hint"] == "…1234", "only enough to tell two keys apart"


def test_saving_takes_effect_without_a_restart(env_file):
    """`get_settings` is cached for the life of the process.

    Without clearing it, a saved key would only appear after a restart - which
    is the entire reason this endpoint exists rather than "edit .env yourself".
    Asserted on the cache, because that is what this module controls; the file
    it writes is asserted separately below.
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

    # ...and the client actually sends there, rather than the setting sitting
    # in a file nothing reads.
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "key-for-the-client")
    get_settings.cache_clear()
    from app.agents.openai_client import build_client

    assert str(build_client(get_settings()).base_url).startswith("https://api.deepseek.com")


def test_no_base_url_still_talks_to_openai(monkeypatch):
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
