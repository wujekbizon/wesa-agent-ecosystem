"""Tests for src.config — E0.2 config object."""

from src.config import WesaConfig, get_config


def test_defaults_load_without_env():
    cfg = WesaConfig(_env_file=None)
    assert cfg.daemon_max_workers == 4
    assert cfg.rc_interval_hours == 4
    assert cfg.sc1_interval_hours == 2
    assert cfg.sy1_interval_hours == 24
    assert cfg.wesa_env == "development"


def test_is_dev_property():
    cfg = WesaConfig(_env_file=None, wesa_env="development")
    assert cfg.is_dev is True

    cfg = WesaConfig(_env_file=None, wesa_env="production")
    assert cfg.is_dev is False


def test_logs_and_tasks_dir_are_paths():
    cfg = WesaConfig(_env_file=None)
    assert str(cfg.logs_dir) == "logs"
    assert str(cfg.tasks_dir) == "tasks"


def test_get_config_is_cached_singleton():
    a = get_config()
    b = get_config()
    assert a is b


def test_env_var_override(monkeypatch):
    monkeypatch.setenv("DAEMON_MAX_WORKERS", "9")
    cfg = WesaConfig(_env_file=None)
    assert cfg.daemon_max_workers == 9
