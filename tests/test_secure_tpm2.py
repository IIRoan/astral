"""Guided recovery must finish backups before changing TPM enrollment."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "mkosi.extra/usr/libexec/astral/secure-tpm2"
CREATE = "Create a recovery key using the existing TPM enrollment"
EXISTING = "Use an existing passphrase or recovery key"
STUB = r'''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with Path(os.environ['CALLS']).open('a') as log:
    log.write(json.dumps([name, *args]) + '\n')
state = Path(os.environ['STATE'])
def metadata(): return json.loads(state.read_text())
if name == 'sudo':
    if args == ['-v']: sys.exit(int(os.environ.get('SUDO_RC', '0')))
    os.execvp(args[0], args)
elif name == 'gum':
    counter = Path(os.environ['PROMPT_COUNT'])
    i = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(i + 1))
    answer = json.loads(os.environ['ANSWERS'])[i]
    if isinstance(answer, str): print(answer)
    else: sys.exit(answer)
elif name == 'cryptsetup':
    if args[0] == 'isLuks': sys.exit(int(os.environ.get('LUKS_RC', '0')))
    if args[0] == 'luksDump': print(state.read_text())
    elif args[0] == 'luksHeaderBackup':
        path = Path(args[args.index('--header-backup-file') + 1])
        if path.stem == os.environ.get('BACKUP_FAIL'): sys.exit(32)
        path.write_text('disposable-header-fixture')
        if os.environ.get('UNSAFE_BACKUP_MODE') == '1': path.chmod(0o644)
    elif args[0] == 'open':
        assert '--test-passphrase' in args and '--disable-external-tokens' in args and '--key-file=-' in args
        sys.exit(0 if sys.stdin.read() == 'disposable-recovery-fixture' else 1)
    else: raise AssertionError(args)
elif name == 'systemd-ask-password': sys.stdout.write('disposable-recovery-fixture')
elif name == 'mokutil':
    print(os.environ.get('SECURE_BOOT', 'SecureBoot enabled'))
    sys.exit(int(os.environ.get('SECURE_BOOT_RC', '0')))
elif name == 'systemd-analyze':
    print(os.environ.get('PCRS', json.dumps([{'nr': 7, 'sha256': '1'*64}, {'nr': 14, 'sha256': '2'*64}])))
    sys.exit(int(os.environ.get('PCR_RC', '0')))
elif name == 'findmnt':
    if args[-1] == 'FSTYPE': print(os.environ.get('BACKUP_FS', 'ext4'))
    elif args[-1] == 'UUID': print('system-fs' if args[args.index('-T') + 1] == '/var' else os.environ.get('BACKUP_UUID', 'external-fs'))
    else: raise AssertionError(args)
elif name == 'systemd-cryptenroll':
    assert args[:2] == ['--unlock-tpm2-device=auto', '--recovery-key']
    if os.environ.get('RECOVERY_FAIL') == '1': sys.exit(42)
    d = metadata()
    d['keyslots']['1'] = {}
    d['tokens']['2'] = {'type': 'systemd-recovery', 'keyslots': ['1']}
    state.write_text(json.dumps(d))
elif name == 'luks-tpm2-autounlock':
    if os.environ.get('ENROLL_FAIL') == '1': sys.exit(42)
    if os.environ.get('ENROLL_CANCEL') == '1': sys.exit(130)
    d = metadata()
    d['keyslots'].pop('2')
    d['keyslots']['3'] = {}
    d['tokens']['1'] = {'type': 'systemd-tpm2', 'keyslots': ['3'], 'tpm2-pcrs': json.loads(os.environ.get('AFTER_PCRS', '[7,14]'))}
    if os.environ.get('DROP_RECOVERY') == '1': d['keyslots'].pop('1')
    state.write_text(json.dumps(d))
elif name == 'cat' and args == ['/proc/cmdline']:
    print(os.environ.get('CMDLINE', 'rd.luks.uuid=11111111-2222-3333-4444-555555555555'))
elif name == 'realpath':
    if args[-1].startswith('/dev/disk/by-uuid/'): print(os.environ['DEVICE'])
    else: os.execv('/usr/bin/realpath', ['realpath', *args])
elif name == 'chmod':
    if os.environ.get('UNSAFE_BACKUP_MODE') == '1': sys.exit(0)
    os.execv('/usr/bin/chmod', ['chmod', *args])
else: raise AssertionError((name, args))
'''


class SecureTpm2Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.device = self.root / "luks.img"
        self.device.touch()
        self.recovery = self.root / "external recovery"
        self.recovery.mkdir()
        self.state = self.root / "state.json"
        self.state.write_text(json.dumps({
            "keyslots": {"0": {}, "2": {}},
            "tokens": {"1": {"type": "systemd-tpm2", "keyslots": ["2"],
                              "tpm2-pcrs": [], "tpm2-blob": "never-print-token-material"}},
        }))
        self.env = os.environ | {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "CALLS": str(self.root / "calls"), "STATE": str(self.state),
            "PROMPT_COUNT": str(self.root / "prompts"), "DEVICE": str(self.device),
            "ANSWERS": json.dumps([0, CREATE, str(self.recovery), 0, 0]),
        }
        for name in ("sudo", "gum", "cryptsetup", "mokutil", "systemd-analyze",
                     "findmnt", "systemd-cryptenroll", "luks-tpm2-autounlock", "systemd-ask-password",
                     "cat", "realpath", "chmod"):
            path = self.bin / name
            path.write_text(STUB)
            path.chmod(0o755)

    def run_helper(self, expected=0, args=None, **env):
        result = subprocess.run(
            ["bash", str(HELPER), *(args if args is not None else ["--apply", "--device", str(self.device)])],
            env=self.env | env, text=True, capture_output=True, timeout=30,
        )
        if expected is None:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn("never-print-token-material", result.stdout + result.stderr)
        return result

    def calls(self, name):
        path = self.root / "calls"
        return [r[1:] for line in path.read_text().splitlines()
                if (r := json.loads(line))[0] == name] if path.exists() else []

    def test_default_is_check_only(self):
        result = self.run_helper(args=["--device", str(self.device)])
        self.assertIn("No changes made", result.stdout)
        self.assertEqual(self.calls("gum"), [])
        self.assertEqual(self.calls("systemd-cryptenroll"), [])
        self.assertEqual(self.calls("luks-tpm2-autounlock"), [])
        self.assertFalse(list(self.recovery.iterdir()))

    def test_help_and_bad_arguments_do_not_authenticate(self):
        self.run_helper(args=["--help"])
        self.run_helper(expected=2, args=["--device"])
        self.assertEqual(self.calls("sudo"), [])

    def test_guided_creation_backs_up_each_stage_and_preserves_recovery(self):
        result = self.run_helper()
        folder, = self.recovery.iterdir()
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        for filename in ("header-before.img", "header-with-recovery.img", "header-secured.img"):
            self.assertEqual((folder / filename).stat().st_mode & 0o777, 0o600)
        operations = [r[0] for r in self.calls("cryptsetup")]
        self.assertEqual(operations.count("luksHeaderBackup"), 3)
        rows = [json.loads(line) for line in (self.root / "calls").read_text().splitlines()]
        initial_backup = next(i for i, r in enumerate(rows) if r[:2] == ["cryptsetup", "luksHeaderBackup"])
        recovery = next(i for i, r in enumerate(rows) if r[0] == "systemd-cryptenroll")
        enrollment = next(i for i, r in enumerate(rows) if r[0] == "luks-tpm2-autounlock")
        self.assertLess(initial_backup, recovery)
        self.assertLess(recovery, enrollment)
        summary = json.loads((folder / "policy.json").read_text())
        self.assertEqual(summary["pcrs"], [[7, 14]])
        self.assertEqual(summary["independent_slots"], ["0", "1"])
        self.assertIn("Verified TPM binding", result.stdout)
        self.assertEqual(self.calls("luks-tpm2-autounlock"), [["--report-cancel", "--device", str(self.device)]])
        self.assertNotIn("reboot", [r[0] for r in rows])

    def test_existing_credential_does_not_create_another_recovery_key(self):
        self.run_helper(ANSWERS=json.dumps([0, EXISTING, str(self.recovery), 0]))
        self.assertEqual(self.calls("systemd-cryptenroll"), [])
        self.assertEqual(len([r for r in self.calls("cryptsetup") if r[0] == "luksHeaderBackup"]), 2)

    def test_cancel_before_slot_changes(self):
        for answers in ([1], [130], [0, "Cancel"], [0, CREATE, ""], [0, CREATE, str(self.recovery), 1]):
            with self.subTest(answers=answers):
                self.setUp()
                self.run_helper(ANSWERS=json.dumps(answers))
                self.assertEqual(self.calls("systemd-cryptenroll"), [])
                self.assertEqual(self.calls("luks-tpm2-autounlock"), [])

    def test_cancel_after_key_creation_keeps_old_tpm_and_new_recovery(self):
        result = self.run_helper(ANSWERS=json.dumps([0, CREATE, str(self.recovery), 0, 1]))
        d = json.loads(self.state.read_text())
        self.assertEqual(d["tokens"]["1"]["tpm2-pcrs"], [])
        self.assertEqual(d["tokens"]["2"]["type"], "systemd-recovery")
        self.assertEqual(self.calls("luks-tpm2-autounlock"), [])
        self.assertIn("A recovery key was added", result.stdout)

    def test_ineligible_boot_policy_never_changes_slots(self):
        for change in ({"SECURE_BOOT": "SecureBoot disabled"}, {"SECURE_BOOT_RC": "1"},
                       {"PCRS": "[]"}, {"PCR_RC": "1"}, {"LUKS_RC": "1"}):
            with self.subTest(change=change):
                self.setUp()
                self.run_helper(expected=None, **change)
                self.assertEqual(self.calls("systemd-cryptenroll"), [])
                self.assertEqual(self.calls("luks-tpm2-autounlock"), [])

    def test_temporary_or_system_filesystems_are_rejected_before_changes(self):
        for change in ({"BACKUP_FS": "tmpfs"}, {"BACKUP_FS": "overlay"}, {"BACKUP_UUID": "system-fs"}):
            with self.subTest(change=change):
                self.setUp()
                self.run_helper(expected=None, **change)
                self.assertEqual(self.calls("systemd-cryptenroll"), [])
                self.assertEqual(self.calls("luks-tpm2-autounlock"), [])

    def test_backup_and_recovery_failures_never_replace_tpm(self):
        for change in ({"BACKUP_FAIL": "header-before"}, {"BACKUP_FAIL": "header-with-recovery"}, {"RECOVERY_FAIL": "1"}):
            with self.subTest(change=change):
                self.setUp()
                self.run_helper(expected=None, **change)
                self.assertEqual(self.calls("luks-tpm2-autounlock"), [])

    def test_storage_that_ignores_private_header_permissions_is_rejected(self):
        self.run_helper(expected=None, UNSAFE_BACKUP_MODE="1")
        self.assertEqual(self.calls("systemd-cryptenroll"), [])
        self.assertEqual(self.calls("luks-tpm2-autounlock"), [])

    def test_cancelled_enrollment_does_not_report_verified_setup(self):
        result = self.run_helper(ENROLL_CANCEL="1")
        self.assertIn("Setup canceled", result.stdout)
        self.assertNotIn("Verified TPM binding", result.stdout)
        self.assertFalse(list(self.recovery.glob("*/header-secured.img")))
        self.assertFalse(list(self.recovery.glob("*/policy.json")))

    def test_cancel_with_existing_policy_never_reports_verified_setup(self):
        enable = "Enable or replace TPM2 enrollment"
        for recovery_mode in (EXISTING, CREATE):
            for cancellation in (["Cancel"], [enable, 1], [enable, 0, "Cancel"]):
                with self.subTest(recovery_mode=recovery_mode, cancellation=cancellation):
                    self.setUp()
                    before = json.loads(self.state.read_text())
                    before["tokens"]["1"]["tpm2-pcrs"] = [7, 14]
                    self.state.write_text(json.dumps(before))
                    enrollment_helper = self.bin / "luks-tpm2-autounlock"
                    enrollment_helper.unlink()
                    enrollment_helper.symlink_to(ROOT / "mkosi.extra/usr/bin/luks-tpm2-autounlock")
                    answers = [0, recovery_mode, str(self.recovery), 0]
                    if recovery_mode == CREATE:
                        answers.append(0)
                    result = self.run_helper(ANSWERS=json.dumps(answers + cancellation))
                    after = json.loads(self.state.read_text())
                    self.assertEqual(after["tokens"]["1"], before["tokens"]["1"])
                    self.assertEqual(len(self.calls("systemd-cryptenroll")), int(recovery_mode == CREATE))
                    if recovery_mode == CREATE:
                        self.assertEqual(after["tokens"]["2"]["type"], "systemd-recovery")
                    self.assertIn("Setup canceled", result.stdout)
                    self.assertNotIn("Verified TPM binding", result.stdout)
                    self.assertNotIn("systemctl reboot", result.stdout)
                    self.assertFalse(list(self.recovery.glob("*/header-secured.img")))
                    self.assertFalse(list(self.recovery.glob("*/policy.json")))

    def test_failed_or_incorrect_enrollment_is_not_reported_as_secured(self):
        for change in ({"ENROLL_FAIL": "1"}, {"AFTER_PCRS": "[7]"}, {"DROP_RECOVERY": "1"}):
            with self.subTest(change=change):
                self.setUp()
                result = self.run_helper(expected=None, **change)
                self.assertNotIn("Verified TPM binding", result.stdout)
                self.assertFalse(list(self.recovery.glob("*/header-secured.img")))

    def test_kernel_device_discovery_and_ambiguity(self):
        self.run_helper(args=[])
        self.setUp()
        self.run_helper(expected=None, args=[], CMDLINE="quiet")
        self.assertEqual(self.calls("sudo"), [])


if __name__ == "__main__":
    unittest.main()
