"""Every power mode Noctalia can pick must resolve to an Astral TuneD profile chain."""

import configparser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXTRA = ROOT / "mkosi.extra"
PROFILES = EXTRA / "usr/lib/tuned/profiles"


def read(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(path)
    return parser


class PowerProfileTests(unittest.TestCase):
    def test_power_modes_resolve_to_profiles(self):
        ppd = read(EXTRA / "usr/share/astral/tuned/ppd.conf")
        names = [*ppd["profiles"].values(), *ppd["battery"].values()]
        self.assertEqual(set(ppd["profiles"]), {"power-saver", "balanced", "performance"})
        while names:
            name = names.pop()
            # Stock profiles (balanced, powersave, ...) ship with Fedora's tuned
            # package in the image, not in this repo or on CI runners.
            if not name.startswith("astral-"):
                continue
            conf = PROFILES / name / "tuned.conf"
            self.assertTrue(conf.is_file(), f"missing TuneD profile {name}")
            names += [n.strip() for n in read(conf)["main"].get("include", "").split(",") if n.strip()]

    def test_ppd_mapping_is_installed_into_etc(self):
        tmpfiles = (EXTRA / "usr/lib/tmpfiles.d/astral-tuned.conf").read_text().splitlines()
        self.assertIn("C /etc/tuned/ppd.conf 0644 root root - /usr/share/astral/tuned/ppd.conf", tmpfiles)


if __name__ == "__main__":
    unittest.main()
