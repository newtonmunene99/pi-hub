import json
import os
import stat
import tempfile
import threading
import time
import unittest

from pihub.hostagent import Agent, AgentClient, AgentError, serve

FAKE_CLAUDE = """#!/bin/sh
case "$PWD" in
  *untrusted*) echo "Error: Workspace not trusted. Please run claude first."; exit 1 ;;
esac
printf '\\033[1A\\033[JConnected\\nhttps://claude.ai/code/session_abc123?from=cli\\n'
while :; do sleep 1; done
"""


class AgentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.root = os.path.join(self.tmp, "home")
        for d in ("projects/app", "projects/untrusted", "Music", ".hidden"):
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
        self.assertEqual(self.agent.list_dirs("projects")["dirs"], ["app", "untrusted"])

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
        self.assertEqual(client.call("list", path="projects")["dirs"], ["app", "untrusted"])
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


if __name__ == "__main__":
    unittest.main()
