"""
src.config — single source of truth for WESA agent-ecosystem configuration.

E0 scope (v2 report 3.1): replace the ~15 scattered env-var reads + magic
constants at the top of daemon.py / web_monitor.py with one validated
WesaConfig object, loaded once from .env (or wesa.toml later).

What this does NOT do yet (deferred to E1 engine decomposition):
  - Rewire every src/*.py module's individual os.getenv() calls
    (gemini_agent.py, ollama_agent.py, tool_layer.py, etc.) — those modules
    are either dead-Gemini (E0.3) or will be absorbed into the provider
    abstraction (E1) anyway. Rewiring them now and again in E1 is wasted
    motion.
  - dev/prod "profiles" beyond the existing WESA_ENV flag — full profile
    support lands with the Postgres queue adapter in E1/E4.

Usage:
    from src.config import get_config
    cfg = get_config()
    cfg.daemon_max_workers
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class WesaConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env has many legacy keys (GITHUB_TOKEN etc.) we don't model yet
    )

    # --- environment ---
    wesa_env: str = "development"

    # --- daemon scheduling (was: top-of-daemon.py magic constants) ---
    daemon_max_workers: int = 4
    rc_interval_hours: int = 4
    sc1_interval_hours: int = 2
    sy1_interval_hours: int = 24

    # --- ntfy / notifications ---
    ntfy_topic: str = ""

    # --- ollama (local provider, currently live) ---
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = ""

    # --- lumio infra manager (used by web_monitor infra tab) ---
    lumio_infra_manager_url: str = "http://192.168.1.62:30766"

    # --- paths ---
    agent_log_dir: str = "./logs"
    agent_task_dir: str = "./tasks"

    @property
    def is_dev(self) -> bool:
        return self.wesa_env.lower() != "production"

    @property
    def logs_dir(self) -> Path:
        return Path(self.agent_log_dir)

    @property
    def tasks_dir(self) -> Path:
        return Path(self.agent_task_dir)


@lru_cache
def get_config() -> WesaConfig:
    """Cached singleton — call this everywhere instead of constructing
    WesaConfig() directly, so the whole process shares one validated config
    loaded once from .env."""
    return WesaConfig()
