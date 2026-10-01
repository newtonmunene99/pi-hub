# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- qBittorrent: a wrong username/password is no longer retried on every poll, which could get pi-hub's IP banned by qBittorrent. The card now says "Login failed — check username and password".
- An invalid `pollSeconds` value now gives a clear config error instead of a crash.
- The download speed in the sidebar could occasionally lose an update when qBittorrent and SABnzbd were polled at the same time.

### Changed

- Integration API: `stats(client, info)` now *returns* its findings (a string, or a `Report` with sidebar data) instead of writing to a shared context, and integrations declare the sidebar sections they fill with `provides`. New media or download integrations get the sidebar without changes to the hub. See CONTRIBUTING.md.

## [0.1.0] - 2026-10-01

### Added

- Dashboard with pinned tiles, category filters, search-to-launch and per-service details (uptime, version, 24 h response times).
- Integrations: Plex, Sonarr, Radarr, Prowlarr, Bazarr, qBittorrent, SABnzbd, Kometa, and a generic up/down check.
- Host stats: CPU temperature, load, memory, uptime, any number of disks; Argon ONE and hwmon fan readings.
- Sidebar: now playing, download queue, scheduled jobs.
- Settings page with write-only API keys, `env:`/`file:` secret references, optional password and read-only mode.
- Phone layout, demo mode, multi-arch Docker image.
