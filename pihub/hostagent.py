"""Optional host agent: lets the dashboard restart the machine and start Claude Code sessions.

pi-hub usually runs in a container, which can't reboot the host or start programs on it. The
agent runs on the host as your user (``python3 -m pihub.hostagent``, normally as a systemd
service) and listens on a unix socket that is mounted into the container. It does only these
things:

  status   which actions are available and the Claude sessions it started
  list     folders under PIHUB_AGENT_ROOT (default: your home directory)
  start    run ``claude remote-control`` in one of those folders
  stop     stop a session it started
  reboot   run PIHUB_AGENT_REBOOT (default ``sudo -n systemctl reboot``)
  update   pull a container's new image and recreate it with Docker Compose

The protocol is one JSON object per line in each direction, one request per connection.

Environment variables:
  PIHUB_AGENT_SOCKET  socket path                      (default /run/pihub-agent/agent.sock)
  PIHUB_AGENT_STATE   where session state and logs go  (default ~/.local/state/pihub)
  PIHUB_AGENT_ROOT    folder browsing root             (default your home directory)
  PIHUB_AGENT_CLAUDE  claude executable, or "" to turn sessions off  (default claude)
  PIHUB_AGENT_REBOOT  reboot command, or "" to turn rebooting off    (default sudo -n systemctl reboot)
  PIHUB_AGENT_DOCKER  docker executable, or "" to turn updates off   (default docker)
"""

import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import signal
import socket
import socketserver
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("pihub.hostagent")

MAX_REQUEST = 8_192
MAX_DIRS = 500
DEFAULT_SOCKET = "/run/pihub-agent/agent.sock"  # inside the systemd unit's RuntimeDirectory
START_GRACE = 5.0  # seconds a new session gets to fail before it counts as started
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
SESSION_URL = re.compile(r"https://claude\.ai/code[^\s\x07\x1b]*")
CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
INSPECT_TIMEOUT = 10  # seconds; under the dashboard's socket timeout so a refusal reaches it
JOB_TTL = 3600  # seconds a finished update job stays in the status
UPDATE_TIMEOUT = 900  # seconds per docker compose command; big images on a Pi pull slowly
COMPOSE_LABELS = (
    "com.docker.compose.project",
    "com.docker.compose.project.working_dir",
    "com.docker.compose.project.config_files",
    "com.docker.compose.service",
)


class AgentError(Exception):
    """A request the agent refuses; the message is shown in the UI."""


def clean_log(text: str) -> str:
    """Strips terminal escape codes from ``claude remote-control`` output."""
    return ANSI.sub("", text).replace("\r", "")


