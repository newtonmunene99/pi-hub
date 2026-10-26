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

## Signing in

pi-hub has one admin account. Signing in protects everything: the dashboard, the settings and host actions. Only the `/api/health` check stays open.

There are two ways to create the account:

- **In the browser (default).** With `PIHUB_USERNAME` and `PIHUB_PASSWORD` unset, pi-hub logs a one-time setup code on start:

  ```
  WARNING pihub.auth: No admin account yet. Open pi-hub and enter setup code 3f9a1c07 to create one.
  ```

  Open pi-hub, enter the code (`docker logs pi-hub` shows it), and choose a username and password (8+ characters). The account is saved, as a salted hash, in `auth.json` next to `config.json` (set `PIHUB_AUTH_FILE` to put it elsewhere). You can change the password later under **Settings → Account**. After 50 wrong codes, pi-hub logs a new one.
- **From the environment.** Set both `PIHUB_USERNAME` and `PIHUB_PASSWORD`. `PIHUB_PASSWORD` accepts `env:NAME` and `file:/run/secrets/...` like other secrets. The password can't be changed in the browser then. Setting only one of the two is an error and pi-hub won't start.

Sessions last 30 days. Restarting pi-hub signs everyone out. Changing the password signs out every other device.

After 5 wrong passwords in a row from one address, that address must wait a minute. Behind a reverse proxy, every visitor comes from the proxy's address, so wrong guesses lock everyone out for that minute. pi-hub ignores `X-Forwarded-For` on purpose, because any client can fake it.

**Forgot the password?** For a browser-created account, delete `auth.json` and restart pi-hub. It logs a new setup code. For an environment account, change `PIHUB_PASSWORD`.

**Behind an authenticating proxy** (Authelia, Authentik, oauth2-proxy…), you can set `PIHUB_AUTH=off` to skip pi-hub's own sign-in. pi-hub then logs a warning, because anyone who can reach it directly can use it. If your proxy serves pi-hub over HTTPS, the session cookie still works; pi-hub can't tell, so it doesn't mark the cookie `Secure` itself.

## Host actions: restart and Claude Code (optional)

With the **host agent** running, a **Host** link appears in the header. It opens a panel where you can:

- **Restart** the machine (tap twice to confirm).
- **Start [Claude Code](https://claude.com/claude-code)** in any folder under your home directory. It runs `claude remote-control`, so you can carry on from the Claude mobile app or claude.ai/code without SSH. Sessions started here are listed with *Open* and *Stop* buttons.

pi-hub usually runs in a container, which can't do either of these itself. The agent is a small part of pi-hub that runs **on the host, as your user**, and listens on a unix socket that you mount into the container. Without it, nothing changes.

**1. Run the agent on the host** (Python 3.11+, from a checkout of this repo). Save this as `/etc/systemd/system/pihub-agent.service`, replacing `you` and the path:

```ini
[Unit]
Description=pi-hub host agent
After=network-online.target

[Service]
User=you
ExecStart=/usr/bin/python3 -m pihub.hostagent
WorkingDirectory=/home/you/pi-hub
RuntimeDirectory=pihub-agent
RuntimeDirectoryPreserve=yes
# Keep Claude sessions running when the agent restarts.
KillMode=process
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl daemon-reload && sudo systemctl enable --now pihub-agent
```

**2. Mount the socket's folder into the container** and point pi-hub at it:

```yaml
    environment:
      - PIHUB_AGENT_SOCKET=/run/pihub-agent/agent.sock
    volumes:
      - /run/pihub-agent:/run/pihub-agent
```

The socket is only accessible to its owner and group, and the image runs as UID 1000. If your host user isn't UID 1000, set `user: "<uid>:<gid>"` on the container.

**Requirements on the host:**

- *Restart* runs `sudo -n systemctl reboot`, so your user needs passwordless sudo for that command, e.g. `you ALL=(root) NOPASSWD: /usr/bin/systemctl reboot` in `/etc/sudoers.d/pihub`.
- *Claude Code* must be installed and signed in for your user. `claude remote-control` only starts in folders you have trusted: run `claude` once in a project folder and accept the prompt, and its subfolders are trusted too. Claude never saves trust for the home directory itself, so pick a folder inside it. Only one Remote Control session can serve a folder at a time. If Claude refuses to start, the panel shows its error.

| Agent variable | Default | |
|---|---|---|
| `PIHUB_AGENT_SOCKET` | `/run/pihub-agent/agent.sock` | socket path |
| `PIHUB_AGENT_ROOT` | your home directory | the folder picker can't go above this |
| `PIHUB_AGENT_STATE` | `~/.local/state/pihub` | session list and logs |
| `PIHUB_AGENT_CLAUDE` | `claude` | Claude Code executable; empty turns sessions off |
| `PIHUB_AGENT_REBOOT` | `sudo -n systemctl reboot` | reboot command; empty turns restart off |

**Security:** the panel is only available after [signing in](#signing-in). With sign-in turned off (`PIHUB_AUTH=off`), anyone who can reach pi-hub can use these buttons, so keep it behind an authenticating proxy. `PIHUB_READONLY=1` turns host actions off. The endpoints only accept JSON, so another website can't trigger them from your browser. Sessions run under *your* Claude account, so only you can work in them, but anyone who can use the panel can start or stop them.

## Environment variables

See the table in the [README](../README.md#environment-variables).
