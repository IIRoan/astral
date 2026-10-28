"""Behavioral checks for update failures, reminders and ComposeFS remounts."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "mkosi.extra/usr/libexec/astral"


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tmp = Path(self.directory.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.env = os.environ | {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "XDG_STATE_HOME": str(self.tmp / "state"),
            "NOTIFICATIONS": str(self.tmp / "notifications"),
            "UNITS": "{}",
            "DEPLOYMENTS": '{"deployments": []}',
        }
        self.stub("systemctl", """
import json, os, sys
args = sys.argv[1:]
scope = 'user' if '--user' in args else 'system'
args = [arg for arg in args if arg != '--user']
operation, unit = args[:2]
data = json.loads(os.environ['UNITS']).get(scope + ':' + unit, {})
if operation == 'is-enabled':
    enabled = data.get('enabled', 'enabled')
    print(enabled)
    sys.exit(0 if enabled == 'enabled' else 1)
if operation == 'show':
    prop = args[args.index('-p') + 1]
    print(data.get(prop, {'ActiveState': 'inactive', 'Result': 'success', 'ConditionResult': 'yes'}.get(prop, '')))
elif operation == 'enable':
    with open(os.environ['NOTIFICATIONS'], 'a') as f:
        f.write('enable timers\\n')
""")
        self.stub("rpm-ostree", """
import os, sys
print(os.environ['DEPLOYMENTS'])
sys.exit(int(os.environ.get('DEPLOYMENTS_RC', '0')))
""")
        self.stub("notify-send", """
