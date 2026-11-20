from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture(autouse=True)
def _fresh_gemini_verification(monkeypatch):
    """CUO-T02: the Gemini check lives in process memory; every test starts «Sin comprobar»."""
    from multiagent import model_checks
    monkeypatch.setattr(model_checks, "_gemini", {"verification": "unverified", "verified_at": None, "resets_at": None})
