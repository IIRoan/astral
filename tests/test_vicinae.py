"""Keep launcher startup from replacing writable user settings."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "mkosi.extra/usr/libexec/astral/prepare-vicinae"
SEED = ROOT / "mkosi.extra/usr/share/astral/dotfiles/dot_config/vicinae/settings.json"


class VicinaeSetupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = Path(directory.name) / "config with spaces"
        self.settings = self.config / "vicinae/settings.json"

    def prepare(self):
        subprocess.run(
            ["bash", str(HELPER)],
            env=os.environ | {"XDG_CONFIG_HOME": str(self.config)},
            check=True, capture_output=True, text=True,
        )

    def test_first_start_seeds_the_same_import_as_a_fresh_install(self):
        self.prepare()
        self.assertEqual(json.loads(self.settings.read_text()), json.loads(SEED.read_text()))
        imported = json.loads(self.settings.read_text())["imports"][0]
        self.assertTrue((ROOT / "mkosi.extra" / imported.lstrip("/")).is_file())

    def test_restart_preserves_gui_changes_and_config_imports(self):
        self.prepare()
        content = '{"imports":["my-settings.json"],"launcher_window":{"compact_mode":{"enabled":false}}}\n'
        self.settings.write_text(content)
        self.settings.chmod(0o600)
        self.prepare()
        self.assertEqual(self.settings.read_text(), content)
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o600)

    def test_existing_jsonc_is_not_parsed_or_rewritten(self):
        self.settings.parent.mkdir(parents=True)
        content = '{\n  // Settings managed by the user\n  "tray": {"enabled": true}\n}\n'
        self.settings.write_text(content)
        self.prepare()
        self.assertEqual(self.settings.read_text(), content)

    def test_config_symlinks_including_missing_targets_are_preserved(self):
        self.settings.parent.mkdir(parents=True)
        target = self.config / "personal-settings.json"
        self.settings.symlink_to(target)
        self.prepare()
        self.assertTrue(self.settings.is_symlink())
        self.assertFalse(target.exists())
        target.write_text('{"tray":{"enabled":true}}')
        self.prepare()
        self.assertEqual(target.read_text(), '{"tray":{"enabled":true}}')


if __name__ == "__main__":
    unittest.main()