import json, os, sys
with open(os.environ['NOTIFICATIONS'], 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
""")
        self.stub("fwupdmgr", """
import os, sys
if sys.argv[1] == 'refresh':
    sys.exit(0)
if sys.argv[1] == 'get-devices':
    print(os.environ.get('FIRMWARE_DEVICES', '{}'))
    sys.exit(int(os.environ.get('FIRMWARE_DEVICES_RC', '0')))
print(os.environ.get('FIRMWARE', '{}'))
sys.exit(int(os.environ.get('FIRMWARE_RC', '0')))
""")

    def stub(self, name, source):
        path = self.bin / name
        path.write_text("#!/usr/bin/python3\n" + source.lstrip())
        path.chmod(0o755)
        return path

    def run_helper(self, name, *args, expected=0):
        result = subprocess.run(
            ["/usr/bin/bash", str(HELPERS / name), *args],
            env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def notifications(self):
        path = self.tmp / "notifications"
        return path.read_text().splitlines() if path.exists() else []

    def set_failure(self, state="failed"):
        self.env["UNITS"] = json.dumps({
            "system:bootc-fetch-apply-updates.service": {
                "ActiveState": state, "Result": "exit-code"
            }
        })

    def test_enabled_timer_does_not_hide_failed_service(self):
        self.set_failure()
        result = self.run_helper("update-health")
        self.assertIn("timer enabled; service failed (exit-code)", result.stdout)
        self.assertEqual(self.notifications(), [])

    def test_failure_notification_repeats_daily_and_clears_after_recovery(self):
        self.set_failure()
        self.run_helper("update-health", "--notify")
        self.run_helper("update-health", "--notify")
        self.assertEqual(len(self.notifications()), 1)
        self.assertIn("critical", self.notifications()[0])
        state = self.tmp / "state/astral/update-health.json"
        data = json.loads(state.read_text())
        data["notified_at"] -= 86401
        state.write_text(json.dumps(data))
        self.run_helper("update-health", "--notify")
        self.assertEqual(len(self.notifications()), 2)
        self.env["UNITS"] = "{}"
        self.run_helper("update-health", "--notify")
        self.assertFalse(state.exists())

    def test_failure_is_reported_while_automatic_retry_is_waiting(self):
        self.set_failure("activating")
        self.run_helper("update-health", "--notify")
        self.assertIn("System image: exit-code", self.notifications()[0])

    def test_staged_update_notifies_without_rebooting(self):
        self.env["DEPLOYMENTS"] = json.dumps({"deployments": [
            {"booted": True, "staged": False, "checksum": "old"},
            {"booted": False, "staged": True, "checksum": "new"},
        ]})
        self.run_helper("update-health", "--notify")
        self.assertIn("Reboot when convenient", self.notifications()[0])
        self.assertNotIn("critical", self.notifications()[0])

    def test_disabled_timer_is_reported_without_false_failure(self):
        self.env["UNITS"] = json.dumps({
            "system:bootc-fetch-apply-updates.timer": {"enabled": "disabled"}
        })
        result = self.run_helper("update-health", "--notify")
        self.assertIn("System image: timer disabled", result.stdout)
        self.assertEqual(self.notifications(), [])

    def test_pending_firmware_reboot_is_reported_even_without_new_updates(self):
        self.env["FIRMWARE_DEVICES"] = json.dumps({"Devices": [{
            "Name": "UEFI dbx", "DeviceId": "dbx", "UpdateState": 4,
        }]})
        result = self.run_helper("update-health", "--notify")
        self.assertIn("Firmware update staged", result.stdout)
        self.assertIn("Reboot when convenient", self.notifications()[0])

    def test_status_query_errors_do_not_suppress_other_update_alerts(self):
        self.set_failure()
        original_env = self.env.copy()
        for name, changes, label in [
            ("firmware unavailable", {"FIRMWARE_DEVICES_RC": "1"}, "Firmware status"),
            ("invalid firmware response", {"FIRMWARE_DEVICES": "{invalid"}, "Firmware status"),
            ("deployment unavailable", {"DEPLOYMENTS_RC": "1"}, "System deployment status"),
            ("invalid deployment response", {"DEPLOYMENTS": "{invalid"}, "System deployment status"),
        ]:
            with self.subTest(name=name):
                self.env = original_env | changes
                (self.tmp / "notifications").unlink(missing_ok=True)
                (self.tmp / "state/astral/update-health.json").unlink(missing_ok=True)
                self.run_helper("update-health", "--notify")
                self.assertEqual(len(self.notifications()), 1)
                self.assertIn("System image: exit-code", self.notifications()[0])
                self.assertIn(f"{label}: query failed", self.notifications()[0])

    def test_status_query_error_is_reported_without_other_failures(self):
        self.env["FIRMWARE_DEVICES_RC"] = "1"
        self.run_helper("update-health", "--notify")
        self.assertIn("Firmware status: query failed", self.notifications()[0])
        self.assertIn("critical", self.notifications()[0])

    def test_user_timer_setup_respects_later_disable_choices(self):
        self.run_helper("update-user-setup")
        self.run_helper("update-user-setup")
        self.assertEqual(self.notifications(), ["enable timers"])

    def pending_firmware(self):
        self.env["FIRMWARE"] = json.dumps({"Devices": [{
            "Name": "UEFI dbx", "Version": "20260402", "Releases": [{
                "Version": "20260707", "ReleaseId": "150556",
                "Urgency": "high", "Flags": ["is-upgrade"],
            }],
        }]})

    def test_firmware_reminds_again_after_one_day(self):
        self.pending_firmware()
        self.run_helper("firmware-update-check")
        self.run_helper("firmware-update-check")
        self.assertEqual(len(self.notifications()), 1)
        state = self.tmp / "state/astral/firmware-notify.json"
        data = json.loads(state.read_text())
        data["notified_at"] -= 86401
        state.write_text(json.dumps(data))
        self.run_helper("firmware-update-check")
        self.assertEqual(len(self.notifications()), 2)
        self.assertIn("critical", self.notifications()[0])

    def test_firmware_query_failure_is_not_reported_as_success(self):
        self.env["FIRMWARE_RC"] = "1"
        self.run_helper("firmware-update-check", expected=1)
        self.assertEqual(self.notifications(), [])

    def test_invalid_firmware_json_fails_without_notification(self):
        self.env["FIRMWARE"] = "{invalid"
        self.run_helper("firmware-update-check", expected=1)
        self.assertEqual(self.notifications(), [])

    def test_no_firmware_updates_clear_the_reminder(self):
        self.pending_firmware()
        self.run_helper("firmware-update-check")
        self.env["FIRMWARE_RC"] = "2"
        self.run_helper("firmware-update-check")
        self.assertFalse((self.tmp / "state/astral/firmware-notify.json").exists())

    def run_remount(self, source):
        self.env["ROOT_SOURCE"] = source
        self.env["SYSTEMD_FSTAB"] = str(self.tmp / "fstab")
        original = "# Root is already mounted\nUUID=root / btrfs ro 0 0\nUUID=usr /usr ext4 ro 0 0\nproc /proc proc nosuid 0 0\nUUID=var /var btrfs defaults 0 0\n"
        (self.tmp / "fstab").write_text(original)
        self.stub("findmnt", "import os; print(os.environ['ROOT_SOURCE'])")
        remounter = self.stub("remounter", """
import os, pathlib
pathlib.Path(os.environ['NOTIFICATIONS']).write_text(pathlib.Path(os.environ['SYSTEMD_FSTAB']).read_text())
""")
        # Replace only the OS binary and /run paths in a temporary fixture.
        # The production filtering and environment handling run unchanged.
        script = (HELPERS / "composefs-remount").read_text()
        script = script.replace("/usr/lib/systemd/systemd-remount-fs", str(remounter))
        script = script.replace("/run/astral-remount.", str(self.tmp / "astral-remount."))
        result = subprocess.run(["/usr/bin/bash", "-c", script], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.tmp / "fstab").read_text(), original)
        self.assertEqual(list(self.tmp.glob("astral-remount.*")), [])
        return (self.tmp / "notifications").read_text()

    def test_composefs_skips_root_but_preserves_usr_and_api_mounts(self):
        actual = self.run_remount("composefs")
        self.assertNotIn("UUID=root / ", actual)
        self.assertIn("UUID=usr /usr ext4 ro", actual)
        self.assertIn("proc /proc proc nosuid", actual)
        self.assertIn("UUID=var /var btrfs defaults", actual)

    def test_non_composefs_keeps_normal_root_remount(self):
        self.assertIn("UUID=root / btrfs ro", self.run_remount("/dev/mapper/root"))


if __name__ == "__main__":
    unittest.main()
