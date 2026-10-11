# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""``python -m multiagent.gui`` and ``multiagent gui``: start the local server and open the GUI with its token."""
from __future__ import annotations

import argparse
import os
import threading
import webbrowser

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def run(*, port: int = 0, real: bool = False, no_browser: bool = False) -> None:
    import uvicorn

    from ..api import create_app
    from ..bootstrap import build_system
    from ..cli import _check_port_free
    from ..config import Settings
    from .token import check_token_location, ensure_token

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
    app = create_app(build_system(settings))

    url = f"http://{f'[{host}]' if ':' in host else host}:{port}/gui/#token={token}"
    print(f"Multi-Agent Framework GUI: {url}")
    print(f"Token file: {settings.gui_token_file}")
    print("Mode: " + ("REAL models (provider calls may cost money)" if real else "Mock (synthetic results)"))
    if not settings.gui_session_roots:
        print("Sessions: no folder is allowed yet; set GUI_SESSION_ROOTS to choose which sessions the viewer shows.")
    print("Press Ctrl+C to stop.")
    if not no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main(argv: list[str] | None = None) -> None:
    from ..cli import add_gui_arguments  # the options are defined once, for `multiagent gui` too

    parser = argparse.ArgumentParser(prog="python -m multiagent.gui", description="Start the local GUI")
    add_gui_arguments(parser)
    args = parser.parse_args(argv)
    try:
        run(port=args.port, real=args.real, no_browser=args.no_browser)
    except RuntimeError as exc:  # a refused launch (host, token folder, busy port): the message, no traceback
        raise SystemExit(f"ERROR: {exc}") from None


if __name__ == "__main__":
    main()
