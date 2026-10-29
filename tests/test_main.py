"""Startup checks in ``python -m pihub`` that stop the server before it listens."""

import os
import subprocess
import sys
import unittest


class StartupTests(unittest.TestCase):
    def run_pihub(self, **env):
        return subprocess.run(
            [sys.executable, "-m", "pihub"],
            env={**os.environ, "PIHUB_DEMO": "1", "PIHUB_HOST": "127.0.0.1", "PIHUB_PORT": "0", **env},
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_short_webhook_token_exits_2_without_logging_it(self):
        result = self.run_pihub(PIHUB_WEBHOOK_TOKEN="tooshort-secret")
        self.assertEqual(result.returncode, 2)
        self.assertIn("PIHUB_WEBHOOK_TOKEN must be at least 32 characters", result.stderr)
        self.assertNotIn("tooshort-secret", result.stderr)

    def test_reference_to_a_missing_file_exits_2(self):
        self.assertEqual(self.run_pihub(PIHUB_WEBHOOK_TOKEN="file:/nonexistent/token").returncode, 2)


if __name__ == "__main__":
    unittest.main()
