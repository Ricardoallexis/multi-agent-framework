from __future__ import annotations

import ipaddress
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

SESSION_SOURCES = ("claude", "codex", "copilot", "framework")  # sources of the GUI session viewer

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


def _as_list(value: Any) -> Any:
    """A list as is; text as a JSON array or as items separated by ``;`` (or the OS path separator)."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    parts = text.split(";") if os.pathsep == ";" else text.replace(";", os.pathsep).split(os.pathsep)
    return [part.strip() for part in parts if part.strip()]


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
    api_allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost"])
    api_base_url: str = "http://127.0.0.1:8000"

    local_dir: Path = LOCAL_ROOT
    data_dir: Optional[Path] = None
    db_path: Optional[Path] = None
    runs_dir: Optional[Path] = None
    assets_dir: Optional[Path] = None
    # Local, trusted file of named project commands for task gates (ORQ-2);
    # empty means <local_dir>/project_commands.toml. Never read from a task or a request.
    project_commands_file: Optional[Path] = None

    # GUI (/gui): local access token file (empty means ~/.multiagent/gui-token) and SSE timing.
    gui_token_file: Optional[Path] = None
    # The launcher refuses a token file outside the user's home folder unless this is set (see gui/token.py).
    gui_token_outside_home: bool = False
    gui_heartbeat_seconds: float = Field(default=20.0, gt=0)
    gui_poll_seconds: float = Field(default=0.5, gt=0)
    # 0 keeps each SSE connection open until the client leaves; tests set a small bound.
    gui_stream_max_seconds: float = Field(default=0.0, ge=0)
    # Session viewer (GUI-T02 contract, section 6). Only sessions whose working folder is inside one of
    # ``gui_session_roots`` are shown; empty (the default) shows none. Environment values: a JSON list or
    # items separated by ``;``. The four ``gui_*_home``/``_dir`` settings override where each store lives.
    gui_session_roots: Annotated[list[str], NoDecode] = Field(default_factory=list)
    gui_session_sources: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(SESSION_SOURCES))
    gui_claude_home: Optional[Path] = None
    gui_codex_home: Optional[Path] = None
    gui_copilot_home: Optional[Path] = None
    gui_claude_desktop_dir: Optional[Path] = None
    # GUI-T06: may the viewer launch ``claude --desktop --resume <id>`` in a new console (Windows)? False
    # makes the screen show the command to copy instead; the endpoint then answers 403 and starts nothing.
    gui_allow_launch: bool = True

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
    mock_mode: bool = False

    @field_validator("gui_session_roots", "gui_session_sources", mode="before")
    @classmethod
    def _split_gui_lists(cls, value: Any) -> Any:
        return _as_list(value)

    @field_validator("gui_session_roots")
    @classmethod
    def _validate_session_roots(cls, values: list[str]) -> list[str]:
        roots = [str(Path(v).expanduser()) for v in values if isinstance(v, str) and v.strip()]
        if len(roots) != len(values):
            raise ValueError("gui_session_roots must contain non-empty folder paths")
        return list(dict.fromkeys(roots))

    @field_validator("gui_session_sources")
    @classmethod
    def _validate_session_sources(cls, values: list[str]) -> list[str]:
        unknown = [v for v in values if v not in SESSION_SOURCES]
        if unknown:
            raise ValueError(f"gui_session_sources accepts only {', '.join(SESSION_SOURCES)}; got {', '.join(map(str, unknown))}")
        return list(dict.fromkeys(values))

    @field_validator("api_allowed_hosts")
    @classmethod
    def _validate_api_hosts(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("api_allowed_hosts must not be empty")
        normalized = []
        for value in values:
            host = value.lower()
            try:
                ipaddress.ip_address(host)
            except ValueError:
                labels = host.split(".")
                if len(host) > 253 or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in labels
                ):
                    raise ValueError("api_allowed_hosts requires exact hostnames or IP addresses, without ports or wildcards")
            normalized.append(host)
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def _resolve_paths(self):
        self.local_dir = Path(self.local_dir).resolve()
        self.data_dir = Path(self.data_dir or self.local_dir / "data").resolve()
        self.db_path = Path(self.db_path or self.data_dir / "multiagent.db").resolve()
        self.runs_dir = Path(self.runs_dir or self.data_dir / "runs").resolve()
        self.assets_dir = Path(self.assets_dir or self.data_dir / "assets").resolve()
        self.project_commands_file = Path(self.project_commands_file or self.local_dir / "project_commands.toml").resolve()
        self.gui_token_file = Path(self.gui_token_file or Path.home() / ".multiagent" / "gui-token").expanduser().resolve()
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
