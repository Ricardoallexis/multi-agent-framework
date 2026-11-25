# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""``python -m multiagent.gui`` and ``multiagent gui``: start the local server and open the GUI with its token."""
from __future__ import annotations

import argparse
import os
import secrets
import threading
import webbrowser
from collections.abc import Iterable, Mapping
from pathlib import Path

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def serve(app, *, host: str, port: int, stopped: str) -> None:
    """Serve ``app`` until Ctrl+C, then print ``stopped``.

    The first Ctrl+C sets the stop flag (``events.request_shutdown``), so open SSE streams end at once;
    uvicorn then closes the rest, waiting at most 3 s. The Ctrl+C that uvicorn re-raises when its shutdown
    is clean is swallowed here, so there is no traceback.
    """
    import uvicorn

    from .events import request_shutdown, reset_shutdown

    class StoppableServer(uvicorn.Server):
        def handle_exit(self, sig, frame) -> None:
            request_shutdown()
            super().handle_exit(sig, frame)

    reset_shutdown()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", timeout_graceful_shutdown=3)
    try:
        StoppableServer(config).run()
    except KeyboardInterrupt:
        pass
    print(stopped)


def run(*, port: int = 0, real: bool = False, no_browser: bool = False,
        bundles: Mapping[str, str | Path] | None = None, trust_functions: Iterable[str] = (),
        launcher: bool = False) -> None:
    """Start the server; ``bundles`` maps a name to a trusted definition-bundle folder.

    ``launcher`` is the double-click start (INI-T01): the ``models`` key of ``gui-settings.json`` picks Mock
    (``mock``, or no file) or real models (``real``). ``real=True`` still wins.

    ``trust_functions`` names the registered or installed bundles whose ``functions.py`` may be imported after the
    scan (PAQ-T03). An unknown name stops the start with ``ValueError``.
    """
    from ..api import create_app
    from ..bootstrap import build_system
    from ..cli import _check_port_free
    from ..config import Settings
    from ..services import ApplicationServices
    from .token import check_token_location, ensure_token

    if launcher and not real:
        from ..services.gui_settings import load_file

        real = load_file(Settings().local_dir).get("models") == "real"
    if not real:
        os.environ["MOCK_MODE"] = "true"  # synthetic fixtures, no model calls or API keys
    settings = Settings()
    host = settings.app_host
    if host.strip().lower().strip("[]") not in LOOPBACK_HOSTS:
        raise RuntimeError(
            f"The GUI serves local files and tokens, so it only starts on a loopback address "
            f"({', '.join(LOOPBACK_HOSTS)}); APP_HOST is {host!r}. Set APP_HOST=127.0.0.1.")
    check_token_location(settings.gui_token_file, allow_outside_home=settings.gui_token_outside_home)
    settings.ensure_directories()
    port = port or settings.app_port
    _check_port_free(host, port)
    token = ensure_token(settings.gui_token_file)
    services = ApplicationServices(build_system(settings), bundles=bundles, trust_functions=tuple(trust_functions))
    app = create_app(services=services)
    approval_code = secrets.token_urlsafe(32)
    app.state.approval_sessions.bootstrap_code = approval_code

    base_url = f"http://{f'[{host}]' if ':' in host else host}:{port}/gui/"
    url = f"{base_url}#token={token}&approval={approval_code}"
    print(f"Multi-Agent Framework GUI: {url if no_browser else base_url}")
    print(f"Token file: {settings.gui_token_file}")
    print("Mode: " + ("REAL models (provider calls may cost money)" if real else "Mock (synthetic results)"))
    if launcher and not real:
        print("To use real models: Inicio › Pasar a modelos reales")
    registered = app.state.services.bundle_names()  # the --bundle options and the installed packages (PAQ-T01)
    if registered:
        print(f"Bundles: {', '.join(registered)}")
    if not settings.gui_session_roots:
        print("Sessions: no folder is allowed yet; set GUI_SESSION_ROOTS to choose which sessions the viewer shows.")
    print("Press Ctrl+C to stop.")
    if not no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    serve(app, host=host, port=port, stopped="GUI detenida.")


def main(argv: list[str] | None = None) -> None:
    from ..cli import _parse_ui_bundles, add_gui_arguments  # options and --bundle parsing are shared with the CLI

    parser = argparse.ArgumentParser(prog="python -m multiagent.gui", description="Start the local GUI")
    add_gui_arguments(parser)
    args = parser.parse_args(argv)
    try:
        run(port=args.port, real=args.real, no_browser=args.no_browser, bundles=_parse_ui_bundles(args.bundle),
            trust_functions=tuple(args.trust_functions or ()), launcher=args.launcher)
    except (RuntimeError, ValueError) as exc:  # a refused launch (host, token folder, busy port, bad --bundle): no traceback
        raise SystemExit(f"ERROR: {exc}") from None


if __name__ == "__main__":
    main()
