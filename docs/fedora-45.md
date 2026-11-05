# Fedora 45 upgrade

Astral builds its standard AMD64 and ARM64 images and the mkosi tools tree on
Fedora 45. Existing installations receive the image through the normal bootc
update path; no reinstall is needed. Complete these checks before staging it.

## Container networking

Fedora 45 uses Podman 6 and Netavark 2, which remove the iptables firewall
backend. Check system and user configuration, including drop-ins, for an
explicit `firewall_driver = "iptables"` setting:

```bash
rg -n 'firewall_driver|slirp4netns|database_backend' \
    /etc/containers ~/.config/containers
podman info --format '{{.Host.DatabaseBackend}} {{.Host.CgroupVersion}} {{.Host.RootlessNetworkCmd}}'
```

Back up any file you edit, remove the explicit iptables override, and let
Netavark choose its supported default. Preserve other container settings.
Stop and restart the affected development containers deliberately, then test
DNS, outgoing connections, published ports, and communication between services.
Netavark 2 also isolates different bridge networks by default; explicitly connect
services that need to communicate to a shared network.

Podman 6 also removes BoltDB, cgroups v1 and slirp4netns support. An existing
installation should already use SQLite, cgroups v2 and pasta. Follow
[Podman's migration guidance](https://fedoraproject.org/wiki/Changes/Podman6)
if it still uses a removed component; do not reset container storage to work
around a migration error.

## Login and VPN configuration

Greetd's vendor PAM stack now lives at `/usr/lib/pam.d/greetd`. Astral enables
its GNOME Keyring hooks there and keeps the `greetd-spawn` wrapper. The image
does not ship a competing `/etc/pam.d/greetd`. A locally modified file at that
path takes precedence: compare it with the new vendor stack before deciding
whether to keep it. Preserve any deliberate authentication changes.

KMSCON is included on Fedora 45. Greetd continues to reserve tty1 and conflicts
with `kmsconvt@tty1.service`; other virtual terminals remain available.

The retired NetworkManager-vpnc plugin is removed. Migrate any saved Cisco
IPsec VPN profiles to a supported alternative before upgrading; the separate
`vpnc` command-line package remains available. See
[Fedora's release announcement and VPN migration notice](https://www.redhat.com/en/blog/announcing-fedora-45).

## Image signature policy

The vendor signature policy and registry configuration now live in
`/usr/share/containers`. Local `/etc/containers` overrides still take precedence.
Compatibility links under `/usr/share/factory/etc/containers` keep Fedora 44's
existing `/etc` factory links working. `ajust verify-image` checks the `/etc`
policy when present and otherwise uses the vendor location.

## Persistent service state

Fedora 45 enforces TuneD's power-profile translation daemon with SELinux.
Astral migrates its previous `/etc/tuned/ppd.conf` factory symlink to a regular
configuration file and restores the policy labels on persistent TuneD logs.
Administrator configuration files are preserved. SELinux remains enforcing.

The legacy rechunker group repair remains enabled. Its final ownership repair
only processes writable `/etc`, `/var`, `/run` and `/tmp` paths, avoiding a
Fedora 45 tmpfiles rule that attempts to change the immutable root directory.

Fresh installations also create and label GSSproxy's private state directories
before its service starts. Each architecture image carries the same nonempty
OCI version and creation time as the published index. This gives bootc and
bootupd a usable installation version, including on a fresh installation.

## Upstream comparison

The implementation was compared with Zirconium's
[Fedora 45 upgrade](https://github.com/zirconium-dev/zirconium/pull/408)
and upstream main at `9412ad63c44b60132fab1d0c270fc6bdea64a8c1`.

| Upstream change | Astral behavior |
| --- | --- |
| Fedora 45 image and explicit Fedora 45 tools tree | Both architectures use these releases. |
| KMSCON packages, vendor greetd PAM, vendor container policy | Included, with Astral's login wrapper, keyring hooks and Fedora 44 policy compatibility. |
| Obsolete chunkah creation label | Removed; mkosi supplies matching version and creation labels on the native images. |
| Official Cisco OpenH264 repository | Included; image tests encode and decode an actual test video. |
| Gutenprint CUPS drivers, PRs [443](https://github.com/zirconium-dev/zirconium/pull/443) and [445](https://github.com/zirconium-dev/zirconium/pull/445) | `gutenprint-cups` is included. |
| Official Tailscale stable repository | Included with package and repository metadata GPG checks and upstream's repository priority. |
| Terra repository cleanup and metadata signatures | Obsolete multimedia repository removed; Terra metadata and packages require GPG verification. |
| DMS package and repository changes | Astral keeps its Noctalia desktop. |
| Newly listed packages previously removed from Astral | Deliberate Astral removals, including Valent, Orca, IBus and mobile broadband services, stay removed. |
| Development mkosi revision | Astral retains pinned stable mkosi 27.1, tested against both Fedora 45 architectures. |
| Jackrabbit bootstrap `--noverify` workaround | Astral retains signature verification; the gaming profile is outside the standard image. |

## Candidate installer

Build the ISO from an explicitly selected signed candidate digest:

```bash
gh workflow run build-disk.yml --ref feat/fedora-45 \
    -f source-digest=sha256:REVIEWED_CANDIDATE_INDEX_OR_AMD64_DIGEST
```

The workflow verifies Astral's signatures on the requested index and its AMD64
image before extracting keys or building the ISO. All those operations use the
same immutable AMD64 digest. The candidate installer retains that digest as its
signed update reference, so it cannot silently switch back to Fedora 44's
`latest` channel. Failure to set the signed update reference aborts the installer.
Without `source-digest`, the production channel is resolved
once for building and the installed system follows `latest` as usual.

The builder and Fedora branding images are pinned in
[`build-scripts/iso-tools.json`](../build-scripts/iso-tools.json). Updating them
requires reviewing new official image digests and repeating an installer boot
test. The artifact contains the ISO, its checksum, and source metadata recording
the source and tool digests. Building this artifact does not promote the image
or install it on the build machine.

Fedora 45's RPM keys live in `/usr/share/pki/rpm-gpg`. The ISO workflow extracts
them from the verified source image and makes them available at both the vendor
path and the older `/etc/pki/rpm-gpg` path used by builder components.

The legacy ISO builder also uses DNF 4, which cannot discover Fedora 45's
`/usr/share/dnf5/repos.d` directory. A separate build container exposes copies
of those signed repository definitions at the older path. It is derived
offline from the verified source. The installer payload remains the original
digest. Artifact metadata records the build recipe hash and local build image
ID, and the artifact also includes the build manifest.

## Validation and rollback

Check `df -h /boot` before staging and leave room for the new image's kernel
and initramfs alongside the pinned Fedora 44 deployment. Small boot partitions,
including bootc's generic 510 MiB TPM layout, can run out of space at shutdown
even after staging succeeds. Provision at least a 1 GiB boot partition for a
new installation and check available space on existing installations.

After reboot, confirm the expected image is actually booted. If Fedora 44
remains active, inspect `journalctl -b -1 -u ostree-finalize-staged.service`;
a successful staging command does not prove boot finalization succeeded.
Keep the working rollback deployment and repair capacity before retrying;
do not delete kernel files by hand.

PR runs build and test both images without publishing. `Verify published image`
is therefore skipped there. The other skipped jobs are housekeeping:
`keepalive` maintains scheduled workflows, `report` tracks production build
failures, and Dependabot's `enable` only handles dependency PRs.

To test the publishing path before merging, explicitly dispatch a candidate
run on the reviewed branch:

```bash
gh workflow run build-standard-bootc.yaml --ref feat/fedora-45 -f candidate=true
```

This publishes only `candidate-RUN_ID-RUN_ATTEMPT` tags and their commit/architecture
variants. It does not change `latest`, Fedora-version or date tags. Publishing
guards reject PR events and candidate tags that do not belong to that run. The
candidate run verifies signatures on the index and both architectures, and
binds the index provenance to the exact source commit, branch and reusable
workflow. Match the run's head SHA to the reviewed PR head before using it.

Use the candidate's immutable digest for an isolated Fedora 44 → 45 VM upgrade,
reboot and rollback rehearsal before promoting the change. A container test
does not replace this boot test or validation on the target hardware.

Before deployment, require the PR's maintenance tests, software-TPM recovery
tests, both native image builds, bootc lint, Flatpak static-delta update checks,
and Fedora 45 image compatibility checks to pass. The compatibility check runs
inside disposable image containers and exercises real Podman policy lookup for
fresh installations, existing factory links, and administrator overrides. It
also checks the packaged greeter stack and Niri configuration; it does not
exercise a graphical login or hardware boot.

The image checks also exercise TuneD configuration migration and administrator
overrides, and reproduce the group-repair failure in an isolated mount namespace
with a read-only root and writable state directories.

Both architecture builds also run the disposable LUKS/vTPM recovery test with
the image's own systemd and cryptsetup. Software-TPM test tools are installed
only in the disposable containers and are not added to the published image.

Keep the known-good Fedora 44 deployment pinned and retain recovery credentials
and backups before staging the signed Fedora 45 image. After a normal reboot,
verify the intended digest is booted, login and keyring unlocking work, displays
and Wi-Fi work, containers pass the networking checks above, and system/user
services are healthy. Run a disposable TPM/recovery check and a Flatpak update
check against the installed image. Image updates do not re-enroll the real
disk's TPM token; follow [the TPM guide](tpm-unlock.md) separately for enrollment.
