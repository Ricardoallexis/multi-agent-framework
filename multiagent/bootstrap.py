from __future__ import annotations

from dataclasses import dataclass

from .adapters.fake import FakeAdapter
from .adapters.gemini import GeminiAdapter
from .adapters.ollama import OllamaAdapter
from .adapters.openai_cloud import OpenAIAdapter
from .artifacts import ArtifactWriter
from .asset_service import AssetService
from .brand_service import BrandService
from .catalog import Catalog
from .config import Settings, get_settings
from .db.database import Database
from .db.store import Store
from .model_router import ModelRouter
from .prompts import PromptManager
from .run_service import RunService
from .tools.mock import register_mock_tools
from .tools.registry import ToolRegistry
from .workflow_engine import WorkflowEngine
from .workflows import WorkflowCatalog
from .worker import LocalWorker


@dataclass
class System:
    settings: Settings
    db: Database
    store: Store
    catalog: Catalog
    run_service: RunService
    brand_service: BrandService
    asset_service: AssetService
    engine: WorkflowEngine
    worker: LocalWorker
    adapters: dict
    # Tools workflow steps may declare (built-in synthetic tools; a host may register MCP or its own before use).
    tool_registry: ToolRegistry | None = None


def build_system(settings: Settings | None = None, *, dry_run: bool = False, adapter_overrides: dict | None = None) -> System:
    settings = settings or get_settings()
    settings.ensure_directories()
    db = Database(settings)
    db.migrate()
    store = Store(db)
    store.backfill_orchestration_metadata()
    catalog = Catalog(settings)
    prompts = PromptManager(settings)
    adapters = {
        "ollama": OllamaAdapter(settings),
        "gemini": GeminiAdapter(settings),
        "openai": OpenAIAdapter(settings),
        "fake": FakeAdapter(),
    }
    if adapter_overrides:
        adapters.update(adapter_overrides)
    router = ModelRouter(catalog, adapters, dry_run=dry_run or settings.mock_mode)
    tool_registry = ToolRegistry()
    register_mock_tools(tool_registry)
    engine = WorkflowEngine(
        store=store, catalog=catalog, prompts=prompts, workflows=WorkflowCatalog(settings),
        router=router, artifacts=ArtifactWriter(settings), settings=settings,
        approval_timeout_seconds=settings.approval_timeout_seconds,
        tool_registry=tool_registry,
    )
    run_service = RunService(settings, store, engine=engine)
    brand_service = BrandService(store, catalog, prompts, router)
    asset_service = AssetService(settings, store)
    worker = LocalWorker(store, engine, settings.worker_poll_seconds)
    return System(settings, db, store, catalog, run_service, brand_service, asset_service, engine, worker, adapters,
                  tool_registry)
