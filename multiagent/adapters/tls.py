# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Which certificates the provider clients trust (ARR3-T07).

``PROVIDER_TLS_TRUST`` has three values: ``certifi`` (the bundle of the clients, today's behaviour), ``system`` (the store
of the operating system, through ``ssl.create_default_context()``) and ``file:<path>`` (a PEM file of the person, only from
``.local/.env``). The GUI writes ``provider_tls_trust`` in ``gui-settings.json``; that key wins over the environment.
"""
from __future__ import annotations

import ssl
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Settings

TLS_TRUST_VALUES = ("certifi", "system")
FILE_PREFIX = "file:"
SETTING_KEY = "provider_tls_trust"
_MAX_CHAIN = 10


def parse_tls_trust(value: str) -> str:
    """The value, trimmed, when it is ``certifi``, ``system`` or ``file:<path>``; ``ValueError`` otherwise."""
    text = value.strip() if isinstance(value, str) else ""
    if text in TLS_TRUST_VALUES:
        return text
    if text.startswith(FILE_PREFIX) and text[len(FILE_PREFIX):].strip():
        return FILE_PREFIX + text[len(FILE_PREFIX):].strip()
    raise ValueError("PROVIDER_TLS_TRUST must be certifi, system or file:<path>")


def effective_tls_trust(settings: Settings) -> str:
    """The key of ``gui-settings.json`` first, then ``PROVIDER_TLS_TRUST``, then ``certifi``."""
    from ..services.gui_settings import load_file   # lazy: the services import the adapters
    local_dir = getattr(settings, "local_dir", None)
    written = load_file(Path(local_dir)).get(SETTING_KEY) if local_dir else None
    if isinstance(written, str) and written in TLS_TRUST_VALUES:
        return written
    try:
        return parse_tls_trust(getattr(settings, "provider_tls_trust", "certifi"))
    except ValueError:
        return "certifi"


def provider_ssl_context(trust: str) -> ssl.SSLContext | None:
    """``None`` for ``certifi`` (the clients keep their own configuration); a context for ``system`` and ``file:``."""
    trust = parse_tls_trust(trust)
    if trust == "certifi":
        return None
    if trust == "system":
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=trust[len(FILE_PREFIX):])


def is_tls_failure(error: BaseException | str | None) -> bool:
    """Whether ``error`` or one of its causes is a certificate verification failure (SDKs wrap the ``ssl`` error)."""
    if error is None:
        return False
    if isinstance(error, str):
        return "CERTIFICATE_VERIFY_FAILED" in error.upper()
    seen: set[int] = set()
    current: BaseException | None = error
    for _ in range(_MAX_CHAIN):
        if current is None or id(current) in seen:
            return False
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if "CERTIFICATE_VERIFY_FAILED" in f"{current} {current!r}".upper():
            return True
        current = current.__cause__ or current.__context__
    return False
