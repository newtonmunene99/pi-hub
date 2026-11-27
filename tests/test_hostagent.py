import json
import os
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock

from pihub.hostagent import Agent, AgentClient, AgentError, serve

FAKE_CLAUDE = """#!/bin/sh
case "$PWD" in
  *busy*) echo "Error: This folder is already served by a terminal claude remote-control."; sleep 60 ;;
  *untrusted*) echo "Error: Workspace not trusted. Please run claude first."; exit 1 ;;
esac
printf '\\033[1A\\033[JConnected\\nhttps://claude.ai/code/session_abc123?from=cli\\n'
while :; do sleep 1; done
"""

FAKE_DOCKER = """#!/bin/sh
printf '%s|%s\\n' "$PWD" "$*" >> "$FAKE_DOCKER_LOG"
L='{"com.docker.compose.project":"%s",'
L="$L"'"com.docker.compose.project.config_files":"%s",'
L="$L"'"com.docker.compose.project.working_dir":"%s",'
L="$L"'"com.docker.compose.service":"%s"}\\n'
labels() { printf "$L" "$1" "$2" "$STACK" "$1"; }
case "$1 $2" in
  "container inspect")
    name="$(eval echo \\${$#})"
    case "$name" in
      locked) labels locked "$STACK/locked.yml" ;;
      radarr) labels radarr "$STACK/compose.yml,$STACK/override.yml" ;;
      broken|slow) labels "$name" "$STACK/compose.yml" ;;
      plain) echo '{"maintainer":"someone"}' ;;
      *) echo "Error: No such container: $name" >&2; exit 1 ;;
    esac ;;
  compose*)
    case "$*" in
      *"pull broken"*) echo "Pulling broken"; echo "Error response from daemon: manifest unknown" >&2; exit 1 ;;
      *"pull slow"*) sleep 2 ;;
    esac ;;
esac
"""


class AgentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.root = os.path.join(self.tmp, "home")
        for d in ("projects/app", "projects/untrusted", "projects/busy", "Music", ".hidden"):
            os.makedirs(os.path.join(self.root, d))
        open(os.path.join(self.root, "notes.txt"), "w").close()
        os.symlink(self.tmp, os.path.join(self.root, "escape"))
        claude = os.path.join(self.tmp, "claude")
        with open(claude, "w") as f:
            f.write(FAKE_CLAUDE)
        os.chmod(claude, stat.S_IRWXU)
        self.marker = os.path.join(self.tmp, "rebooted")
        self.agent = Agent(
            self.root, os.path.join(self.tmp, "state"), claude=claude, reboot=f"touch {self.marker}", start_grace=0.5
        )
        self.addCleanup(self.stop_all)

    def stop_all(self):
        for s in self.agent.sessions():
            self.agent.stop(s["id"])

    def test_lists_visible_folders_only(self):
        out = self.agent.list_dirs("")
        self.assertEqual(out["path"], "")
        self.assertEqual(out["dirs"], ["Music", "projects"])  # no dotdirs, files or escaping symlinks
        self.assertEqual(self.agent.list_dirs("projects")["dirs"], ["app", "busy", "untrusted"])

    def test_refuses_paths_outside_root(self):
        for rel in ("..", "../..", "/etc", "projects/../../", "escape", "notes.txt", "missing"):
            with self.subTest(rel=rel), self.assertRaises(AgentError):
                self.agent.list_dirs(rel)

    def test_start_list_and_stop_session(self):
        session = self.agent.start("projects/app")
        self.assertEqual(session["name"], "app")
        self.assertEqual(session["path"], "projects/app")
        listed = self.agent.sessions()
        self.assertEqual([s["id"] for s in listed], [session["id"]])
        self.assertEqual(listed[0]["url"], "https://claude.ai/code/session_abc123?from=cli")
        with self.assertRaisesRegex(AgentError, "already running"):
            self.agent.start("projects/app")
        pid = self.agent._load()[0]["pid"]
        self.agent.stop(session["id"])
        self.assertEqual(self.agent.sessions(), [])
        for _ in range(50):
            if not Agent._alive(pid):
                break
            time.sleep(0.05)
        self.assertFalse(Agent._alive(pid))

    def test_start_reports_claude_error(self):
        with self.assertRaisesRegex(AgentError, "^Workspace not trusted"):
            self.agent.start("projects/untrusted")
        self.assertEqual(self.agent.sessions(), [])

    def test_start_stops_claude_that_errors_but_lingers(self):
        self.agent.start_grace = 3
        began = time.monotonic()
        with self.assertRaisesRegex(AgentError, "^This folder is already served"):
            self.agent.start("projects/busy")
        self.assertLess(time.monotonic() - began, 3)
        self.assertEqual(self.agent.sessions(), [])
        self.assertEqual(os.listdir(self.agent.state_dir), [])

    def test_start_in_root_is_named_after_it(self):
        session = self.agent.start("", name="  ")
        self.assertEqual(session["name"], "home")

    def test_reboot_runs_command(self):
        self.agent.reboot()
        self.assertTrue(os.path.exists(self.marker))
        self.agent.reboot_cmd = ["false"]
        with self.assertRaises(AgentError):
            self.agent.reboot()
        self.agent.reboot_cmd = []
        with self.assertRaisesRegex(AgentError, "turned off"):
            self.agent.reboot()

    def test_socket_round_trip(self):
        path = os.path.join(self.tmp, "state", "agent.sock")
        server = serve(self.agent, path)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o660)
        client = AgentClient(path)
        status = client.call("status")
        self.assertTrue(status["claude"] and status["reboot"])
        self.assertEqual(client.call("list", path="projects")["dirs"], ["app", "busy", "untrusted"])
        with self.assertRaisesRegex(AgentError, "outside"):
            client.call("list", path="..")
        with self.assertRaisesRegex(AgentError, "Unknown action"):
            client.call("rm")
        with self.assertRaisesRegex(AgentError, "not reachable"):
            AgentClient(os.path.join(self.tmp, "nope.sock")).call("status")

    def test_state_survives_restart(self):
        session = self.agent.start("Music")
        again = Agent(self.root, self.agent.state_dir, claude="", reboot="")
        self.assertEqual([s["id"] for s in again.sessions()], [session["id"]])
        with open(again.state_path) as f:
            self.assertEqual(len(json.load(f)), 1)


