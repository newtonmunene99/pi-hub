# Contributing to pi-hub

Thanks for helping! Bug reports, new integrations, docs fixes and design polish are all welcome.

## Ground rules

- **No runtime dependencies.** pi-hub runs on the Python standard library and plain HTML/CSS/JS with no build step. That's what keeps it tiny and easy to run on a Pi. Dev tools (like `ruff`) are fine.
- **Never commit secrets.** `config.json`, `history.json` and `.env` are git-ignored; use `config.example.json` and placeholder values in tests and docs. CI runs a secret scanner on every push.
- Be kind. This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

## Getting set up

You need Python 3.10+ and nothing else.

```sh
git clone https://github.com/newtonmunene99/pi-hub && cd pi-hub
PIHUB_DEMO=1 PIHUB_PORT=8000 python3 -m pihub     # fake data, no services needed
```

Open <http://localhost:8000>. The UI lives in `pihub/static/` (`index.html`, `app.css`, `app.js`) — edit and refresh, there's nothing to build.

To run against real services, copy `config.example.json` to `config.json`, edit it, and run `PIHUB_CONFIG=./config.json python3 -m pihub`.

### Tests and linting

```sh
python3 -m unittest discover -s tests -t .
pip install ruff   # once
ruff check . && ruff format --check .
```

CI runs both on Python 3.10–3.14, builds the Docker image, and scans for secrets.

## Project layout

```
pihub/
  __main__.py      entry point, environment variables
  server.py        HTTP server, JSON API, security headers
  hub.py           polling loop, 24h history, /api/state
  config.py        config.json loading, validation, secret handling
  integrations.py  one class per supported app (kind)
  system.py        CPU / memory / temperature / fan / disk stats
  schedule.py      "next run" sidebar entries
  demo.py          fake data for PIHUB_DEMO=1
  static/          the UI
tests/             unittest suites (stdlib only)
```

## Adding an integration

An integration teaches pi-hub to show a useful status line for one app. Most are ~20 lines.

1. In `pihub/integrations.py`, subclass `Integration`:

   ```python
   class Jellyfin(Integration):
       label = "Jellyfin"
       default_port = "8096"
       probe_path = "/health"  # cheap URL that answers when the app is up
       key_hint = "Dashboard → API Keys"  # where users find the key

       def stats(self, client, ctx):
           if not client.api_key:
               return None  # no key: card shows "Online · 12 ms"
           sessions = client.json("/Sessions", {"X-Emby-Token": client.api_key})
           if sessions is None:
               return None
           playing = [s for s in sessions if s.get("NowPlayingItem")]
           return plural(len(playing), "stream")

       def info(self, client):
           info = client.json("/System/Info/Public") or {}
           return {"version": info.get("Version"), "extra": {}}
   ```

2. Register it in `KINDS` at the bottom of the file: `"jellyfin": Jellyfin(),`.
3. Add a test in `tests/test_integrations.py`: put realistic sample responses in `ROUTES` (copy them from the app's API, with any personal data removed) and assert on the status line.
4. Add the kind to the tables in `README.md` and `docs/configuration.md`.

Tips:

- `stats` runs every poll (default 15 s) — keep it to one or two cheap requests. Put anything slow in `info`, which runs every 10 minutes; its `extra` dict is available to `stats` through `ctx.cache[client.svc["id"]]`.
- Return `None` on any failure; never raise for expected errors.
- To feed the sidebar, append to `ctx.playing` or `ctx.downloads` (see `Plex` and `QBittorrent`).
- Always send secrets in headers when the app allows it, not in URLs.

## Pull requests

1. Open an issue first for anything big, so we can agree on the approach.
2. Keep PRs focused; one feature or fix each.
3. Make sure tests and `ruff` pass, and add tests for new behaviour.
4. For UI changes, include before/after screenshots (desktop and phone). `PIHUB_DEMO=1` gives consistent data for these.
5. Add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md).

## Releasing (maintainers)

1. Bump the version in `pihub/__init__.py` and `pyproject.toml` (a test checks they match) and move the changelog entries under the new version.
2. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`.
3. The release workflow builds multi-arch images (`linux/amd64`, `linux/arm64`, `linux/arm/v7`), pushes them to `ghcr.io/newtonmunene99/pi-hub`, and creates a GitHub release.
