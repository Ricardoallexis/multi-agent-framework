from __future__ import annotations

import ipaddress
import json
import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

SESSION_SOURCES = ("claude", "codex", "copilot", "antigravity", "framework")  # sources of the GUI session viewer
BUDGET_SCOPES = ("month", "project", "run")   # USO-T03: what a budget limits
BUDGET_MODES = ("warn", "cut")                # USO-T03: only report (default) or let USO-T04 cut the call
DEFAULT_BUDGET_ALERTS = (80.0, 95.0)          # percent of a budget that raises an alert
AVAILABILITY_KINDS = ("cancel", "interrupt", "adjust", "note")   # AVA-T01: what a message of the person asks for
CONTINUITY_MODES = ("manual", "semi_auto")    # CNT-T05; ``auto`` (USO-T08) is the GUI switch, never a setting or an environment value
CONTINUITY_CANDIDATE_POLICIES = ("no_ask", "ask", "never")   # per executor: delegate without asking / ask first / never
CONTINUITY_WAIT_THRESHOLDS = (1, 3, 5, 24)    # hours: a reset that far away or less is waited for, not delegated
DEFAULT_CONTINUITY_CANDIDATES = {"claude_cli": "no_ask", "codex_cli": "no_ask", "copilot_cli": "ask"}
INSTANCE_LABEL_MAX = 40                       # PRV-T01: characters of the instance label
_BUDGET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_EXECUTOR_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CURRENCY = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,15}$")

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


def _percents(value: Any) -> tuple[float, ...]:
    """Alert percents as a sorted tuple without repeats, each above 0 and at most 100.

    Text is a JSON array or numbers separated by ``,`` or ``;``. An empty list means "no alerts".
    """
    if isinstance(value, str):
        text = value.strip()
        value = json.loads(text) if text.startswith("[") else [part for part in re.split(r"[;,]", text) if part.strip()]
    if not isinstance(value, (list, tuple)):
        raise ValueError("alerts must be a list of percents")
    found: set[float] = set()
    for item in value:
        if isinstance(item, bool):
            raise ValueError("alerts must be numbers")
        try:
            number = float(item)
        except (TypeError, ValueError):
            raise ValueError("alerts must be numbers") from None
        if not 0 < number <= 100:   # also rejects nan
            raise ValueError("each alert must be above 0 and at most 100 percent")
        found.add(number)
    return tuple(sorted(found))


