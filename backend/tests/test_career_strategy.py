"""Career strategy loading, validation, persistence, and hot reload."""

from __future__ import annotations

import pytest
import yaml

from app.core.career_strategy import load_strategy, save_strategy, strategy_hash, strategy_path
from app.core.errors import ValidationError


def test_strategy_ships_with_the_expected_targets():
    strategy = load_strategy(force=True)
    assert strategy["target_cities"] == ["北京", "上海", "广州", "杭州"]
    assert "Cloud Engineer" in strategy["preferred_roles"]
    assert "云运维工程师" in strategy["preferred_roles"]
    assert "AWS" in strategy["relevant_skills"]
    assert "WebSphere" in strategy["relevant_skills"]
    assert any("Helpdesk" in k for k in strategy["excluded_keywords"])
    assert strategy["experience_policy"]["preferred_max_years"] == 3


def test_strategy_is_a_file_not_python():
    """The strategy must stay editable data, per the v0.1 requirements."""
    path = strategy_path()
    assert path.suffix in {".yaml", ".yml"}
    assert path.exists()
    assert isinstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict)


def test_strategy_hash_is_stable_and_order_independent():
    strategy = load_strategy(force=True)
    reordered = dict(reversed(list(strategy.items())))
    assert strategy_hash(strategy) == strategy_hash(reordered)


def test_save_round_trips_and_hot_reloads():
    original = load_strategy(force=True)
    updated = {**original, "target_cities": ["成都", "西安"]}
    save_strategy(updated)

    # a fresh load (no force) must observe the change via mtime
    assert load_strategy()["target_cities"] == ["成都", "西安"]
    assert strategy_hash(updated) != strategy_hash(original)

    on_disk = yaml.safe_load(strategy_path().read_text(encoding="utf-8"))
    assert on_disk["target_cities"] == ["成都", "西安"]


def test_save_rejects_a_malformed_strategy():
    with pytest.raises(ValidationError):
        save_strategy({"target_cities": "北京"})
    with pytest.raises(ValidationError):
        save_strategy({"relevant_skills": [1, 2, 3]})


def test_partial_save_keeps_nested_defaults():
    saved = save_strategy({"target_cities": ["北京"], "experience_policy": {"flexibility": "lenient"}})
    assert saved["experience_policy"]["flexibility"] == "lenient"
    assert saved["experience_policy"]["preferred_max_years"] == 3
    assert saved["salary"]["min_monthly_cny"] == 15000


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------


def test_get_career_strategy(client):
    body = client.get("/api/settings/career-strategy").json()
    assert body["strategy"]["target_cities"] == ["北京", "上海", "广州", "杭州"]
    assert body["path"].endswith(".yaml")
    assert len(body["hash"]) == 64


def test_put_career_strategy_persists(client):
    strategy = client.get("/api/settings/career-strategy").json()["strategy"]
    strategy["target_cities"] = ["北京", "深圳"]
    strategy["relevant_skills"] = ["AWS", "Kubernetes"]
    strategy["experience_policy"]["preferred_max_years"] = 5

    response = client.put("/api/settings/career-strategy", json=strategy)
    assert response.status_code == 200
    assert response.json()["strategy"]["target_cities"] == ["北京", "深圳"]

    reloaded = client.get("/api/settings/career-strategy").json()["strategy"]
    assert reloaded["relevant_skills"] == ["AWS", "Kubernetes"]
    assert reloaded["experience_policy"]["preferred_max_years"] == 5


def test_put_career_strategy_rejects_bad_types(client):
    response = client.put("/api/settings/career-strategy", json={"target_cities": "北京"})
    assert response.status_code == 422
