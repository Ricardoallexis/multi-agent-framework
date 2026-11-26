from __future__ import annotations

from dataclasses import dataclass

from .adapters.fake import FakeAdapter
from .adapters.gemini import GeminiAdapter
from .adapters.ollama import OllamaAdapter
from .adapters.openai_cloud import OpenAIAdapter
from .artifacts import ArtifactWriter
from .asset_service import AssetService
from .backends import BackendContext, ExecutionBackend, build_backend
from .backends.native import NativeBackend
from .brand_service import BrandService
from .catalog import Catalog
from .config import Settings, get_settings
from .db.database import Database
from .db.store import Store
from .model_router import ModelRouter
from .prompts import PromptManager
from .run_service import RunService
from .tools.files import register_file_tools
from .tools.mock import register_mock_tools
from .tools.registry import ToolRegistry
from .workflow_engine import WorkflowEngine
from .workflows import WorkflowCatalog
from .worker import LocalWorker, concurrency_limit_source


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
    backend: ExecutionBackend
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
    if settings.file_tools_root is not None:
        register_file_tools(tool_registry, settings.file_tools_root)
    workflows = WorkflowCatalog(settings)
    artifacts = ArtifactWriter(settings)
    context = BackendContext(
        store=store, catalog=catalog, prompts=prompts, workflows=workflows,
        router=router, artifacts=artifacts, settings=settings,
        approval_timeout_seconds=settings.approval_timeout_seconds,
        tool_registry=tool_registry,
    )
    backend = build_backend(settings.execution_backend, context)
    engine = backend if isinstance(backend, NativeBackend) else WorkflowEngine(
        store=store, catalog=catalog, prompts=prompts, workflows=workflows,
        router=router, artifacts=artifacts, settings=settings,
        approval_timeout_seconds=settings.approval_timeout_seconds,
        tool_registry=tool_registry,
    )
    run_service = RunService(settings, store, engine=backend)
    brand_service = BrandService(store, catalog, prompts, router)
    asset_service = AssetService(settings, store)
    worker = LocalWorker(
        store, backend, settings.worker_poll_seconds, concurrency=settings.worker_concurrency,
        max_active_total=settings.worker_max_active_total,
        max_active_per_project=settings.worker_max_active_per_project,
        lease_seconds=settings.worker_lease_seconds,
        shutdown_wait_seconds=settings.worker_shutdown_wait_seconds,
    )
    system = System(settings, db, store, catalog, run_service, brand_service, asset_service, engine, backend, worker,
                    adapters, tool_registry)
    # PAR-T03: a worker that runs alone (``multiagent worker``) follows the concurrency mode saved in the GUI settings.
    # The services that host the GUI replace this source with their own (``RunServices``) before the worker starts.
    worker.limit_source = _saved_mode_limit_source(system)
    return system


def _saved_mode_limit_source(system: "System"):
    """The cap of the saved concurrency mode, or None while no mode is saved (the worker then keeps its static limits).

    The services that read the usage and the settings are built only when a mode is saved, and only once.
    """
    from .services.gui_settings import load_file

    holder: dict = {}

    def source():
        if "source" not in holder:
            if "concurrency" not in load_file(system.settings.local_dir):
                return None
            from .services import ApplicationServices

            holder["source"] = concurrency_limit_source(ApplicationServices(system))
        return holder["source"]()

    return source