class UsageBudget(BaseModel):
    """One spending budget (USO-T03), compared with the estimated cost of the framework's provider calls.

    * ``scope``: ``month`` (everything spent in the current UTC calendar month), ``project`` (everything a
      project has spent since it began; ``project`` is required) or ``run`` (the cap of each single run).
      ``project`` also narrows a ``month`` or ``run`` budget to the runs of that project.
    * ``amount`` is in ``currency``, the currency of the price evidence (default ``USD``): only costs priced
      in that currency are counted, nothing is converted.
    * ``mode``: ``warn`` reports; ``cut`` additionally asks USO-T04 to hold a call that would pass the amount.
      Empty means ``usage_budget_mode``. ``alerts`` are the percents that raise a ``budget.threshold``
      event; empty means ``usage_budget_alerts``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    scope: str
    amount: float = Field(gt=0, allow_inf_nan=False)   # a JSON number: neither a bool nor a numeric text is accepted
    currency: str = "USD"
    project: Optional[str] = None
    mode: Optional[str] = None
    alerts: Optional[tuple[float, ...]] = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _BUDGET_ID.fullmatch(value):
            raise ValueError("id must be 1-64 letters, digits, '_', '-' or '.' and start with a letter or digit")
        return value

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, value: str) -> str:
        if value not in BUDGET_SCOPES:
            raise ValueError(f"scope accepts only {', '.join(BUDGET_SCOPES)}")
        return value

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, value: str) -> str:
        if not _CURRENCY.fullmatch(value):
            raise ValueError("currency must be 2-16 letters or digits, like the currency of the price evidence")
        return value.upper()

    @field_validator("amount", mode="before")
    @classmethod
    def _check_amount(cls, value: Any) -> Any:
        # ``True`` would become 1.0 and "10" would be coerced. The environment value is a JSON array, so an amount is
        # always a JSON number there and nothing needs the text form.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("amount must be a number (not a boolean or a text)")
        return value

    @field_validator("project")
    @classmethod
    def _check_project(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        name = value.strip()   # " alpha " would never match a project
        if not name or len(name) > 120:
            raise ValueError("project must be a project name of at most 120 characters")
        return name

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in BUDGET_MODES:
            raise ValueError(f"mode accepts only {', '.join(BUDGET_MODES)}")
        return value

    @field_validator("alerts", mode="before")
    @classmethod
    def _check_alerts(cls, value: Any) -> Any:
        return None if value is None else _percents(value)

    @model_validator(mode="after")
    def _project_scope_needs_a_project(self):
        if self.scope == "project" and self.project is None:
            raise ValueError("a budget with scope 'project' needs a project")
        return self


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
    db_reuse_connections: bool = True
    runs_dir: Optional[Path] = None
    external_preview_ttl_seconds: int = Field(default=900, ge=1)
    external_evidence_max_file_bytes: int = Field(default=10_000_000, ge=1)
    external_evidence_max_total_bytes: int = Field(default=25_000_000, ge=1)
    assets_dir: Optional[Path] = None
    # Local, trusted file of named project commands for task gates (ORQ-2);
    # empty means <local_dir>/project_commands.toml. Never read from a task or a request.
    project_commands_file: Optional[Path] = None
    # ARC-T01: optional workspace folder exposed to the built-in ``file.*`` workflow tools (no git required).
    # Empty means no file tool is registered; set to confine list/read/write/move strictly inside this resolved folder.
    file_tools_root: Optional[Path] = None

    # GUI (/gui): local access token file (empty means ~/.multiagent/gui-token) and SSE timing.
    gui_token_file: Optional[Path] = None
    # The launcher refuses a token file outside the user's home folder unless this is set (see gui/token.py).
    gui_token_outside_home: bool = False
    # PRV-T01: a short plain-text name of this instance, shown in the GUI header and in the tab title. Empty shows
    # nothing. At most 40 characters and no control character; it is never used in a path, a file name or a log.
    instance_label: str = ""
    gui_heartbeat_seconds: float = Field(default=20.0, gt=0)
    gui_poll_seconds: float = Field(default=0.5, gt=0)
    # 0 keeps each SSE connection open until the client leaves; tests set a small bound.
    gui_stream_max_seconds: float = Field(default=0.0, ge=0)
    # Session viewer (GUI-T02 contract, section 6). Only sessions whose working folder is inside one of
    # ``gui_session_roots`` are operator-approved folders; the session viewer also permits the dedicated
    # ``executor_workdir_root``. Environment values: a JSON list or items separated by ``;``.
    gui_session_roots: Annotated[list[str], NoDecode] = Field(default_factory=list)
    gui_session_sources: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(SESSION_SOURCES))
    gui_claude_home: Optional[Path] = None
    gui_codex_home: Optional[Path] = None
    gui_copilot_home: Optional[Path] = None
    gui_antigravity_home: Optional[Path] = None  # default ~/.gemini/antigravity-cli
    gui_claude_desktop_dir: Optional[Path] = None
    # USO-T02 usage service. Both are the operator's own manual input (the apps leave neither on disk): the
    # monthly AI credit cap of their Copilot plan, and a JSON file a Claude ``statusLine`` script saved (its
    # ``rate_limits``). Empty means "not available" is shown instead of a percent.
    usage_copilot_monthly_credits: Optional[int] = Field(default=None, ge=1)
    usage_claude_statusline_file: Optional[Path] = None

    # CUO-T01: optional override for Copilot's read-only quota cache. Empty uses LOCALAPPDATA outside Mock mode.
    usage_copilot_cache_file: Optional[Path] = None

    # USO-T03 budgets: a list of ``UsageBudget`` (an environment value is a JSON array), the mode they use unless
    # a budget sets its own (``warn`` reports, ``cut`` lets USO-T04 hold the call) and the percents that raise an
    # alert (``80;95`` or ``[80, 95]``). No budget is configured by default: nothing is limited or reported.
    usage_budgets: Annotated[list[UsageBudget], NoDecode] = Field(default_factory=list)
    usage_budget_mode: str = "warn"
    usage_budget_alerts: Annotated[tuple[float, ...], NoDecode] = DEFAULT_BUDGET_ALERTS
    # GUI-T06: may the viewer launch ``claude --desktop --resume <id>`` in a new console (Windows)? False
    # makes the screen show the command to copy instead; the endpoint then answers 403 and starts nothing.
    gui_allow_launch: bool = True
    # GUI2-T17: a model reads the free text of "New session" and proposes a workflow and its inputs. Off: the chat
    # matches words only. ``gui_new_session_model`` is the catalog model used (Mock mode always answers from a fixture).
    gui_new_session_interpret: bool = True
    gui_new_session_model: str = "local_default"

    # ORQ-T51 executor applications. ``executor_workdir_root`` holds the work folders the framework creates for tasks
    # (empty means <local_dir>/executor_runs); GUI-created runs stay under this root. A launch is stopped after
    # ``executor_timeout_seconds``; a quota stop
    # pauses the executor for ``executor_quota_pause_minutes``. A catalog declaration may override the last two.
    executor_workdir_root: Optional[Path] = None
    executor_timeout_seconds: int = Field(default=1800, ge=1, le=86400)
    executor_quota_pause_minutes: int = Field(default=300, ge=1, le=10080)
    # GEM-T03 Antigravity (agy) executor: supervised and off by default. It is enabled only when its catalog
    # declaration says ``enabled: true`` AND ``antigravity_notice_accepted`` holds the id of the third-party notice the
    # person accepted (``agy-third-party-notice/1``, see docs/EXECUTORS.md). While the file ``antigravity_kill_switch``
    # exists (empty means <data_dir>/antigravity-off) nothing is launched and a running agy is stopped.
    # ``antigravity_lock_file`` (empty means <data_dir>/antigravity-run.lock) keeps one agy run at a time across the
    # processes of the installation. ``antigravity_add_dirs`` are absolute folders passed as ``--add-dir`` to write runs
    # (the virtual environment and the Python installation, so tests can run in agy's sandbox): a JSON list or items
    # separated by ``;``.
    antigravity_notice_accepted: str = ""
    antigravity_kill_switch: Optional[Path] = None
    antigravity_lock_file: Optional[Path] = None
    antigravity_add_dirs: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # AVA-T01/T02 availability policy (``services.availability.policy_from_settings`` reads these and enforces the same
    # limits). Off by default: ``run_task`` then waits for the whole attempt. On, an attempt is launched in the background
    # and the caller waits at most ``availability_max_wait_seconds`` (1 to 120) before it is told ``running``;
    # ``availability_heartbeat_seconds`` (5 to 300) is how often a running attempt writes ``executor.heartbeat``.
    # ``availability_priorities`` is the inbox order (``cancel`` first; ``cancel;interrupt;adjust;note`` or a JSON array) and
    # ``availability_orchestrator_threshold`` (0.50 to 0.60) is reserved for S8: validated, never read by a flow.
    availability_enabled: bool = False
    availability_max_wait_seconds: float = Field(default=30, ge=1, le=120, allow_inf_nan=False)
    availability_heartbeat_seconds: float = Field(default=60, ge=5, le=300, allow_inf_nan=False)
    availability_priorities: Annotated[tuple[str, ...], NoDecode] = AVAILABILITY_KINDS
    availability_orchestrator_threshold: Optional[float] = Field(default=None, ge=0.5, le=0.6, allow_inf_nan=False)

    # CNT-T05 continuity policy (contract CNT-T01 section 7), the profile level: what to do with a task held by a usage
    # limit. ``manual`` always asks; ``semi_auto`` waits for a reset that is ``continuity_wait_threshold_hours`` away or
    # less, and otherwise delegates without asking only to an executor marked ``no_ask`` (see ``services/handoff.py``).
    # ``auto`` is not accepted here: only the person's switch in the GUI turns it on (USO-T08, ``gui-settings.json``). A task
    # can only restrict these values further.
    # ``continuity_candidates`` maps an executor id to ``no_ask``, ``ask`` or ``never`` (an environment value is a JSON
    # object or ``claude_cli=no_ask;copilot_cli=never``); the ones it names replace the defaults, the rest keep them, and
    # an executor it does not know is asked about.
    continuity_mode: str = "semi_auto"
    continuity_wait_threshold_hours: int = 5
    continuity_max_handoffs: int = Field(default=1, ge=1, le=3)
    continuity_candidates: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=lambda: dict(DEFAULT_CONTINUITY_CANDIDATES))
    # The one integration marker of the installation (empty means <data_dir>/watchers/integration.lock). While the file
    # exists an integration (or a watcher resuming a task) is going on and no held task is handed over (contract section 8).
    # The services and ``multiagent watcher`` both read it from here: a command option cannot point somewhere else.
    integration_marker: Optional[Path] = None

    # SKL-T01 bundle scan (``bundle_scan.py``): ``auto`` runs SkillSpector when its signed ``python.exe`` is found,
    # ``off`` never does. ``skillspector_python`` is the interpreter of another installation (empty means the uv default).
    bundle_scan: Literal["auto", "off"] = "auto"
    skillspector_python: str = ""
    bundle_scan_timeout_seconds: float = Field(default=300.0, ge=10.0, le=3600.0, allow_inf_nan=False)

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    ollama_num_ctx: int = Field(default=8192, ge=1024)
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

    # ARR3-T07: which certificates the provider clients trust: certifi, system or file:<path> (adapters/tls.py).
    # The ``provider_tls_trust`` key of gui-settings.json, written by the GUI, wins over this value.
    provider_tls_trust: str = "certifi"

    max_llm_calls: int = Field(default=4, ge=1, le=50)
    max_run_seconds: int = Field(default=300, ge=30)
    approval_timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    # OpenTelemetry traces and metrics of the native engine (CAP-T21; needs the ``otel`` extra). Off: nothing is imported
    # or exported. Prompts and responses stay out of the spans unless ``telemetry_otel_capture_content`` is on.
    telemetry_otel_enabled: bool = False
    telemetry_otel_capture_content: bool = False
    egress_default: Literal["off", "redact", "block", "escalate"] = "off"
    # Execution backend used by the run service and local worker.
    execution_backend: str = "native"
    worker_poll_seconds: float = Field(default=0.75, gt=0.05)
    worker_enabled: bool = True
    # Per-process run threads; the optional limits are enforced across processes at claim time.
    worker_concurrency: int = Field(default=1, ge=1)
    worker_max_active_total: int | None = Field(default=None, ge=1)
    worker_max_active_per_project: int | None = Field(default=None, ge=1)
    # PAR-T02: explicit environment limits override persisted GUI limits; CLI can override these per process.
    worker_max_active_per_app: int | None = Field(default=None, ge=1)
    worker_max_active_per_model: int | None = Field(default=None, ge=1)
    # A live run renews its lease until it ends or shutdown stops waiting for it.
    worker_lease_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    worker_shutdown_wait_seconds: float = Field(default=30, ge=0, allow_inf_nan=False)
    eval_live_timeout_seconds: float = Field(default=900, gt=0, allow_inf_nan=False)
    # A case regresses when its score drops by more than this (0 to 1); 0 flags any drop.
    eval_regression_threshold: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)
    mock_mode: bool = False

    @field_validator("instance_label")
    @classmethod
    def _validate_instance_label(cls, value: str) -> str:
        if len(value) > INSTANCE_LABEL_MAX:
            raise ValueError(f"INSTANCE_LABEL has {len(value)} characters; the limit is {INSTANCE_LABEL_MAX}")
        if any(unicodedata.category(char) == "Cc" for char in value):
            raise ValueError("INSTANCE_LABEL must not contain control characters")
        return value

    @field_validator("gui_session_roots", "gui_session_sources", mode="before")
    @classmethod
    def _split_gui_lists(cls, value: Any) -> Any:
        return _as_list(value)

    @field_validator("antigravity_add_dirs", mode="before")
    @classmethod
    def _split_add_dirs(cls, value: Any) -> Any:
        return _as_list(value)

    @field_validator("antigravity_add_dirs")
    @classmethod
    def _validate_add_dirs(cls, values: list[str]) -> list[str]:
        folders = [str(Path(v).expanduser()) for v in values if isinstance(v, str) and v.strip()]
        if len(folders) != len(values) or not all(Path(folder).is_absolute() for folder in folders):
            raise ValueError("antigravity_add_dirs must contain absolute folder paths")
        return list(dict.fromkeys(folders))

    @field_validator("usage_budgets", mode="before")
    @classmethod
    def _split_budgets(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            return json.loads(text) if text else []
        return value

    @field_validator("usage_budgets")
    @classmethod
    def _validate_budgets(cls, values: list[UsageBudget]) -> list[UsageBudget]:
        ids = [budget.id for budget in values]
        repeated = sorted({name for name in ids if ids.count(name) > 1})
        if repeated:
            raise ValueError(f"usage_budgets ids must be unique; repeated: {', '.join(repeated)}")
        return values

    @field_validator("usage_budget_mode")
    @classmethod
    def _validate_budget_mode(cls, value: str) -> str:
        if value not in BUDGET_MODES:
            raise ValueError(f"usage_budget_mode accepts only {', '.join(BUDGET_MODES)}")
        return value

    @field_validator("usage_budget_alerts", mode="before")
    @classmethod
    def _validate_budget_alerts(cls, value: Any) -> tuple[float, ...]:
        return _percents(value)

    @field_validator("provider_tls_trust")
    @classmethod
    def _validate_provider_tls_trust(cls, value: str) -> str:
        from .adapters.tls import parse_tls_trust    # lazy: the adapters import this module
        return parse_tls_trust(value)

    @field_validator("continuity_mode")
    @classmethod
    def _validate_continuity_mode(cls, value: str) -> str:
        if value not in CONTINUITY_MODES:
            raise ValueError(f"continuity_mode accepts only {', '.join(CONTINUITY_MODES)}")
        return value

    @field_validator("continuity_wait_threshold_hours")
    @classmethod
    def _validate_continuity_threshold(cls, value: int) -> int:
        if value not in CONTINUITY_WAIT_THRESHOLDS:
            raise ValueError("continuity_wait_threshold_hours accepts only "
                             + ", ".join(str(hours) for hours in CONTINUITY_WAIT_THRESHOLDS))
        return value

    @field_validator("continuity_candidates", mode="before")
    @classmethod
    def _validate_continuity_candidates(cls, value: Any) -> dict[str, str]:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                value = {}
            elif text.startswith("{"):
                value = json.loads(text)
            else:
                pairs = [part.split("=", 1) for part in re.split(r"[;,]", text) if part.strip()]
                if any(len(pair) != 2 for pair in pairs):
                    raise ValueError("continuity_candidates must be a JSON object or items like claude_cli=no_ask")
                value = {key.strip(): policy.strip() for key, policy in pairs}
        if not isinstance(value, dict):
            raise ValueError("continuity_candidates must map executor ids to no_ask, ask or never")
        found = dict(DEFAULT_CONTINUITY_CANDIDATES)
        for key, policy in value.items():
            if not isinstance(key, str) or not _EXECUTOR_KEY.fullmatch(key):
                raise ValueError("continuity_candidates keys must be executor ids")
            if policy not in CONTINUITY_CANDIDATE_POLICIES:
                raise ValueError(f"continuity_candidates values accept only {', '.join(CONTINUITY_CANDIDATE_POLICIES)}")
            found[key] = policy
        return found

    @field_validator("availability_priorities", mode="before")
    @classmethod
    def _validate_availability_priorities(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return AVAILABILITY_KINDS
            value = json.loads(text) if text.startswith("[") else [part.strip() for part in re.split(r"[;,]", text)
                                                                  if part.strip()]
        if not isinstance(value, (list, tuple)):
            raise ValueError("availability_priorities must be a list of message kinds")
        items = tuple(value)
        if len(items) != len(AVAILABILITY_KINDS) or set(items) != set(AVAILABILITY_KINDS):
            raise ValueError(f"availability_priorities must list each of {', '.join(AVAILABILITY_KINDS)} exactly once")
        if items[0] != "cancel":
            raise ValueError("availability_priorities must start with 'cancel'")
        return items

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

    @field_validator("assets_dir", "file_tools_root", "integration_marker", "antigravity_kill_switch",
                     "antigravity_lock_file", mode="before")
    @classmethod
    def _empty_path_is_unset(cls, value):
        # An empty variable (``INTEGRATION_MARKER=``) means "not set", not ``Path("")``, which is the current folder.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _resolve_paths(self):
        self.local_dir = Path(self.local_dir).resolve()
        self.data_dir = Path(self.data_dir or self.local_dir / "data").resolve()
        self.db_path = Path(self.db_path or self.data_dir / "multiagent.db").resolve()
        self.runs_dir = Path(self.runs_dir or self.data_dir / "runs").resolve()
        self.assets_dir = Path(self.assets_dir or self.data_dir / "assets").resolve()
        self.project_commands_file = Path(self.project_commands_file or self.local_dir / "project_commands.toml").resolve()
        if self.file_tools_root is not None:
            self.file_tools_root = Path(self.file_tools_root).expanduser().resolve()
        self.executor_workdir_root = Path(self.executor_workdir_root or self.local_dir / "executor_runs").resolve()
        self.integration_marker = Path(self.integration_marker
                                       or self.data_dir / "watchers" / "integration.lock").expanduser().resolve()
        self.gui_token_file = Path(self.gui_token_file or Path.home() / ".multiagent" / "gui-token").expanduser().resolve()
        self.antigravity_kill_switch = Path(self.antigravity_kill_switch
                                            or self.data_dir / "antigravity-off").expanduser().resolve()
        self.antigravity_lock_file = Path(self.antigravity_lock_file
                                          or self.data_dir / "antigravity-run.lock").expanduser().resolve()
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
