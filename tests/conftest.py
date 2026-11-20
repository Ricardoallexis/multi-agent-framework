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


@pytest.fixture(autouse=True)
def _no_real_skillspector(monkeypatch):
    """SKL-T02: no test looks for the user's SkillSpector; a test passes its own path or env."""
    from multiagent import bundle_scan
    real = bundle_scan.default_python
    monkeypatch.setattr(bundle_scan, "default_python", lambda env=None: None if env is None else real(env))
