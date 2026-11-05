"""Exercise ISO source selection without registry writes or privileged builds."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_iso", ROOT / "build-scripts/prepare-iso.py")
ISO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ISO)
INDEX = "sha256:" + "a" * 64
AMD64 = "sha256:" + "b" * 64
ARM64 = "sha256:" + "c" * 64


class IsoSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "build-scripts").mkdir()
        for name in ("iso.toml", "build-scripts/iso-tools.json", "build-scripts/Containerfile.iso-build"):
            (self.root / name).write_text((ROOT / name).read_text())
        self.config = self.root / "rendered.toml"
        self.metadata = self.root / "source.json"
        self.calls = []
        self.manifest = {
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {"digest": AMD64, "platform": {"architecture": "amd64", "os": "linux"}},
                {"digest": ARM64, "platform": {"architecture": "arm64", "os": "linux"}},
            ],
        }
        self.image_config = {
            "architecture": "amd64", "os": "linux",
            "config": {"Labels": {"containers.bootc": "1", "org.opencontainers.image.version": "45.123.1"}},
        }
        self.rejected_signature = None
        self.signature_identity = ISO.REPOSITORY
        self.signature_digest = None

    def command(self, arguments):
        self.calls.append(arguments)
        reference = arguments[-1]
        if arguments[:2] == ["crane", "digest"]:
            self.assertEqual(reference, ISO.DEFAULT_REFERENCE)
            return INDEX + "\n"
        self.assertIn(reference, (ISO.REPOSITORY + "@" + INDEX, ISO.REPOSITORY + "@" + AMD64))
        if arguments[:2] == ["cosign", "verify"]:
            self.assertIn(str(self.root / "astral.pub"), arguments)
            if reference.endswith(self.rejected_signature or "never"):
                raise subprocess.CalledProcessError(1, arguments, stderr="invalid signature")
            return json.dumps([{"critical": {
                "identity": {"docker-reference": self.signature_identity},
                "image": {"docker-manifest-digest": self.signature_digest or reference.split("@", 1)[1]},
            }}])
        if arguments[:2] == ["crane", "manifest"]:
            return json.dumps(self.manifest)
        if arguments[:2] == ["crane", "config"]:
            return json.dumps(self.image_config)
        raise AssertionError(arguments)

    def prepare(self, digest=INDEX):
        with patch.object(ISO, "command", side_effect=self.command):
            return ISO.prepare(self.root, digest, self.config, self.metadata, "crane", "cosign")

    def assert_preserved_on_failure(self, digest=INDEX):
        self.config.write_text("previous config")
        self.metadata.write_text("previous metadata")
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            self.prepare(digest)
        self.assertEqual(self.config.read_text(), "previous config")
        self.assertEqual(self.metadata.read_text(), "previous metadata")

    def test_candidate_index_verifies_both_signatures_and_pins_installation(self):
        metadata = self.prepare()
        source = ISO.REPOSITORY + "@" + AMD64
        self.assertEqual(metadata["source_image"], source)
        self.assertEqual(metadata["install_reference"], source)
        self.assertEqual(metadata["build_container_source"], source)
        self.assertTrue(metadata["pinned_source"])
        self.assertTrue(metadata["signatures_verified"])
        self.assertEqual(json.loads(self.metadata.read_text()), metadata)
        kickstart = tomllib.loads(self.config.read_text())["customizations"]["installer"]["kickstart"]["contents"]
        self.assertIn("--enforce-container-sigpolicy --transport registry " + source, kickstart)
        self.assertNotIn(ISO.DEFAULT_REFERENCE, kickstart)
        self.assertEqual([call[-1] for call in self.calls if call[:2] == ["cosign", "verify"]],
                         [ISO.REPOSITORY + "@" + INDEX, source])
        self.assertNotIn(["crane", "digest", ISO.DEFAULT_REFERENCE], self.calls)

    def test_production_resolves_once_then_uses_immutable_source(self):
        metadata = self.prepare("")
        self.assertFalse(metadata["pinned_source"])
        self.assertEqual(metadata["install_reference"], ISO.DEFAULT_REFERENCE)
        self.assertEqual(metadata["source_image"], ISO.REPOSITORY + "@" + AMD64)
        self.assertEqual(self.calls[0], ["crane", "digest", ISO.DEFAULT_REFERENCE])
        self.assertEqual(sum(call[-1] == ISO.DEFAULT_REFERENCE for call in self.calls), 1)
        self.assertIn(ISO.DEFAULT_REFERENCE, self.config.read_text())

    def test_direct_amd64_manifest_is_verified_and_used(self):
        self.manifest = {"mediaType": "application/vnd.oci.image.manifest.v1+json"}
        metadata = self.prepare(AMD64)
        self.assertEqual(metadata["requested_image"], metadata["source_image"])
        self.assertEqual(metadata["install_reference"], metadata["source_image"])
        self.assertEqual(sum(call[:2] == ["cosign", "verify"] for call in self.calls), 1)

    def test_invalid_input_never_invokes_tools_or_replaces_output(self):
        for digest in ("latest", "45", "sha256:abc", INDEX.upper(), "other/repo@" + INDEX, INDEX + "\n"):
            with self.subTest(digest=digest):
                self.calls.clear()
                self.assert_preserved_on_failure(digest)
                self.assertEqual(self.calls, [])

    def test_bad_index_or_child_signature_preserves_output(self):
        for digest in (INDEX, AMD64):
            with self.subTest(digest=digest):
                self.rejected_signature = digest
                self.assert_preserved_on_failure()

    def test_signature_cannot_identify_another_repository_or_digest(self):
        for identity, digest in ((ISO.REPOSITORY + "-spoof", None), ("other/repo", None), (ISO.REPOSITORY, ARM64)):
            with self.subTest(identity=identity, digest=digest):
                self.signature_identity, self.signature_digest = identity, digest
                self.assert_preserved_on_failure()

    def test_index_requires_exactly_one_linux_amd64_child(self):
        amd64, arm64 = self.manifest["manifests"]
        for images in ([arm64], [amd64, copy.deepcopy(amd64)],
                       [dict(amd64, digest="not-a-digest")],
                       [dict(amd64, platform={"architecture": "amd64", "os": "windows"})]):
            with self.subTest(images=images):
                self.manifest["manifests"] = images
                self.assert_preserved_on_failure()

    def test_image_configuration_rejects_other_architectures_and_operating_systems(self):
        for architecture, operating_system in (("arm64", "linux"), ("amd64", "windows")):
            with self.subTest(architecture=architecture, operating_system=operating_system):
                self.image_config.update(architecture=architecture, os=operating_system)
                self.assert_preserved_on_failure()

    def test_installation_requires_bootc_and_a_real_version(self):
        for labels in ({}, {"containers.bootc": "0", "org.opencontainers.image.version": "45"},
                       {"containers.bootc": "1", "org.opencontainers.image.version": None},
                       {"containers.bootc": "1", "org.opencontainers.image.version": " "}):
            with self.subTest(labels=labels):
                self.image_config["config"]["Labels"] = labels
                self.assert_preserved_on_failure()

    def test_unreviewed_or_mutable_builder_is_rejected(self):
        lock_path = self.root / "build-scripts/iso-tools.json"
        lock = json.loads(lock_path.read_text())
        for reference in ("quay.io/centos-bootc/bootc-image-builder:latest", "other/repo@" + INDEX):
            with self.subTest(reference=reference):
                lock_path.write_text(json.dumps(lock | {"builder": reference}))
                self.assert_preserved_on_failure()

    def test_ambiguous_installer_update_reference_is_rejected(self):
        template = (self.root / "iso.toml").read_text()
        for modified in (template.replace(ISO.DEFAULT_REFERENCE, "other/repo:latest"),
                         template + "\n# " + ISO.DEFAULT_REFERENCE):
            with self.subTest(template=modified):
                (self.root / "iso.toml").write_text(modified)
                self.assert_preserved_on_failure()

    def test_unknown_manifest_type_is_rejected(self):
        self.manifest["mediaType"] = "application/octet-stream"
        self.assert_preserved_on_failure()


if __name__ == "__main__":
    unittest.main()
