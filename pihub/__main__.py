"""Entry point: ``python -m pihub``.

Environment variables:
  PIHUB_CONFIG    path to config.json            (default /config/config.json)
  PIHUB_HISTORY   path for uptime history        (default next to the config)
  PIHUB_HOST      bind address                   (default 0.0.0.0)
  PIHUB_PORT      port                           (default 8000)
  PIHUB_USERNAME  admin username (with PIHUB_PASSWORD); unset, the account is created in the browser
  PIHUB_PASSWORD  admin password; accepts env:NAME and file:/path references
  PIHUB_AUTH      "off" to turn sign-in off (e.g. behind an authenticating reverse proxy)
  PIHUB_AUTH_FILE where a browser-created account is stored (default auth.json next to the config)
  PIHUB_READONLY  "1" to disable editing settings in the UI
  PIHUB_AGENT_SOCKET host agent socket: enables the restart and Claude buttons (see hostagent.py)
  PIHUB_DEMO      "1" to serve fake data (no config needed) - for UI work and screenshots
  PIHUB_LOG_LEVEL DEBUG, INFO, WARNING or ERROR  (default INFO)
"""

import contextlib
import logging
import os
import sys
import threading

from . import __version__
from .auth import AuthError, auth_from_env
from .config import ConfigError
from .hostagent import AgentClient
from .server import HubAPI, serve


def main() -> None:
    """Reads the environment, starts polling (unless in demo mode) and serves HTTP."""
    logging.basicConfig(
        level=os.environ.get("PIHUB_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("pihub")
    host = os.environ.get("PIHUB_HOST", "0.0.0.0")
    port = int(os.environ.get("PIHUB_PORT", os.environ.get("PORT", "8000")))
    readonly = os.environ.get("PIHUB_READONLY", "") in ("1", "true", "yes")

    demo = os.environ.get("PIHUB_DEMO", "") in ("1", "true", "yes")
    config_path = os.environ.get("PIHUB_CONFIG", "/config/config.json")
    try:
        auth = auth_from_env(os.environ, config_path, demo=demo)
    except AuthError as e:
        logger.error("%s", e)
        sys.exit(2)

    hub: HubAPI
    if demo:
        from .demo import DemoHub

        hub = DemoHub()
    else:
        from .hub import Hub

        history_path = os.environ.get("PIHUB_HISTORY", os.path.join(os.path.dirname(config_path), "history.json"))
        try:
            real_hub = Hub(config_path, history_path)
        except ConfigError as e:
            logger.error("%s", e)
            sys.exit(2)
        threading.Thread(target=real_hub.run_forever, daemon=True).start()
        hub = real_hub

    socket_path = os.environ.get("PIHUB_AGENT_SOCKET", "")
    agent = AgentClient(socket_path) if socket_path else None
    httpd = serve(hub, host, port, auth, readonly, agent)
    mode = " (demo)" if os.environ.get("PIHUB_DEMO") else ""
    logger.info("pi-hub %s%s listening on http://%s:%s", __version__, mode, host, port)
    with contextlib.suppress(KeyboardInterrupt):
        httpd.serve_forever()


if __name__ == "__main__":
    main()
