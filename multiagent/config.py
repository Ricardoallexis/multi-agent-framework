from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
BUNDLED_ROOT = PACKAGE_ROOT / "_resources"
IN_CHECKOUT = (
    (PROJECT_ROOT / "pyproject.toml").is_file()
    and (PROJECT_ROOT / "catalog" / "agents.yaml").is_file()
)
LOCAL_ROOT = PROJECT_ROOT / ".local" if IN_CHECKOUT else Path.home() / ".multi-agent-framework"


def _resource_dir(name: str) -> Path:
    return (PROJECT_ROOT if IN_CHECKOUT else BUNDLED_ROOT) / name


class Settings(BaseSettings):
    """Central framework configuration.

    Public code and configuration live in the repository or installed package.
    Runtime data uses the checkout's ``.local/`` directory in editable mode
    and a user-owned directory for installed distributions. ``LOCAL_DIR`` can
    redirect the workspace without changing source code.
    """

    model_config = SettingsConfigDict(
        env_file=LOCAL_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    api_base_url: str = "http://127.0.0.1:8000"

    local_dir: Path = LOCAL_ROOT
    data_dir: Optional[Path] = None
    db_path: Optional[Path] = None
    runs_dir: Optional[Path] = None
    assets_dir: Optional[Path] = None

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    ollama_num_ctx: int = Field(default=4096, ge=1024)
    ollama_keep_alive: str = "15m"
    ollama_timeout_seconds: float = 120.0
    cloud_timeout_seconds: float = 120.0
    ollama_context_soft_limit: float = Field(default=0.75, gt=0.3, lt=1.0)

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_grounded_model: str = "gemini-3.5-flash"
    gemini_advanced_model: str = "gemini-3.6-flash"

    openai_api_key: str = ""
    openai_model: str = "configure-me"
    openai_enabled: bool = False

    max_llm_calls: int = Field(default=4, ge=1, le=50)
    max_run_seconds: int = Field(default=300, ge=30)
    worker_poll_seconds: float = Field(default=0.75, gt=0.05)
    worker_enabled: bool = True

    @model_validator(mode="after")
    def _resolve_paths(self):
        self.local_dir = Path(self.local_dir).resolve()
        self.data_dir = Path(self.data_dir or self.local_dir / "data").resolve()
        self.db_path = Path(self.db_path or self.data_dir / "multiagent.db").resolve()
        self.runs_dir = Path(self.runs_dir or self.data_dir / "runs").resolve()
        self.assets_dir = Path(self.assets_dir or self.data_dir / "assets").resolve()
        return self

    @property
    def catalog_dir(self) -> Path:
        return _resource_dir("catalog")

    @property
    def workflows_dir(self) -> Path:
        return _resource_dir("workflows")

    @property
    def prompts_dir(self) -> Path:
        return _resource_dir("prompts")

    @property
    def skills_dir(self) -> Path:
        return _resource_dir("skills")

    @property
    def migrations_dir(self) -> Path:
        return _resource_dir("migrations")

    def ensure_directories(self) -> None:
        for p in [self.local_dir, self.data_dir, self.runs_dir, self.assets_dir, self.assets_dir / "inbox"]:
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
