from decimal import Decimal
from pathlib import Path

import pytest
from scavenger.config import Config, ConfigError, load

EXAMPLE = Path(__file__).parents[2] / "scavenger.example.toml"


def test_loads_example_config():
    config = load(EXAMPLE)
    assert config.budget.currency == "USD"
    assert config.loop.tick_seconds == 60
    assert config.loop.max_active == 1


def test_unknown_key_is_error(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[loop]\ntick_seconds = 60\nbogus_key = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load(bad)


def test_paths_resolve_relative_to_config_file(tmp_path):
    conf = tmp_path / "sub" / "scavenger.toml"
    conf.parent.mkdir()
    conf.write_text(
        '[paths]\nmissions = "m"\ndb = "l.db"\nrun_dir = "r"\n', encoding="utf-8"
    )
    config = load(conf)
    assert config.paths.missions == conf.parent / "m"
    assert config.paths.db == conf.parent / "l.db"


def test_require_env_missing_names_variable(monkeypatch):
    monkeypatch.delenv("SCAVENGER_TEST_MISSING_VAR", raising=False)
    with pytest.raises(ConfigError, match="SCAVENGER_TEST_MISSING_VAR"):
        Config.require_env("SCAVENGER_TEST_MISSING_VAR")


def test_money_fields_are_decimal():
    config = load(EXAMPLE)
    assert isinstance(config.llm.price_per_million_input, Decimal)
    assert isinstance(config.llm.price_per_million_output, Decimal)
    assert isinstance(config.supervisor.max_hourly_money, Decimal)


def test_channel_feeds_and_rounds_parse(tmp_path):
    conf = tmp_path / "scavenger.toml"
    conf.write_text(
        "[channels]\n"
        'enabled = ["grants"]\n'
        'grants_feeds = ["https://feeds.example/rounds.rss"]\n'
        'inbox_feeds = ["https://jobs.example/feed.rss"]\n'
        "\n"
        "[[channels.grants_rounds]]\n"
        'name = "Example round"\n'
        'url = "https://rounds.example/one"\n'
        'amount = "20000"\n'
        "\n"
        "[[channels.inbox_filters]]\n"
        'subject_regex = "(?i)freelance"\n',
        encoding="utf-8",
    )
    config = load(conf)
    assert config.channels.grants_feeds == ("https://feeds.example/rounds.rss",)
    assert config.channels.grants_rounds[0].name == "Example round"
    assert config.channels.inbox_filters[0].subject_regex == "(?i)freelance"
