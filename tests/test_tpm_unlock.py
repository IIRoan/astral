"""TPM setup must verify recovery and boot policy before changing any slot."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "mkosi.extra/usr/bin/luks-tpm2-autounlock"
UUID = "11111111-2222-3333-4444-555555555555"
SECRET = "test-only-recovery-secret"
STUB = r'''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
name=Path(sys.argv[0]).name
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as log:
    log.write(json.dumps([name,*args])+'\n')
if name=='sudo':
    if args==['-v']:
        sys.exit(int(os.environ.get('SUDO_RC','0')))
    os.execvp(args[0],args)
elif name=='gum':
    count=Path(os.environ['PROMPT_COUNT'])
    i=int(count.read_text()) if count.exists() else 0
    count.write_text(str(i+1))
    sys.exit(json.loads(os.environ.get('ANSWERS','[0,0,1]'))[i])
elif name=='mokutil':
    print(os.environ.get('SECURE_BOOT','SecureBoot enabled'))
    sys.exit(int(os.environ.get('SECURE_BOOT_RC','0')))
elif name=='systemd-analyze':
    print(os.environ.get('PCRS',json.dumps([{'nr':7,'sha256':'1'*64},{'nr':14,'sha256':'2'*64}])))
    sys.exit(int(os.environ.get('PCR_RC','0')))
elif name=='systemd-ask-password':
    sys.stdout.write(os.environ.get('SECRET_INPUT','test-only-recovery-secret'))
    sys.exit(int(os.environ.get('ASK_RC','0')))
elif name=='cryptsetup':
    if args[0]=='isLuks':
        sys.exit(int(os.environ.get('LUKS_RC','0')))
    assert '--test-passphrase' in args and '--key-file=-' in args and '--disable-external-tokens' in args
    sys.exit(0 if sys.stdin.read()=='test-only-recovery-secret' else 1)
elif name=='systemd-cryptenroll':
    sys.exit(int(os.environ.get('ENROLL_RC','0')))
elif name=='cat' and args==['/proc/cmdline']:
    print(os.environ.get('CMDLINE','quiet rd.luks.uuid=luks-'+os.environ['UUID']))
elif name=='realpath':
    if args[-1].startswith('/dev/disk/by-uuid/'):
        print(os.environ['DEVICE'])
    else:
        os.execv('/usr/bin/realpath',['realpath',*args])
else:
    raise SystemExit('Unexpected test command')
'''


class TpmUnlockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.device = self.root / "luks.img"
        self.device.touch()
        self.env = os.environ | {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "CALLS": str(self.root / "calls"),
            "PROMPT_COUNT": str(self.root / "prompts"),
            "DEVICE": str(self.device),
            "UUID": UUID,
        }
        for name in ("sudo", "gum", "mokutil", "systemd-analyze", "systemd-ask-password",
                     "cryptsetup", "systemd-cryptenroll", "cat", "realpath"):
            path = self.bin / name
            path.write_text(STUB)
            path.chmod(0o755)

    def run_helper(self, expected=0, args=None, **env):
        self.env.update(env)
        result = subprocess.run(
            ["/usr/bin/bash", str(HELPER), *(args if args is not None else ["--device", str(self.device)])],
            env=self.env, text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNotIn(SECRET, (self.root / "calls").read_text() if (self.root / "calls").exists() else "")
        return result

    def calls(self, name):
        path = self.root / "calls"
        return [row[1:] for line in path.read_text().splitlines()
                if (row := json.loads(line))[0] == name] if path.exists() else []

    def test_enable_replaces_tpm_only_after_independent_recovery_check(self):
        self.run_helper()
        enrollment, = self.calls("systemd-cryptenroll")
        self.assertIn("--tpm2-pcrs=7+14", enrollment)
        self.assertIn("--tpm2-device=auto", enrollment)
        self.assertIn("--wipe-slot=tpm2", enrollment)
        self.assertIn("--tpm2-with-pin=no", enrollment)
        self.assertEqual(enrollment[-1], str(self.device))
        self.assertEqual(self.calls("cryptsetup")[-1][0], "open")

    def test_pin_is_opt_in(self):
        self.run_helper(ANSWERS="[0,0,0]")
        self.assertIn("--tpm2-with-pin=yes", self.calls("systemd-cryptenroll")[0])

    def test_cancel_at_each_prompt_does_not_change_enrollment(self):
        for answers in ("[130]", "[0,1]", "[0,0,130]"):
            with self.subTest(answers=answers):
                self.setUp()
                self.run_helper(ANSWERS=answers)
                self.assertEqual(self.calls("systemd-cryptenroll"), [])

    def test_wrong_recovery_credential_or_agent_failure_does_not_enroll(self):
        for change in ({"SECRET_INPUT": "wrong"}, {"ASK_RC": "1"}):
            with self.subTest(change=change):
                self.setUp()
                self.run_helper(expected=1, **change)
                self.assertEqual(self.calls("systemd-cryptenroll"), [])

    def test_disable_requires_recovery_but_not_working_secure_boot_or_tpm(self):
        self.run_helper(ANSWERS="[1,0]", SECURE_BOOT="SecureBoot disabled", PCRS="[]")
        self.assertEqual(self.calls("systemd-cryptenroll"), [["--wipe-slot=tpm2", str(self.device)]])
        self.assertEqual(self.calls("mokutil"), [])
        self.assertEqual(self.calls("systemd-analyze"), [])
        self.assertEqual(self.calls("cryptsetup")[-1][0], "open")

    def test_disable_with_bad_recovery_credential_keeps_tpm(self):
        self.run_helper(expected=1, ANSWERS="[1,0]", SECRET_INPUT="wrong")
        self.assertEqual(self.calls("systemd-cryptenroll"), [])

    def test_secure_boot_disabled_shim_validation_disabled_or_query_error_blocks_enrollment(self):
        for change in (
            {"SECURE_BOOT": "SecureBoot disabled"},
            {"SECURE_BOOT": "SecureBoot enabled\nSecureBoot validation is disabled in shim"},
            {"SECURE_BOOT_RC": "1"},
        ):
            with self.subTest(change=change):
                self.setUp()
                self.run_helper(expected=1, **change)
                self.assertEqual(self.calls("systemd-cryptenroll"), [])

    def test_uninitialized_missing_or_unreadable_pcrs_block_enrollment(self):
        for data in ([], [{"nr":7,"sha256":"1"*64}],
                     [{"nr":7,"sha256":"0"*64},{"nr":14,"sha256":"2"*64}],
                     [{"nr":7,"sha256":"1"*64},{"nr":14,"sha256":"f"*64}],
                     [{"nr":7,"sha1":"1"*40},{"nr":14,"sha1":"2"*40}], None):
            with self.subTest(data=data):
                self.setUp()
                self.run_helper(expected=1, PCRS=json.dumps(data))
                self.assertEqual(self.calls("systemd-cryptenroll"), [])
        self.setUp()
        self.run_helper(expected=1, PCR_RC="1")
        self.assertEqual(self.calls("systemd-cryptenroll"), [])

    def test_non_luks2_device_is_rejected(self):
        self.run_helper(expected=1, LUKS_RC="1")
        self.assertEqual(self.calls("systemd-cryptenroll"), [])

    def test_failed_enrollment_is_reported_without_a_separate_wipe(self):
        result = self.run_helper(expected=42, ENROLL_RC="42")
        self.assertEqual(len(self.calls("systemd-cryptenroll")), 1)
        self.assertNotIn("now binds", result.stdout)

    def test_kernel_uuid_and_name_forms_resolve_the_same_device(self):
        for value in (f"rd.luks.uuid=luks-{UUID}", f"rd.luks.name={UUID}=cryptroot"):
            with self.subTest(value=value):
                self.setUp()
                self.run_helper(args=[], CMDLINE=value)
                self.assertEqual(self.calls("systemd-cryptenroll")[0][-1], str(self.device))

    def test_ambiguous_missing_and_invalid_kernel_devices_are_rejected(self):
        for value in ("quiet", "rd.luks.uuid=invalid", f"rd.luks.uuid={UUID} rd.luks.uuid=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"):
            with self.subTest(value=value):
                self.setUp()
                self.run_helper(expected=1, args=[], CMDLINE=value)
                self.assertEqual(self.calls("systemd-cryptenroll"), [])


if __name__ == "__main__":
    unittest.main()