class UpdateTests(unittest.TestCase):
    """Container updates through a fake ``docker`` that logs its argv and answers from canned labels."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.stack = os.path.join(self.tmp, "stack")
        os.makedirs(self.stack)
        for name in ("compose.yml", "override.yml", "locked.yml"):
            open(os.path.join(self.stack, name), "w").close()
        os.chmod(os.path.join(self.stack, "locked.yml"), 0)
        self.log = os.path.join(self.tmp, "docker.log")
        docker = os.path.join(self.tmp, "docker")
        with open(docker, "w") as f:
            f.write(FAKE_DOCKER)
        os.chmod(docker, stat.S_IRWXU)
        env = mock.patch.dict(os.environ, {"STACK": self.stack, "FAKE_DOCKER_LOG": self.log})
        env.start()
        self.addCleanup(env.stop)
        self.agent = Agent(self.tmp, os.path.join(self.tmp, "state"), claude="", docker=docker)

    def calls(self):
        with open(self.log) as f:
            return [line.rstrip("\n") for line in f]

    def test_update_refusals(self):
        for name in ("", "-rm", "a b", "x" * 129):
            with self.subTest(name=name), self.assertRaisesRegex(AgentError, "^Invalid container name$"):
                self.agent.update(name)
        off = Agent(self.tmp, os.path.join(self.tmp, "state"), claude="", docker="")
        with self.assertRaisesRegex(AgentError, "^Container updates are turned off on the host$"):
            off.update("radarr")
        with self.assertRaisesRegex(AgentError, "^No container named ghost$"):
            self.agent.update("ghost")
        self.assertEqual(self.calls(), [f"{os.getcwd()}|container inspect --format {{{{json .Config.Labels}}}} ghost"])
        with self.assertRaisesRegex(AgentError, "^plain wasn't started by Docker Compose; update it by hand$"):
            self.agent.update("plain")
        if os.geteuid() != 0:  # root can read anything, so the check can't fail
            with self.assertRaises(AgentError) as caught:
                self.agent.update("locked")
            self.assertEqual(str(caught.exception), f"locked: can't read {self.stack}/locked.yml on the host")

    def job(self, name, state="running"):
        """Waits up to 5 s for the container's job to leave ``state`` and returns it."""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.agent.jobs[name]
            if job["state"] != state:
                return dict(job)
            time.sleep(0.05)
        self.fail(f"{name} still {state}")

    def compose_calls(self):
        return [c for c in self.calls() if "|compose " in c]

    def test_update_pulls_then_recreates(self):
        started = self.agent.update("radarr")
        self.assertEqual(started["container"], "radarr")
        self.assertEqual(started["state"], "running")
        job = self.job("radarr")
        self.assertEqual(job["state"], "done")
        self.assertGreaterEqual(job["finished"], started["started"])
        files = f"-f {self.stack}/compose.yml -f {self.stack}/override.yml"
        self.assertEqual(
            self.compose_calls(),
            [
                f"{self.stack}|compose -p radarr {files} pull radarr",
                f"{self.stack}|compose -p radarr {files} up -d --no-deps radarr",
            ],
        )

    def test_failed_pull_skips_up(self):
        self.agent.update("broken")
        job = self.job("broken")
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["message"], "Error response from daemon: manifest unknown")
        self.assertEqual(
            self.compose_calls(), [f"{self.stack}|compose -p broken -f {self.stack}/compose.yml pull broken"]
        )

    def test_refuses_second_update_while_running(self):
        self.agent.update("slow")
        with self.assertRaisesRegex(AgentError, "^slow is already updating$"):
            self.agent.update("slow")
        self.assertEqual(self.job("slow")["state"], "done")
        self.assertEqual(self.agent.update("slow")["state"], "running")
        self.job("slow")

    def test_handle_update_reports_refusal(self):
        self.assertEqual(
            self.agent.handle({"action": "update", "container": "ghost"}), {"error": "No container named ghost"}
        )


if __name__ == "__main__":
    unittest.main()
