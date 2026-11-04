"""Every power mode Noctalia can pick must resolve to a TuneD profile chain."""

import configparser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXTRA = ROOT / "mkosi.extra"
PROFILE_DIRS = (EXTRA / "usr/lib/tuned/profiles", Path("/usr/lib/tuned/profiles"))


def read(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(path)
    return parser


def profile(name):
    return next((d / name / "tuned.conf" for d in PROFILE_DIRS if (d / name / "tuned.conf").is_file()), None)


class PowerProfileTests(unittest.TestCase):
    def test_power_modes_resolve_to_profiles(self):
        ppd = read(EXTRA / "usr/share/astral/tuned/ppd.conf")
        names = [*ppd["profiles"].values(), *ppd["battery"].values()]
        self.assertEqual(set(ppd["profiles"]), {"power-saver", "balanced", "performance"})
        while names:
            name = names.pop()
            conf = profile(name)
            self.assertIsNotNone(conf, f"missing TuneD profile {name}")
            names += [n.strip() for n in read(conf)["main"].get("include", "").split(",") if n.strip()]

    def test_ppd_mapping_is_installed_into_etc(self):
        tmpfiles = (EXTRA / "usr/lib/tmpfiles.d/99-astral-factory.conf").read_text().splitlines()
        self.assertIn("L+ /etc/tuned/ppd.conf - - - - /usr/share/astral/tuned/ppd.conf", tmpfiles)


if __name__ == "__main__":
    unittest.main()
