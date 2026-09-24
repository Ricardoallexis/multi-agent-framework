from __future__ import annotations

from pathlib import Path

from .contracts import AssetMetadata
from .db.store import Store
from .config import Settings


class AssetService:
    """Reusable asset-ingestion logic for the CLI and future UI/API upload flows."""

    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store

    def ingest(self, source_path: str | Path, metadata: AssetMetadata):
        return self.store.add_asset(Path(source_path), metadata, self.settings.assets_dir)
