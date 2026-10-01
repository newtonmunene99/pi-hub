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
       # A cheap URL that answers whenever the app is up.
       probe_path = "/health"
       # Where users find the key; shown in the settings page.
       key_hint = "Dashboard → API Keys"
       # Sidebar sections this kind can fill.
       provides = frozenset({"playing"})

       def stats(self, client, info):
           # Without a key the card falls back to "Online · 12 ms".
           if not client.api_key:
               return None
           sessions = client.json("/Sessions", {"X-Emby-Token": client.api_key})
           if sessions is None:
               return None
           playing = [
               {
                   "title": s["NowPlayingItem"]["Name"],
                   "who": s.get("UserName", "?"),
                   "how": "Direct play",
                   "pct": 0,
                   "state": "playing",
               }
               for s in sessions
               if s.get("NowPlayingItem")
           ]
           return Report(status=plural(len(playing), "stream"), playing=playing)

       def info(self, client):
           data = client.json("/System/Info/Public") or {}
           return {"version": data.get("Version"), "extra": {}}
   ```

2. Register it in `KINDS` at the bottom of the file: `"jellyfin": Jellyfin(),`.
3. Add a test in `tests/test_integrations.py`: put realistic sample responses in `ROUTES` (copy them from the app's API, with any personal data removed) and assert on the status line.
4. Add the kind to the tables in `README.md` and `docs/configuration.md`.

Tips:

- `stats(client, info)` runs every poll (default 15 s) in a worker thread. Keep it to one or two cheap requests, and **return** what you found rather than storing it anywhere: a string for just a status line, or a `Report` when you also have sidebar data. The hub merges all reports after the workers finish.
- Put anything slow in `info`, which runs every 10 minutes. Its last result is passed to `stats` as `info` (see how `Plex` reads the library size from `info["extra"]`).
- Set `provides` to `{"playing"}` and/or `{"downloads"}` when your `Report` fills those sidebar sections; the dashboard shows a section whenever any configured service provides it.
- Apps checked without HTTP (like Kometa, which writes a log file) set `needs_port = False` and implement `local_status(svc)` instead of `stats`.
- Return `None` on any failure; never raise for expected errors. An unexpected exception is caught and logged, and the card falls back to "Online".
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
