"""Verified downloads, repeatable updates and rollback of persistent node state."""

import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "mkosi.extra/usr/libexec/astral/update-tailscale"
loader = importlib.machinery.SourceFileLoader("tailscale_update", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
updater = importlib.util.module_from_spec(spec)
loader.exec_module(updater)
VERSION = "1.104.4"
NAME = f"tailscale_{VERSION}_amd64.tgz"
BEFORE = {"Version": "1.98.8", "BackendState": "Running", "Self": {"ID": "existing-node", "Online": True, "TailscaleIPs": ["100.64.0.1"]}}


class TailscaleUpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tmp = Path(self.directory.name)
        self.releases = self.tmp / "releases"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.state = self.tmp / "state"
        self.state.mkdir(mode=0o700)
        (self.state / "tailscaled.state").write_text("original-private-node-state")
        self.dropin = self.tmp / "systemd/90-astral-stable.conf"
        patcher = patch.multiple(updater, RELEASES=self.releases, CURRENT=self.releases / "current", BIN=self.bin, STATE=self.state, DROPIN=self.dropin, BACKUPS=self.tmp / "backups")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.commands = []

    def run_command(self, *args, **kwargs):
        self.commands.append(args)
        if args[-1] in ["--version", "version"]:
            output = VERSION + "\n"
        elif "cat" in args:
            output = "previous service definition\n"
        elif "MainPID" in args:
            output = "123\n"
        else:
            output = ""
        return subprocess.CompletedProcess(args, 0, output, "")

    def archive(self, symlink=False):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            for binary in ["tailscale", "tailscaled"]:
                member = tarfile.TarInfo(NAME.removesuffix(".tgz") + "/" + binary)
                if symlink and binary == "tailscaled":
                    member.type = tarfile.SYMTYPE
                    member.linkname = "/etc/shadow"
                    archive.addfile(member)
                else:
                    payload = (binary + "-verified-payload").encode()
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
        return data.getvalue()

    def download(self, archive):
        checksum = hashlib.sha256(archive).hexdigest().encode()
        return lambda name, limit: checksum if name.endswith(".sha256") else archive

    def test_rejects_checksum_mismatch_before_executing_download(self):
        with patch.object(updater, "download", side_effect=[b"0" * 64, b"untrusted archive"]), patch.object(updater, "run") as run:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                updater.stage_release(VERSION, NAME)
            run.assert_not_called()
        self.assertFalse(self.releases.exists())

    def test_rejects_executable_symlinks_in_archive(self):
        with patch.object(updater, "download", side_effect=self.download(self.archive(symlink=True))), patch.object(updater, "run", side_effect=self.run_command):
            with self.assertRaisesRegex(ValueError, "Invalid executable"):
                updater.stage_release(VERSION, NAME)
        self.assertFalse((self.releases / VERSION).exists())

    def test_verified_release_can_be_reused_after_failed_activation(self):
        with patch.object(updater, "download", side_effect=self.download(self.archive())), patch.object(updater, "run", side_effect=self.run_command):
            release = updater.stage_release(VERSION, NAME)
            self.assertEqual(updater.stage_release(VERSION, NAME), release)
            (release / "tailscaled").write_text("tampered executable")
            with self.assertRaisesRegex(ValueError, "does not match"):
                updater.stage_release(VERSION, NAME)

    def test_release_executables_remain_readable_with_private_root_umask(self):
        previous = os.umask(0o077)
        try:
            with patch.object(updater, "download", side_effect=self.download(self.archive())), patch.object(updater, "run", side_effect=self.run_command):
                release = updater.stage_release(VERSION, NAME)
        finally:
            os.umask(previous)
        self.assertEqual(self.releases.stat().st_mode & 0o777, 0o755)
        self.assertEqual(release.stat().st_mode & 0o777, 0o755)
        self.assertEqual((release / "tailscale").stat().st_mode & 0o777, 0o755)

    def test_selinux_rule_uses_logical_path_with_bootc_equivalence(self):
        prefix = Path('/usr/local/libexec/astral-tailscale')
        original_exists = Path.exists
        original_resolve = Path.resolve

        def exists(path):
            return True if str(path) == '/sys/fs/selinux/enforce' else original_exists(path)

        def resolve(path, *args, **kwargs):
            if path == prefix:
                return Path('/var/usrlocal/libexec/astral-tailscale')
            return original_resolve(path, *args, **kwargs)

        def run(*args, **kwargs):
            self.commands.append(args)
            output = 'system_u:object_r:usr_t:s0\n' if 'matchpathcon' in args[0] else 'system_u:object_r:bin_t:s0\n'
            if 'semanage' in args[0] and args[-1].startswith('/var/usrlocal'):
                raise subprocess.CalledProcessError(1, args, stderr='conflicts with equivalency rule')
            return subprocess.CompletedProcess(args, 0, output, '')

        with patch.object(updater, 'RELEASES', prefix), patch.object(Path, 'exists', exists), patch.object(Path, 'resolve', resolve), patch.object(updater, 'run', side_effect=run):
            updater.label_release(prefix / VERSION)
        self.assertTrue(any(command[-1] == str(prefix) + '(/.*)?' for command in self.commands))

    def test_stable_metadata_cannot_select_another_download_path(self):
        metadata = {"TarballsVersion": VERSION, "Tarballs": {"amd64": "../../other.tgz"}}
        with patch.object(updater, "download", return_value=json.dumps(metadata).encode()), patch.object(updater.platform, "machine", return_value="x86_64"):
            with self.assertRaisesRegex(ValueError, "archive name"):
                updater.stable_release()

    def test_rejects_release_below_security_baseline(self):
        with patch.object(updater, "download", return_value=b'{"TarballsVersion":"1.98.8"}'):
            with self.assertRaisesRegex(ValueError, "security baseline"):
                updater.stable_release()

    def test_current_cli_does_not_hide_an_old_running_daemon(self):
        with patch.object(updater, "stable_release", return_value=(VERSION, NAME)), patch.object(updater, "status", return_value=BEFORE), patch.object(updater, "run", side_effect=self.run_command), patch.object(updater, "stage_release") as stage, patch("sys.argv", [str(SCRIPT)]), contextlib.redirect_stdout(io.StringIO()) as output:
            updater.main()
        self.assertIn("running daemon 1.98.8", output.getvalue())
        self.assertIn("Check only", output.getvalue())
        stage.assert_not_called()

    def test_current_cli_and_daemon_do_not_restart(self):
        before = BEFORE | {"Version": VERSION}
        with patch.object(updater, "stable_release", return_value=(VERSION, NAME)), patch.object(updater, "status", return_value=before), patch.object(updater, "run", side_effect=self.run_command), patch.object(updater, "stage_release") as stage, patch("sys.argv", [str(SCRIPT), "--apply"]), patch.object(updater.os, "geteuid", return_value=0), contextlib.redirect_stdout(io.StringIO()):
            updater.main()
        stage.assert_not_called()
        self.assertFalse(any("systemctl" in command[0] for command in self.commands))

    def test_reconnect_rejects_changed_node_identity(self):
        changed = BEFORE | {"Self": BEFORE["Self"] | {"ID": "different-node"}}
        with patch.object(updater, "status", return_value=changed):
            with self.assertRaisesRegex(RuntimeError, "identity"):
                updater.wait_online(BEFORE, VERSION)

    def test_failed_activation_restores_state_links_and_service(self):
        release = self.releases / VERSION
        release.mkdir(parents=True)
        old_release = self.releases / "previous"
        old_release.mkdir()
        updater.CURRENT.symlink_to(old_release)
        for name in ["tailscale", "tailscaled"]:
            (self.bin / name).symlink_to(updater.CURRENT / name)
        self.dropin.parent.mkdir()
        self.dropin.write_text("previous drop-in")
        starts = []

        def run(*args, **kwargs):
            if "start" in args and "tailscaled.service" in args:
                starts.append(args)
                if len(starts) == 1:
                    (self.state / "tailscaled.state").write_text("migrated state")
            return self.run_command(*args, **kwargs)

        with patch.object(updater, "run", side_effect=run), patch.object(updater, "wait_online", side_effect=[RuntimeError("activation failed"), BEFORE]), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "activation failed"):
                updater.activate(release, VERSION, BEFORE)
        self.assertEqual((self.state / "tailscaled.state").read_text(), "original-private-node-state")
        self.assertEqual(updater.CURRENT.resolve(), old_release)
        self.assertEqual(self.dropin.read_text(), "previous drop-in")
        self.assertEqual(len(starts), 2)
        self.assertEqual(next(updater.BACKUPS.iterdir()).stat().st_mode & 0o777, 0o700)

    def test_success_checks_running_executable_and_retains_private_backup(self):
        release = self.releases / VERSION
        release.mkdir(parents=True)
        for name in ["tailscale", "tailscaled"]:
            (release / name).write_text(name)
        after = BEFORE | {"Version": VERSION}
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            if str(path) == "/proc/123/exe":
                return original_resolve(release / "tailscaled")
            return original_resolve(path, *args, **kwargs)

        with patch.object(updater, "run", side_effect=self.run_command), patch.object(updater, "wait_online", return_value=after), patch.object(Path, "resolve", resolve), contextlib.redirect_stdout(io.StringIO()):
            updater.activate(release, VERSION, BEFORE)
        self.assertEqual(updater.CURRENT.resolve(), release)
        self.assertEqual((self.bin / "tailscaled").resolve(), release / "tailscaled")
        backup = next(updater.BACKUPS.iterdir())
        self.assertEqual((backup / "state-before/tailscaled.state").read_text(), "original-private-node-state")
        self.assertTrue(json.loads((backup / "success.json").read_text())["same_identity_and_addresses"])

    def test_factory_masks_replace_inherited_cockpit_units(self):
        units = self.tmp / "etc/systemd/system"
        units.mkdir(parents=True)
        for name in ["cockpit.service", "cockpit.socket"]:
            (units / name).write_text("inherited startup configuration")
        rules = self.tmp / "cockpit.conf"
        factory = (ROOT / "mkosi.extra/usr/lib/tmpfiles.d/99-astral-factory.conf").read_text()
        rules.write_text("\n".join(line for line in factory.splitlines() if line.startswith("L+ /etc/systemd/system/cockpit.")) + "\n")
        subprocess.run(["systemd-tmpfiles", "--root=" + str(self.tmp), "--create", str(rules)], check=True, capture_output=True)
        for name in ["cockpit.service", "cockpit.socket"]:
            self.assertTrue((units / name).is_symlink())
            self.assertEqual(str((units / name).readlink()), "/dev/null")


if __name__ == "__main__":
    unittest.main()
