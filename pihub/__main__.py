"""Entry point: ``python -m pihub``.

Environment variables:
  PIHUB_CONFIG    path to config.json            (default /config/config.json)
  PIHUB_HISTORY   path for uptime history        (default next to the config)
  PIHUB_HOST      bind address                   (default 0.0.0.0)
  PIHUB_PORT      port                           (default 8000)
  PIHUB_PASSWORD  require this password to view/edit settings
  PIHUB_READONLY  "1" to disable editing settings in the UI
  PIHUB_DEMO      "1" to serve fake data (no config needed) - for UI work and screenshots
"""

import contextlib
import os
import sys
import threading

from . import __version__
from .config import ConfigError
from .server import serve


def main():
    host = os.environ.get("PIHUB_HOST", "0.0.0.0")
    port = int(os.environ.get("PIHUB_PORT", os.environ.get("PORT", "8000")))
    password = os.environ.get("PIHUB_PASSWORD", "")
    readonly = os.environ.get("PIHUB_READONLY", "") in ("1", "true", "yes")

    if os.environ.get("PIHUB_DEMO", "") in ("1", "true", "yes"):
        from .demo import DemoHub

        hub = DemoHub()
    else:
        from .hub import Hub

        config_path = os.environ.get("PIHUB_CONFIG", "/config/config.json")
        history_path = os.environ.get("PIHUB_HISTORY", os.path.join(os.path.dirname(config_path), "history.json"))
        try:
            hub = Hub(config_path, history_path)
        except ConfigError as e:
            print(f"pi-hub: {e}", file=sys.stderr)
            sys.exit(2)
        threading.Thread(target=hub.run_forever, daemon=True).start()

    httpd = serve(hub, host, port, password, readonly)
    mode = " (demo)" if os.environ.get("PIHUB_DEMO") else ""
    print(f"pi-hub {__version__}{mode} listening on http://{host}:{port}", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        httpd.serve_forever()


if __name__ == "__main__":
    main()