class Agent:
    """The actions themselves, independent of the socket so they can be tested directly."""

    def __init__(
        self,
        root: str,
        state_dir: str,
        claude: str = "claude",
        reboot: str = "",
        start_grace: float = START_GRACE,
        docker: str = "",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.clock = clock
        self.start_grace = start_grace
        self.root = os.path.realpath(root)
        self.state_dir = state_dir
        self.claude = shutil.which(claude) if claude else None
        self.reboot_cmd = shlex.split(reboot)
        self.docker = shutil.which(docker) if docker else None
        self.state_path = os.path.join(state_dir, "sessions.json")
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}  # container -> update job, guarded by self.lock
        os.makedirs(state_dir, mode=0o700, exist_ok=True)

    # ── folders ──
    def resolve(self, rel: str) -> str:
        """Maps a path relative to the root to an absolute folder, refusing anything outside it."""
        full = os.path.realpath(os.path.join(self.root, (rel or "").lstrip("/")))
        if not self._within(full):
            raise AgentError("That folder is outside the allowed root")
        if not os.path.isdir(full):
            raise AgentError("Folder not found")
        return full

    def _within(self, full: str) -> bool:
        return full == self.root or full.startswith(self.root + os.sep)

    def _inside(self, entry: os.DirEntry) -> bool:
        """False for a symlink that leads out of the root (it couldn't be opened anyway)."""
        return not entry.is_symlink() or self._within(os.path.realpath(entry.path))

    def relative(self, full: str) -> str:
        """The inverse of ``resolve``: "" for the root, otherwise a path without a leading slash."""
        return "" if full == self.root else os.path.relpath(full, self.root)

    def list_dirs(self, rel: str) -> dict:
        """Visible subfolders of one folder, sorted case-insensitively."""
        full = self.resolve(rel)
        try:
            with os.scandir(full) as it:
                names = [e.name for e in it if not e.name.startswith(".") and e.is_dir() and self._inside(e)]
        except PermissionError as e:
            raise AgentError("Permission denied") from e
        names.sort(key=str.lower)
        return {"path": self.relative(full), "root": self.root, "dirs": names[:MAX_DIRS], "more": len(names) > MAX_DIRS}

    # ── sessions ──
    def _load(self) -> list[dict]:
        try:
            with open(self.state_path) as f:
                return list(json.load(f))
        except (OSError, ValueError):
            return []

    def _save(self, sessions: list[dict]) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(sessions, f)
        os.replace(tmp, self.state_path)

    @staticmethod
    def _alive(pid: int) -> bool:
        """True if ``pid`` is still a ``claude remote-control`` process (pids get reused)."""
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return b"remote-control" in f.read()
        except OSError:
            return False

    def _url(self, s: dict) -> str:
        """The session link Claude printed, if any; the last one printed is the current one."""
        try:
            with open(s["log"], errors="replace") as f:
                found = SESSION_URL.findall(f.read())
        except OSError:
            return ""
        sessions = [u for u in found if "/session_" in u]
        return (sessions or found or [""])[-1]

    def sessions(self) -> list[dict]:
        """Live sessions this agent started, pruning ones that have exited."""
        with self.lock:
            all_ = self._load()
            live = [s for s in all_ if self._alive(s["pid"])]
            if len(live) != len(all_):
                self._save(live)
        return [
            {"id": s["id"], "name": s["name"], "path": s["path"], "started": s["started"], "url": self._url(s)}
            for s in live
        ]

    def start(self, rel: str, name: str = "") -> dict:
        """Starts ``claude remote-control`` in a folder and waits briefly to catch early failures."""
        if not self.claude:
            raise AgentError("Claude Code isn't installed on the host (or sessions are turned off)")
        full = self.resolve(rel)
        rel = self.relative(full)
        if any(s["path"] == rel for s in self.sessions()):
            raise AgentError("A session is already running in that folder")
        name = (name or "").strip()[:80] or os.path.basename(full) or "home"
        sid = uuid.uuid4().hex[:12]
        log_path = os.path.join(self.state_dir, f"session-{sid}.log")
        with open(log_path, "wb") as log:
            proc = subprocess.Popen(
                [self.claude, "remote-control", "--name", name],
                cwd=full,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # survives agent restarts (with KillMode=process)
            )
        # Some failures exit at once; others print "Error: ..." and linger for a minute before exiting.
        deadline = time.monotonic() + self.start_grace
        while True:
            with open(log_path, errors="replace") as f:
                lines = [ln.strip() for ln in clean_log(f.read()).splitlines() if ln.strip()]
            err = next((ln.removeprefix("Error:").strip() for ln in lines if ln.startswith("Error")), "")
            if err or proc.poll() is not None or time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        if err or proc.poll() is not None:
            if proc.poll() is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGTERM)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=5)
            os.remove(log_path)
            raise AgentError(err or (lines[-1] if lines else f"claude exited with code {proc.returncode}"))
        entry = {"id": sid, "pid": proc.pid, "name": name, "path": rel, "started": int(time.time()), "log": log_path}
        with self.lock:
            self._save([*self._load(), entry])
        logger.info("started claude session %s in %s (pid %s)", sid, full, proc.pid)
        return {"id": sid, "name": name, "path": rel, "started": entry["started"], "url": self._url(entry)}

    def stop(self, sid: str) -> None:
        """Stops a session this agent started (SIGTERM to its process group)."""
        with self.lock:
            sessions = self._load()
            match = next((s for s in sessions if s["id"] == sid), None)
            if not match:
                raise AgentError("No such session")
            if self._alive(match["pid"]):
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(match["pid"], signal.SIGTERM)
            self._save([s for s in sessions if s["id"] != sid])
        with contextlib.suppress(OSError):
            os.remove(match["log"])
        logger.info("stopped claude session %s", sid)

    # ── reboot ──
    def reboot(self) -> None:
        """Runs the reboot command; it normally returns before the machine goes down."""
        if not self.reboot_cmd:
            raise AgentError("Rebooting is turned off on the host")
        logger.warning("reboot requested from the dashboard")
        try:
            done = subprocess.run(self.reboot_cmd, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise AgentError(f"Reboot command failed: {e}") from e
        if done.returncode:
            raise AgentError((done.stderr or done.stdout).strip() or f"Reboot command exited with {done.returncode}")

    # ── container updates ──
    def update(self, container: str) -> dict:
        """Starts pulling a container's image and recreating it; returns the job at once.

        A pull can take minutes, far longer than one socket request, so the work runs in a
        thread and its state shows up in ``status``.
        """
        project, cwd, files, service = self._compose_target(container)
        with self.lock:
            if self.jobs.get(container, {}).get("state") == "running":
                raise AgentError(f"{container} is already updating")
            job = {"container": container, "state": "running", "started": int(self.clock())}
            self.jobs[container] = job
            started = dict(job)
        base = [self.docker or "docker", "compose", "-p", project]
        for f in files:
            base += ["-f", f]
        steps = [("pull", [*base, "pull", service]), ("up", [*base, "up", "-d", "--no-deps", service])]
        threading.Thread(target=self._run_update, args=(container, cwd, steps), daemon=True).start()
        logger.info("updating container %s (%s in %s)", container, service, cwd)
        return started

    def _run_update(self, container: str, cwd: str, steps: list[tuple[str, list[str]]]) -> None:
        """Runs the compose steps in order, stopping at the first failure, and records the outcome.

        The job always ends ``done`` or ``failed``, whatever goes wrong: a job stuck at
        ``running`` would refuse every later update of that container until the agent restarts.
        """
        message = ""
        try:
            message = self._compose_steps(cwd, steps)
        except Exception as e:  # anything unexpected still has to end the job
            logger.exception("updating container %s crashed", container)
            message = str(e) or type(e).__name__
        finally:
            with self.lock:
                job = self.jobs[container]
                job["finished"] = int(self.clock())
                if message:
                    job.update(state="failed", message=message)
                else:
                    job["state"] = "done"
        if message:
            logger.warning("updating container %s failed: %s", container, message)
        else:
            logger.info("updated container %s", container)

    @staticmethod
    def _compose_steps(cwd: str, steps: list[tuple[str, list[str]]]) -> str:
        """Runs each step; returns "" on success, else why the first failing step failed."""
        for step, argv in steps:
            try:
                done = subprocess.run(
                    argv, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=UPDATE_TIMEOUT
                )
            except subprocess.TimeoutExpired:
                return f"docker compose {step} timed out"
            except OSError as e:
                return str(e)
            if done.returncode:
                # Compose reports errors on stderr; fall back to stdout, then to the exit code.
                lines = [ln.strip() for ln in done.stderr.splitlines() if ln.strip()]
                lines = lines or [ln.strip() for ln in done.stdout.splitlines() if ln.strip()]
                return lines[-1] if lines else f"docker compose {step} exited with {done.returncode}"
        return ""

    def _compose_target(self, name: str) -> tuple[str, str, list[str], str]:
        """Finds a container's Compose project, folder, files and service from its labels."""
        if not CONTAINER_NAME.fullmatch(name):
            raise AgentError("Invalid container name")
        if not self.docker:
            raise AgentError("Container updates are turned off on the host")
        argv = [self.docker, "container", "inspect", "--format", "{{json .Config.Labels}}", name]
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=INSPECT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise AgentError(f"docker inspect failed: {e}") from e
        if done.returncode:
            raise AgentError(f"No container named {name}")
        try:
            labels = json.loads(done.stdout) or {}
        except ValueError:
            labels = {}
        if not isinstance(labels, dict) or not all(
            labels.get(k) and isinstance(labels[k], str) for k in COMPOSE_LABELS
        ):
            raise AgentError(f"{name} wasn't started by Docker Compose; update it by hand")
        project, cwd, files, service = (labels[k] for k in COMPOSE_LABELS)
        paths = [f for f in files.split(",") if f]
        for path in (cwd, *paths):
            if not os.access(path, os.R_OK):
                raise AgentError(f"{name}: can't read {path} on the host")
        return project, cwd, paths, service

    def update_jobs(self) -> list[dict]:
        """Update jobs, oldest first, forgetting those that finished more than an hour ago."""
        cutoff = self.clock() - JOB_TTL
        with self.lock:
            for name, job in list(self.jobs.items()):
                if job.get("finished", cutoff) < cutoff:
                    del self.jobs[name]
            return sorted((dict(j) for j in self.jobs.values()), key=lambda j: j["started"])

    # ── dispatch ──
    def status(self) -> dict:
        return {
            "claude": bool(self.claude),
            "reboot": bool(self.reboot_cmd),
            "docker": bool(self.docker),
            "updates": self.update_jobs(),
            "root": self.root,
            "sessions": self.sessions(),
        }

    def handle(self, req: dict) -> dict:
        """Runs one request and returns the response object (``error`` set on failure)."""
        action = req.get("action")
        try:
            if action == "status":
                return self.status()
            if action == "list":
                return self.list_dirs(str(req.get("path", "")))
            if action == "start":
                return {"session": self.start(str(req.get("path", "")), str(req.get("name", "")))}
            if action == "stop":
                self.stop(str(req.get("id", "")))
                return {"ok": True}
            if action == "reboot":
                self.reboot()
                return {"ok": True}
            if action == "update":
                return {"job": self.update(str(req.get("container", "")))}
            raise AgentError("Unknown action")
        except AgentError as e:
            return {"error": str(e)}


class AgentClient:
    """Used by the dashboard to talk to the agent over its unix socket."""

    def __init__(self, path: str, timeout: float = START_GRACE + 10) -> None:
        self.path = path
        self.timeout = timeout

    def call(self, action: str, **params: Any) -> dict:
        """Sends one request; raises AgentError if the agent is unreachable or refuses."""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.connect(self.path)
                s.sendall(json.dumps({"action": action, **params}).encode() + b"\n")
                with s.makefile("rb") as f:
                    line = f.readline(1_000_000)
        except OSError as e:
            raise AgentError(f"Host agent not reachable ({e.strerror or e})") from e
        try:
            resp = json.loads(line)
        except ValueError as e:
            raise AgentError("Host agent sent an invalid response") from e
        if resp.get("error"):
            raise AgentError(resp["error"])
        return dict(resp)


class _Handler(socketserver.StreamRequestHandler):
    agent: Agent

    def handle(self) -> None:
        line = self.rfile.readline(MAX_REQUEST)
        try:
            req = json.loads(line)
            resp = self.agent.handle(req) if isinstance(req, dict) else {"error": "Bad request"}
        except ValueError:
            resp = {"error": "Bad request"}
        except Exception:
            logger.exception("request failed")
            resp = {"error": "Internal error in the host agent"}
        self.wfile.write(json.dumps(resp).encode() + b"\n")


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(agent: Agent, path: str) -> socketserver.UnixStreamServer:
    """Binds the socket (replacing a stale one) with owner/group-only permissions."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)
    handler = type("Handler", (_Handler,), {"agent": agent})
    old = os.umask(0o117)
    try:
        server = _Server(path, handler)
    finally:
        os.umask(old)
    return server


def main() -> None:
    """Reads the environment and serves until interrupted."""
    logging.basicConfig(
        level=os.environ.get("PIHUB_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    path = os.environ.get("PIHUB_AGENT_SOCKET") or DEFAULT_SOCKET
    agent = Agent(
        root=os.environ.get("PIHUB_AGENT_ROOT") or os.path.expanduser("~"),
        state_dir=os.environ.get("PIHUB_AGENT_STATE") or os.path.expanduser("~/.local/state/pihub"),
        claude=os.environ.get("PIHUB_AGENT_CLAUDE", "claude"),
        reboot=os.environ.get("PIHUB_AGENT_REBOOT", "sudo -n systemctl reboot"),
        docker=os.environ.get("PIHUB_AGENT_DOCKER", "docker"),
    )
    server = serve(agent, path)
    logger.info(
        "pi-hub host agent on %s (root %s, claude %s, reboot %s, docker %s)",
        path,
        agent.root,
        agent.claude or "off",
        shlex.join(agent.reboot_cmd) or "off",
        agent.docker or "off",
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
