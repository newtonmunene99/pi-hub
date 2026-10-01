# Configuration

pi-hub reads one JSON file, `config.json` (path set by `PIHUB_CONFIG`, default `/config/config.json`). Changes made on disk are picked up on the next poll; changes made in the **Settings** page are written back to the same file.

A complete example: [`config.example.json`](../config.example.json).

## Top level

| Key | Type | Default | Description |
|---|---|---|---|
| `title` | string | `"pi-hub"` | Name shown in the header and browser tab. |
| `pollSeconds` | number | `15` | How often services are checked (minimum 5). |
| `categories` | list of strings | `["Media","Automation","Downloads","System"]` | Filter chips, in order. Services with an unknown category go in the last one. |
| `system` | object | `{}` | Host stats — see below. |
| `schedule` | list | `[]` | Scheduled jobs for the sidebar — see below. |
| `services` | list | `[]` | The cards — see below. |

## `services`

| Key | Required | Description |
|---|---|---|
| `name` | yes | Display name. |
| `kind` | no | Integration: `generic` (default), `plex`, `sonarr`, `radarr`, `prowlarr`, `bazarr`, `qbittorrent`, `sabnzbd`, `kometa`. |
| `port` | for HTTP kinds | Port the service listens on. Also the big number on the card. |
| `category` | no | One of `categories`. |
| `host` | no | Where pi-hub reaches the service. Default `127.0.0.1` (use with host networking). |
| `scheme` | no | `http` (default) or `https`. |
| `basePath` | no | URL base if the app runs under a sub-path, e.g. `/radarr`. Used for API calls *and* links. |
| `linkPath` | no | Extra path for the link only, e.g. `/web` for Plex. |
| `url` | no | Full link override, e.g. `https://radarr.example.com` behind a reverse proxy. The status checks still use `host`/`port`. |
| `description` | no | Small text under the name. |
| `icon` | no | 1–2 characters for the badge. Defaults to the first letter. |
| `pinned` | no | `true` shows a big tile in the *Pinned* row. |
| `apiKey` | no | API key / token used for the status line. See [Secrets](#secrets). |
| `username`, `password` | no | qBittorrent Web UI login (not needed if "Bypass authentication for clients on localhost" applies). |
| `log` | kometa | Path to Kometa's `meta.log`. |

Links open on the **hostname you used to reach pi-hub** (so `http://pi.local:8000` links to `http://pi.local:7878`), unless `url` is set.

### Where to find API keys

| Kind | Where |
|---|---|
| `sonarr`, `radarr`, `prowlarr` | Settings → General → API Key |
| `bazarr` | Settings → General → Security → API Key |
| `sabnzbd` | Config → General → API Key |
| `plex` | An `X-Plex-Token` — see Plex's support article *"Finding an authentication token / X-Plex-Token"* |

## Secrets

`apiKey` and `password` accept:

- a literal value: `"apiKey": "0123abcd…"` — stored in `config.json` (saved with `0600` permissions);
- an environment variable: `"apiKey": "env:RADARR_API_KEY"`;
- a file, e.g. a Docker secret: `"apiKey": "file:/run/secrets/radarr_api_key"`.

References are shown by name in the settings page; literal values are never sent to the browser. Leaving the API key field empty when saving keeps the stored key.

**Changing where a service lives drops its secrets.** If you edit `host`, `port`, `scheme`, `basePath` or `kind` in the settings page without entering the key again, the stored key is removed. This stops anyone with access to the settings page from pointing a service at their own machine to capture your keys.

## `system`

```json
"system": {
  "label": "",
  "temperature": true,
  "fan": { "type": "argon", "config": "/argononed.conf" },
  "disks": [
    { "name": "media",   "path": "/disks/media" },
    { "name": "backups", "path": "/disks/backups" }
  ]
}
```

| Key | Description |
|---|---|
| `label` | Board name on the CPU card. Detected from the device tree when running without Docker (e.g. `Pi 5`); Docker hides that file, so set it here if you want it shown. |
| `temperature` | `false` hides the CPU temperature card. |
| `fan.type` | `argon` — speed computed from an Argon ONE `argononed.conf` fan curve (mount it read-only and set `config`).<br>`hwmon` — first fan sensor in `/sys/class/hwmon` (e.g. the official Pi 5 active cooler), shown in RPM.<br>`none` — no fan info. |
| `disks` | Cards for disk usage. `path` is any directory on the disk *as seen by pi-hub* — in Docker, mount each disk read-only (`/mnt/media:/disks/media:ro`). Over 90 % full turns the card red. |

CPU load, memory and uptime are always shown when `/proc` is readable. Inside Docker these are the host's values.

## `schedule`

pi-hub doesn't run jobs; it shows when they run next.

```json
"schedule": [
  { "name": "Kometa", "at": "03:00", "service": "kometa" },
  { "name": "Config backup", "at": ["05:00", "17:00"],
    "lastRun": { "dir": "/backups", "glob": "*.tar.zst" } }
]
```

| Key | Description |
|---|---|
| `name` | Label in the sidebar. |
| `at` | `"HH:MM"` or a list of them (24-hour, container time zone — set `TZ`). |
| `service` | Optional service `id` whose details popup shows this as *Next run*. |
| `lastRun` | Optional: shows *"Last today 05:00 · 85 MB"* from the newest file matching `glob` in `dir` (hover the row). |

## Networking

pi-hub needs to reach each service, and it reads host stats from `/proc` and `/sys`.

- **Host networking (simplest, recommended on a Pi):** `network_mode: host`, keep `host` at `127.0.0.1`.
- **Bridge networking:** publish `8000:8000` and set each service's `host` to your server's LAN IP (or a container name on a shared Docker network).
- **Other machines:** set `host` (and `scheme`/`port`) per service.

## Environment variables

See the table in the [README](../README.md#environment-variables).
