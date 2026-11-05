"""Run the real client against a private Wayland compositor and EDID fixtures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def edid(serial="TH5EE0088525", model="XB253Q", vendor="ACR"):
    data = bytearray(128)
    data[:8] = bytes.fromhex("00ffffffffffff00")
    manufacturer = sum((ord(c) - 64) << shift for c, shift in zip(vendor, [10, 5, 0]))
    data[8:10] = manufacturer.to_bytes(2, "big")
    for offset, tag, text in [(54, 0xfc, model), (72, 0xff, serial)]:
        data[offset:offset + 18] = b"\0\0\0" + bytes([tag, 0]) + (text + "\n").encode().ljust(13, b" ")
    data[127] = -sum(data[:127]) % 256
    return data


class DisplayColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = ["cc", "wayland-scanner", "pkg-config"]
        if not all(shutil.which(name) for name in required):
            raise unittest.SkipTest("Wayland development tools are required")
        subprocess.run(["pkg-config", "--exists", "wayland-client", "wayland-server"], check=True)
        cls.build = tempfile.TemporaryDirectory()
        build = Path(cls.build.name)
        subprocess.run(["bash", str(ROOT / "build-scripts/build-display-color")], check=True,
                       env=os.environ | {"SRCDIR": str(ROOT), "DESTDIR": str(build)})
        cls.client = build / "usr/libexec/astral/display-color"
        xml = ROOT / "build-scripts/display-color/wlr-gamma-control-unstable-v1.xml"
        header = build / "wlr-gamma-control-unstable-v1-server-protocol.h"
        code = build / "protocol.c"
        subprocess.run(["wayland-scanner", "server-header", str(xml), str(header)], check=True)
        subprocess.run(["wayland-scanner", "private-code", str(xml), str(code)], check=True)
        flags = subprocess.check_output(["pkg-config", "--cflags", "--libs", "wayland-server"], text=True).split()
        cls.server = build / "compositor"
        subprocess.run(["cc", "-std=c11", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Werror", "-I", str(build),
                        str(ROOT / "tests/display_color_compositor.c"), str(code), *flags,
                        "-lm", "-o", str(cls.server)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.sysfs = self.root / "drm"
        for name, serial in [("DP-5", "TH5EE0088525"), ("DP-3", "TH5EE0088525"), ("DP-6", "another-unit")]:
            p = self.sysfs / ("card1-" + name)
            p.mkdir(parents=True)
            (p / "edid").write_bytes(edid(serial))
        self.env = os.environ | {"XDG_RUNTIME_DIR": str(self.runtime), "WAYLAND_DISPLAY": "display-color-test"}
        self.events_path = self.root / "events"
        self.events_file = self.events_path.open("w")
        self.addCleanup(self.events_file.close)
        self.processes = []
        self.addCleanup(self.stop_all)

    def stop_all(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    def events(self):
        return [json.loads(line) for line in self.events_path.read_text().splitlines()]

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail(f"Timed out; compositor events: {self.events()}")

    def start_server(self, *args):
        process = subprocess.Popen([str(self.server), *args], env=self.env, stdout=self.events_file)
        self.processes.append(process)
        self.wait_for(lambda: (self.runtime / "display-color-test").exists())

    def command(self, *args):
        return [str(self.client), "--vendor", "ACR", "--model", "XB253Q", "--serial", "TH5EE0088525",
                "--gain", "0.9", "--sysfs-root", str(self.sysfs), *args]

    def start_client(self, *args):
        process = subprocess.Popen(self.command("--apply", *args), env=self.env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(process)
        return process

    def test_check_only_never_claims_gamma(self):
        self.start_server()
        result = subprocess.run(self.command(), env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DP-5: matched", result.stdout)
        self.assertIn("DP-6: unchanged", result.stdout)
        self.assertEqual(self.events(), [])

    def test_correct_ramp_only_on_exact_unit_and_restore_on_stop(self):
        self.start_server()
        client = self.start_client()
        self.wait_for(lambda: any(e["event"] == "ramp-verified" for e in self.events()))
        client.terminate()
        self.assertEqual(client.wait(timeout=5), 0)
        self.wait_for(lambda: any(e["event"] == "restored" for e in self.events()))
        self.assertEqual({e["output"] for e in self.events()}, {"DP-5"})

    def test_reconnect_with_new_connector_keeps_device_identity(self):
        self.start_server("--hotplug")
        self.start_client()
        self.wait_for(lambda: len([e for e in self.events() if e["event"] == "ramp-verified"]) == 2)
        self.assertEqual([e["output"] for e in self.events() if e["event"] == "ramp-verified"], ["DP-5", "DP-3"])
        self.assertIn({"event": "restored", "output": "DP-5"}, self.events())
        self.assertNotIn("DP-6", {e["output"] for e in self.events()})

    def test_changed_monitor_identity_releases_existing_control(self):
        self.start_server()
        self.start_client()
        self.wait_for(lambda: any(e["event"] == "ramp-verified" for e in self.events()))
        (self.sysfs / "card1-DP-5/edid").write_bytes(edid(serial="replacement"))
        self.wait_for(lambda: any(e["event"] == "restored" for e in self.events()))
        self.assertEqual([e["event"] for e in self.events()], ["claimed", "ramp-verified", "restored"])

    def test_rejected_control_is_released_and_retried(self):
        self.start_server("--reject-first")
        self.start_client()
        self.wait_for(lambda: any(e["event"] == "ramp-verified" for e in self.events()), timeout=14)
        self.assertIn({"event": "rejected-control-destroyed", "output": "DP-5"}, self.events())
        self.assertNotIn("DP-6", {e["output"] for e in self.events()})

    def test_bad_or_different_edids_are_untouched(self):
        self.start_server()
        for label, data in [("serial", edid(serial="other")), ("model", edid(model="OTHER")),
                            ("vendor", edid(vendor="DEL")), ("checksum", edid()[:-1] + b"\0"),
                            ("short", b"invalid")]:
            with self.subTest(label=label):
                (self.sysfs / "card1-DP-5/edid").write_bytes(data)
                result = subprocess.run(self.command(), env=self.env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("DP-5: unchanged", result.stdout)
                client = self.start_client()
                time.sleep(.12)
                client.terminate()
                self.assertEqual(client.wait(timeout=5), 0)
        self.assertEqual(self.events(), [])

    def test_ambiguous_connectors_are_untouched(self):
        p = self.sysfs / "card2-DP-5"
        p.mkdir()
        (p / "edid").write_bytes(edid(serial="other"))
        self.start_server()
        result = subprocess.run(self.command(), env=self.env, capture_output=True, text=True, timeout=5)
        self.assertIn("DP-5: unchanged", result.stdout)
        client = self.start_client()
        time.sleep(.12)
        client.terminate()
        self.assertEqual(client.wait(timeout=5), 0)
        self.assertEqual(self.events(), [])

    def test_gain_one_does_not_claim_gamma(self):
        self.start_server()
        client = self.start_client("--gain", "1")
        time.sleep(.15)
        self.assertIsNone(client.poll())
        self.assertEqual(self.events(), [])

    def test_invalid_gain_is_rejected_before_display_access(self):
        for value in ["nan", "inf", "0", "0.49", "1.01", "0.9junk", ""]:
            with self.subTest(value=value):
                result = subprocess.run(self.command("--gain", value), env=self.env,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == "__main__":
    unittest.main()
