# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""``python -m multiagent.gui``: start the local server and open the GUI with its token."""
from __future__ import annotations

import argparse
import os
import threading
import webbrowser


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m multiagent.gui", description="Start the local GUI")
    parser.add_argument("--port", type=int, default=0, help="Port (default: app_port from settings)")
    parser.add_argument("--real", action="store_true", help="Use real models (default: Mock mode)")
    parser.add_argument("--no-browser", action="store_true", help="Print the URL without opening the browser")
    args = parser.parse_args(argv)

    import uvicorn

    from ..api import create_app
    from ..bootstrap import build_system
    from ..cli import _check_port_free
    from ..config import Settings
    from .token import ensure_token

    if not args.real:
        os.environ["MOCK_MODE"] = "true"  # synthetic fixtures, no model calls or API keys
    settings = Settings()
    settings.ensure_directories()
    host = settings.app_host
    port = args.port or settings.app_port
    _check_port_free(host, port)
    token = ensure_token(settings.gui_token_file)
    app = create_app(build_system(settings))

    url = f"http://{f'[{host}]' if ':' in host else host}:{port}/gui/#token={token}"
    print(f"Multi-Agent Framework GUI: {url}")
    print(f"Token file: {settings.gui_token_file}")
    print("Mode: " + ("REAL models (provider calls may cost money)" if args.real else "Mock (synthetic results)"))
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
