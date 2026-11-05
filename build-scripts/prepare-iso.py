#!/usr/bin/env python3
"""Verify an immutable Astral source before preparing an ISO build."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib


REPOSITORY = "ghcr.io/iiroan/astral"
DEFAULT_REFERENCE = REPOSITORY + ":latest"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}


def command(arguments):
    return subprocess.check_output(arguments, text=True, stdin=subprocess.DEVNULL)


def verify(cosign, key, reference):
    signatures = json.loads(command([
        str(cosign), "verify", "--key", str(key),
        "--insecure-ignore-tlog=true", reference,
    ]))
    expected_digest = reference.split("@", 1)[1]
    for signature in signatures:
        critical = signature.get("critical", {})
        identity = critical.get("identity", {}).get("docker-reference", "")
        same_repository = identity == REPOSITORY or identity.startswith((REPOSITORY + ":", REPOSITORY + "@"))
        if same_repository and critical.get("image", {}).get("docker-manifest-digest") == expected_digest:
            return
    raise ValueError("The verified signature does not identify this Astral repository and digest")


def prepare(root, digest, config_path, metadata_path, crane, cosign):
    # Reject malformed input before invoking tools or replacing existing output.
    if digest and not DIGEST.fullmatch(digest):
        raise ValueError("Specify a sha256 digest, not an image tag or another repository")
    pinned = bool(digest)
    if not digest:
        digest = command([str(crane), "digest", DEFAULT_REFERENCE]).strip()
    if not DIGEST.fullmatch(digest):
        raise ValueError("Registry returned an invalid source digest")
    requested = REPOSITORY + "@" + digest
    verify(cosign, root / "astral.pub", requested)
    manifest = json.loads(command([str(crane), "manifest", requested]))
    media_type = manifest.get("mediaType")
    if media_type in INDEX_TYPES:
        images = [m for m in manifest["manifests"] if m.get("platform", {}).get("os") == "linux"
                  and m.get("platform", {}).get("architecture") == "amd64"]
        if len(images) != 1 or not DIGEST.fullmatch(images[0]["digest"]):
            raise ValueError("Source index must contain exactly one Linux AMD64 image")
        source = REPOSITORY + "@" + images[0]["digest"]
        verify(cosign, root / "astral.pub", source)
    elif media_type in MANIFEST_TYPES:
        source = requested
    else:
        raise ValueError("Unsupported source manifest type")
    config = json.loads(command([str(crane), "config", source]))
    if config.get("architecture") != "amd64" or config.get("os") != "linux":
        raise ValueError("ISO source must be a Linux AMD64 image")
    labels = config.get("config", {}).get("Labels", {})
    version = labels.get("org.opencontainers.image.version")
    if labels.get("containers.bootc") != "1" or not isinstance(version, str) or not version.strip():
        raise ValueError("Source must be a bootc image with a nonempty installation version")
    tools = json.loads((root / "build-scripts/iso-tools.json").read_text())
    for name, repository in (("builder", "quay.io/centos-bootc/bootc-image-builder"),
                             ("branding", "registry.fedoraproject.org/fedora")):
        if not re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", tools[name]):
            raise ValueError("ISO tools must use the reviewed repositories and immutable digests")
    install_reference = source if pinned else DEFAULT_REFERENCE
    template = (root / "iso.toml").read_text()
    contents = tomllib.loads(template)["customizations"]["installer"]["kickstart"]["contents"]
    if contents.count(DEFAULT_REFERENCE) != 1 or template.count(DEFAULT_REFERENCE) != 1:
        raise ValueError("Expected exactly one Astral update reference in the installer template")
    rendered = template.replace(DEFAULT_REFERENCE, install_reference)
    metadata = {
        "requested_image": requested, "source_image": source,
        "install_reference": install_reference, "pinned_source": pinned,
        "version": version, "signatures_verified": True,
        "builder_image": tools["builder"], "branding_image": tools["branding"],
    }
    # All verification succeeds before either reviewable output is replaced.
    for path, data in ((config_path, rendered), (metadata_path, json.dumps(metadata, indent=2) + "\n")):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digest", default="")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    metadata = prepare(root, args.digest, args.config, args.metadata, root / "cache/crane", root / "cache/cosign")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
