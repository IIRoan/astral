"""Exercise build input verification before tools or image contents are used."""

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "a" * 64
SIGNER = "https://github.com/OpenGamingCollective/kernel-packages/.github/workflows/fedora.yaml@refs/tags/v1"

TOOL = r'''#!/usr/bin/python3
import json, os, re, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps([name, *args]) + '\n')
if name == 'cosign':
    if os.environ.get('SIGNATURE_FAILURE') == '1':
        sys.exit(1)
    if '--certificate-identity-regexp' in args:
        pattern = args[args.index('--certificate-identity-regexp') + 1]
        if not re.fullmatch(pattern, os.environ['SIGNER']):
            sys.exit(1)
    print('[]')
elif args[0] in ('digest', 'resolve'):
    print(os.environ['DIGEST'])
elif name == 'crane' and args[0] == 'export':
    sys.stdout.buffer.write(Path(os.environ['BOOTSTRAP']).read_bytes())
elif name == 'oras' and args[0] == 'pull':
    target = Path(args[args.index('--output') + 1])
    target.mkdir()
    (target / 'kernel.rpm').write_text('verified kernel')
else:
    sys.exit(2)
'''


def archive(path, files, mode="w"):
    with tarfile.open(path, mode) as bundle:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o755
            bundle.addfile(member, io.BytesIO(data))


class BuildInputTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.src = self.tmp / "src"
        shutil.copytree(ROOT / "build-scripts", self.src / "build-scripts")
        for profile in ("brew", "jackrabbit"):
            target = self.src / "mkosi.profiles" / profile
            target.mkdir(parents=True)
            shutil.copy(ROOT / "mkosi.profiles" / profile / "mkosi.prepare.chroot", target)
        self.fixtures = self.tmp / "fixtures"
        self.fixtures.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.env = os.environ | {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "SRCDIR": str(self.src),
            "FIXTURES": str(self.fixtures),
            "CALLS": str(self.tmp / "calls"),
            "BOOTSTRAP": str(self.tmp / "bootstrap.tar"),
            "ARCHITECTURE": "x86_64",
            "DIGEST": DIGEST,
            "SIGNER": SIGNER,
        }
        self.stub("uname", "import os; print(os.environ['ARCHITECTURE'])")
        self.stub("curl", r'''
import json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
url = next(arg for arg in args if arg.startswith('https://'))
asset = url.rsplit('/', 1)[1]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps(['curl', url]) + '\n')
if os.environ.get('DOWNLOAD_FAILURE') == '1':
    sys.exit(22)
target = Path(args[args.index('--output') + 1])
shutil.copyfile(Path(os.environ['FIXTURES']) / asset, target)
if os.environ.get('CORRUPT_DOWNLOAD') == asset:
    target.write_bytes(b'corrupt download')
''')
        archive(Path(self.env["BOOTSTRAP"]), {
            "system_files/usr/share/homebrew.tar.zst": b"verified bootstrap",
        })
        # Trust small local fixtures instead of downloading release binaries.
        lock = self.src / "build-scripts/tools.lock"
        entries = []
        self.assets = {}
        for line in lock.read_text().splitlines():
            if line.startswith("#"):
                continue
            tool, arch, version, asset, _ = line.split()
            self.assets[tool, arch] = asset
            path = self.fixtures / asset
            if tool == "cosign":
                path.write_text(TOOL)
            else:
                archive(path, {tool: TOOL.encode()}, "w:gz")
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append(f"{tool} {arch} {version} {asset} {checksum}")
        lock.write_text("\n".join(entries) + "\n")

    def stub(self, name, source):
        path = self.bin / name
        path.write_text("#!/usr/bin/python3\n" + source.lstrip())
        path.chmod(0o755)

    def run_script(self, path, *args, success=True):
        result = subprocess.run(
            ["/usr/bin/bash", str(self.src / path), *args],
            env=self.env, capture_output=True, text=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calls(self, name):
        path = Path(self.env["CALLS"])
        return [call for line in path.read_text().splitlines()
                if (call := json.loads(line))[0] == name] if path.exists() else []

    def prepare(self, profile="brew", **kwargs):
        return self.run_script(f"mkosi.profiles/{profile}/mkosi.prepare.chroot", **kwargs)

    def seed_cache(self, name):
        path = self.src / "cache" / name
        path.mkdir(parents=True)
        (path / "old").write_text("unverified old cache")
        return path

    def test_both_platforms_verify_and_extract_the_same_digest(self):
        for architecture, platform in [("x86_64", "linux/amd64"), ("aarch64", "linux/arm64")]:
            with self.subTest(architecture=architecture):
                self.env["ARCHITECTURE"] = architecture
                self.prepare()
                verification = self.calls("cosign")[-1]
                extraction = self.calls("crane")[-1]
                self.assertEqual(verification[-1], f"ghcr.io/ublue-os/brew@{DIGEST}")
                self.assertEqual(extraction[-2], verification[-1])
                self.assertEqual(extraction[extraction.index("--platform") + 1], platform)
                key = verification[verification.index("--key") + 1]
                self.assertEqual(Path(key).read_bytes(), (ROOT / "build-scripts/ublue-brew.pub").read_bytes())
                payload = self.src / "cache/ublue-brew/usr/share/homebrew.tar.zst"
                self.assertEqual(payload.read_bytes(), b"verified bootstrap")
        self.assertEqual(len([c for c in self.calls("crane") if c[1] == "digest"]), 2)

    def test_invalid_signature_blocks_extraction_even_with_old_cache(self):
        cache = self.seed_cache("ublue-brew")
        self.env["SIGNATURE_FAILURE"] = "1"
        self.prepare(success=False)
        self.assertFalse(any(c[1] == "export" for c in self.calls("crane")))
        self.assertTrue((cache / "old").exists())

    def test_extracted_cache_is_replaced_with_verified_contents(self):
        cache = self.seed_cache("ublue-brew")
        self.prepare()
        self.assertFalse((cache / "old").exists())
        self.prepare()
        self.assertEqual(len(self.calls("cosign")), 2)

    def test_invalid_digest_never_reaches_verification_or_export(self):
        self.env["DIGEST"] = "ghcr.io/another/image:latest"
        self.prepare(success=False)
        self.assertEqual(self.calls("cosign"), [])
        self.assertEqual(len(self.calls("crane")), 1)

    def test_corrupt_downloads_are_not_installed_or_executed(self):
        for tool in ("crane", "cosign", "oras"):
            with self.subTest(tool=tool):
                self.env["CORRUPT_DOWNLOAD"] = self.assets[tool, "x86_64"]
                self.run_script("build-scripts/ensure-tool", tool, success=False)
                self.assertFalse((self.src / "cache" / tool).exists())
                self.assertFalse(list((self.src / "cache/build-tools").glob(f"{tool}-*")))
                self.assertEqual(self.calls(tool), [])

    def test_cached_downloads_are_verified_again(self):
        self.run_script("build-scripts/ensure-tool", "crane")
        artifact = next((self.src / "cache/build-tools").glob("crane-*"))
        artifact.write_bytes(b"tampered cached archive")
        self.prepare(success=False)
        self.assertEqual(self.calls("crane"), [])

    def test_old_executable_cannot_bypass_download_verification(self):
        cache = self.src / "cache"
        cache.mkdir()
        (cache / "crane").write_text("unchecked executable")
        self.run_script("build-scripts/ensure-tool", "crane")
        self.assertEqual((cache / "crane").read_text(), TOOL)
        (cache / "crane").write_text("tampered executable")
        self.run_script("build-scripts/ensure-tool", "crane")
        self.assertEqual((cache / "crane").read_text(), TOOL)
        self.assertEqual(len(self.calls("curl")), 1)

    def test_failed_download_does_not_leave_a_trusted_cache(self):
        self.env["DOWNLOAD_FAILURE"] = "1"
        self.prepare(success=False)
        self.assertEqual(list((self.src / "cache/build-tools").iterdir()), [])

    def test_unknown_architecture_fails_before_downloading(self):
        self.env["ARCHITECTURE"] = "riscv64"
        self.prepare(success=False)
        self.assertEqual(self.calls("curl"), [])

    def test_kernel_signature_rejects_other_repositories_and_branches(self):
        cache = self.seed_cache("linux-ogc")
        for signer in [SIGNER.replace("OpenGamingCollective", "attacker"),
                       SIGNER.replace("refs/tags/v1", "refs/heads/main")]:
            with self.subTest(signer=signer):
                self.env["SIGNER"] = signer
                self.prepare("jackrabbit", success=False)
                self.assertFalse(any(c[1] == "pull" for c in self.calls("oras")))
                self.assertTrue((cache / "old").exists())

    def test_bad_audio_checksum_preserves_existing_caches(self):
        kernel = self.seed_cache("linux-ogc")
        audio = self.seed_cache("valve-audio")
        (self.fixtures / "steamdeck-dsp-1.03-1-any.pkg.tar.zst").write_bytes(b"bad audio")
        self.prepare("jackrabbit", success=False)
        self.assertTrue((kernel / "old").exists())
        self.assertTrue((audio / "old").exists())
        self.assertEqual(self.calls("oras")[-1][-1], self.calls("cosign")[-1][-1])

    @unittest.skipUnless(shutil.which("zstd"), "zstd is required for the audio archive fixture")
    def test_verified_kernel_and_audio_replace_old_caches(self):
        kernel = self.seed_cache("linux-ogc")
        audio = self.seed_cache("valve-audio")
        source = self.tmp / "audio.tar"
        archive(source, {
            "usr/lib/firmware/amd/firmware": b"firmware",
            "usr/share/alsa/ucm2/conf.d/sof-nau8821-max/config": b"audio config",
        })
        asset = self.fixtures / "steamdeck-dsp-1.03-1-any.pkg.tar.zst"
        subprocess.run(["zstd", "-q", str(source), "-o", str(asset)], check=True)
        # The fixture uses a different pinned checksum than the real firmware.
        prepare = self.src / "mkosi.profiles/jackrabbit/mkosi.prepare.chroot"
        prepare.write_text(re.sub(r"\b[a-f0-9]{64}\b", hashlib.sha256(asset.read_bytes()).hexdigest(), prepare.read_text()))
        self.prepare("jackrabbit")
        self.assertFalse((kernel / "old").exists())
        self.assertFalse((audio / "old").exists())
        self.assertEqual((kernel / "kernel.rpm").read_text(), "verified kernel")
        self.assertEqual((audio / "sof_fw/firmware").read_bytes(), b"firmware")


if __name__ == "__main__":
    unittest.main()
