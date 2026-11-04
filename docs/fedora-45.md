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

## Validation and rollback

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
