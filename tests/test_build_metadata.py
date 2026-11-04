"""Run the real metadata step against image and tools-tree releases."""

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-build-bootc.yaml"


class BuildMetadataTests(unittest.TestCase):
    def run_metadata(self, config, manual=""):
        source = WORKFLOW.read_text().split("      - name: Get mkosi metadata\n", 1)[1]
        source = source.split("\n      - name:", 1)[0]
        script = textwrap.dedent(source.split("        run: |\n", 1)[1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if config is not None:
                (root / "mkosi.conf").write_text(config)
            output = root / "output"
            result = subprocess.run(
                ["/usr/bin/bash", "-c", script],
                cwd=root,
                env=os.environ | {
                    "PATH": "/usr/bin:/bin",
                    "GITHUB_OUTPUT": str(output),
                    "MANUAL_RELEASE": manual,
                },
                text=True, capture_output=True,
            )
            return result, output.read_text() if output.exists() else ""

    def assert_release(self, config, expected, manual=""):
        result, output = self.run_metadata(config, manual)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(output, f"distributionversion={expected}\n")

    def test_tools_release_cannot_replace_image_release(self):
        for config in (
            "ToolsTreeRelease=44\nRelease=45\n",
            "Release=45\nToolsTreeRelease=44\n",
        ):
            with self.subTest(config=config):
                self.assert_release(config, "45")

    def test_equal_releases_do_not_duplicate_output(self):
        self.assert_release("Release=44\nToolsTreeRelease=44\n", "44")

    def test_rawhide_image_keeps_stable_tools_release(self):
        self.assert_release("ToolsTreeRelease=44\nRelease=rawhide\n", "rawhide")

    def test_manual_release_does_not_require_config_file(self):
        self.assert_release(None, "45", manual="45")

    def test_missing_image_release_fails_without_output(self):
        result, output = self.run_metadata("ToolsTreeRelease=44\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, "")

    def test_ambiguous_or_invalid_release_fails_without_output(self):
        for config, manual in (
            ("Release=44\nRelease=45\n", ""),
            ("Release=\n", ""),
            (None, "44\nother=value"),
        ):
            with self.subTest(config=config, manual=manual):
                result, output = self.run_metadata(config, manual)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
