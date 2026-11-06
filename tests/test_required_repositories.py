"""Required package sources must fail a build instead of allowing a fallback."""

import configparser
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
REPOSITORIES = (
    ("yalter-niri.repo", "copr:copr.fedorainfracloud.org:yalter:niri", "niri"),
    ("lizardbyte-beta.repo", "copr:copr.fedorainfracloud.org:lizardbyte:beta", "Sunshine"),
)


class RequiredRepositoryTests(unittest.TestCase):
    def test_required_sources_explicitly_reject_unavailability(self):
        for filename, repository, _ in REPOSITORIES:
            with self.subTest(repository=repository):
                config = configparser.ConfigParser(interpolation=None)
                config.read(ROOT / "mkosi.sandbox/etc/yum.repos.d" / filename)
                self.assertFalse(config[repository].getboolean("skip_if_unavailable", fallback=True))
                self.assertTrue(config[repository].getboolean("gpgcheck"))

    @unittest.skipUnless(shutil.which("dnf5"), "DNF5 is needed for the isolated repository outage check")
    def test_outage_blocks_fallback_with_real_dnf(self):
        with tempfile.TemporaryDirectory(prefix="astral-repo-policy-") as directory:
            root = Path(directory)
            repos = root / "repos"
            repos.mkdir()
            fallback = root / "fallback/repodata"
            fallback.mkdir(parents=True)
            # Repoquery only reads metadata; these package entries need no RPMs.
            packages = "".join(f'''<package type="rpm">
              <name>{name}</name><arch>noarch</arch><version epoch="0" ver="1" rel="1"/>
              <checksum type="sha256" pkgid="YES">{'a' * 64}</checksum>
              <summary>Fallback fixture</summary><description>Fallback fixture</description>
              <packager>Astral tests</packager><url/>
              <time file="0" build="0"/><size package="1" installed="1" archive="1"/>
              <location href="Packages/{name}.rpm"/>
              <format><rpm:license>MIT</rpm:license><rpm:vendor>Astral tests</rpm:vendor>
              <rpm:group>System</rpm:group><rpm:buildhost>localhost</rpm:buildhost>
              <rpm:sourcerpm>{name}.src.rpm</rpm:sourcerpm><rpm:header-range start="0" end="1"/>
              </format></package>''' for _, _, name in REPOSITORIES)
            primary = ('''<?xml version="1.0" encoding="UTF-8"?>
              <metadata xmlns="http://linux.duke.edu/metadata/common"
                xmlns:rpm="http://linux.duke.edu/metadata/rpm" packages="2">'''
                       + packages + "</metadata>").encode()
            compressed = gzip.compress(primary, mtime=0)
            (fallback / "primary.xml.gz").write_bytes(compressed)
            (fallback / "repomd.xml").write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
              <repomd xmlns="http://linux.duke.edu/metadata/repo"><revision>1</revision>
              <data type="primary">
              <checksum type="sha256">{hashlib.sha256(compressed).hexdigest()}</checksum>
              <open-checksum type="sha256">{hashlib.sha256(primary).hexdigest()}</open-checksum>
              <location href="repodata/primary.xml.gz"/><timestamp>1</timestamp>
              <size>{len(compressed)}</size><open-size>{len(primary)}</open-size>
              </data></repomd>''')
            (repos / "fallback.repo").write_text(
                f"[fallback]\nname=Fallback fixture\nbaseurl={(root / 'fallback').as_uri()}\n"
                "enabled=1\ngpgcheck=0\nrepo_gpgcheck=0\n"
            )
            config = root / "dnf.conf"
            config.write_text("[main]\nplugins=0\n")

            for filename, repository, package in REPOSITORIES:
                with self.subTest(repository=repository):
                    shutil.copyfile(ROOT / "mkosi.sandbox/etc/yum.repos.d" / filename, repos / "required.repo")
                    command = [
                        "dnf5", f"--config={config}", f"--installroot={root / 'installroot'}",
                        "--releasever=45", f"--setopt=reposdir={repos}",
                        f"--setopt=cachedir={root / 'cache'}", f"--setopt=logdir={root / 'logs'}",
                        f"--setopt=persistdir={root / 'state'}", "--setopt=optional_metadata_types=",
                        f"--repo={repository}", "--repo=fallback",
                        f"--setopt={repository}.baseurl={(root / 'unavailable').as_uri()}",
                    ]

                    def query(*options):
                        return subprocess.run(
                            [*command, *options, "repoquery", "--available", "--queryformat=%{name}", package],
                            env=os.environ | {"LC_ALL": "C"}, text=True, capture_output=True, timeout=30,
                        )

                    # Demonstrate that skipping this same outage allows a different source.
                    skipped = query(f"--setopt={repository}.skip_if_unavailable=True")
                    self.assertEqual(skipped.returncode, 0, skipped.stdout + skipped.stderr)
                    self.assertIn(package, skipped.stdout.splitlines())
                    required = query()
                    self.assertNotEqual(required.returncode, 0, required.stdout + required.stderr)
                    self.assertIn(repository, required.stderr)
                    self.assertNotIn(package, required.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
