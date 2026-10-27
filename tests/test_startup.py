"""Regression checks for the login-time greeter sync handoff."""

import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "mkosi.extra/usr/libexec/astral/sync-noctalia-greeter"


class GreeterSyncTests(unittest.TestCase):
    def run_sync(self, ready_after):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            calls = tmp / "calls"
            for name, body in {
                "noctalia": """import os, pathlib, sys
path = pathlib.Path(os.environ['CALLS'])
count = int(path.read_text()) + 1 if path.exists() else 1
path.write_text(str(count))
assert sys.argv[1:] == ['msg', 'greeter-sync']
sys.exit(0 if count >= int(os.environ['READY_AFTER']) else 1)
""",
                "sleep": "pass\n",
                "pkexec": "raise RuntimeError('Do not launch a second privileged sync')\n",
            }.items():
                path = tmp / name
                path.write_text("#!/usr/bin/python3\n" + body)
                path.chmod(0o755)
            result = subprocess.run(
                ["bash", str(HELPER)],
                env=os.environ | {
                    "PATH": f"{tmp}:/usr/bin:/bin", "CALLS": str(calls),
                    "READY_AFTER": str(ready_after),
                },
                capture_output=True, text=True,
            )
            return result, int(calls.read_text())

    def test_waits_for_shell_then_hands_off_sync_once(self):
        result, calls = self.run_sync(3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 3)

    def test_missing_shell_is_reported_as_failure(self):
        result, calls = self.run_sync(46)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, 45)
        self.assertIn("did not accept", result.stderr)


@unittest.skipUnless(shutil.which("systemd-analyze"), "systemd is required")
class SessionOrderingTests(unittest.TestCase):
    def test_deferred_setup_does_not_block_the_session_that_starts_it(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            runtime = tmp / "run"
            runtime.mkdir(mode=0o700)
            timer = ROOT / "mkosi.extra/usr/lib/systemd/user/astral-update-user-setup.timer"
            fixtures = {
                timer.name: timer.read_text(),
                "graphical-session.target": (
                    "[Unit]\nWants=startup-test-compositor.service astral-update-user-setup.timer\n"
                    "After=startup-test-compositor.service\n"
                ),
                "startup-test-compositor.service": (
                    "[Unit]\nRequires=basic.target\nAfter=basic.target\n"
                    "Before=graphical-session.target\n[Service]\nExecStart=/usr/bin/true\n"
                ),
                "astral-update-user-setup.service": (
                    "[Unit]\nAfter=graphical-session.target\n"
                    "[Service]\nType=oneshot\nExecStart=/usr/bin/true\n"
                ),
            }
            for name, data in fixtures.items():
                (tmp / name).write_text(data)
            result = subprocess.run(
                ["systemd-analyze", "--user", "--man=no", "verify",
                 str(tmp / "graphical-session.target"), str(tmp / timer.name)],
                env=os.environ | {
                    "SYSTEMD_UNIT_PATH": f"{tmp}:/usr/lib/systemd/user:/lib/systemd/user",
                    "XDG_RUNTIME_DIR": str(runtime),
                },
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("ordering cycle", result.stderr)


if __name__ == "__main__":
    unittest.main()
