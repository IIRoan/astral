# Astral

<img src="assets/logos/logo-astral-badge.svg" width="180" alt="Astral logo" />

[![Artifact Hub](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/astral)](https://artifacthub.io/packages/search?repo=astral)

Astral is an opinionated Fedora bootc desktop built around Niri and Noctalia, on
top of Zirconium's container-focused image foundation.

## Included

- Niri with Noctalia v5 and Noctalia Greeter.
- Rootless Podman with Docker CLI compatibility.
- `docker compose` backed by `podman-compose`, with the Podman user socket
  enabled for Docker API-compatible tools.
- Virtual Machine Manager with a system QEMU/KVM backend and default NAT
  networking.
- Integrated wallpapers, logos, and default dotfiles.
- A stable Fedora 45 bootc image for AMD64 and ARM64.

## Install

Switch an existing Fedora Atomic installation to Astral:

```bash
sudo bootc switch ghcr.io/iiroan/astral:latest
```

For existing Fedora 44 installations, follow the [Fedora 45 upgrade guide](docs/fedora-45.md)
to check container networking and local login overrides before updating.

## Desktop Defaults

Astral starts Noctalia directly from Niri and reserves these shortcuts:

| Shortcut | Action |
| --- | --- |
| `Mod+Space` | Noctalia launcher |
| `Mod+S` | Control center |
| `Mod+Ctrl+Comma` | Noctalia settings |
| `Mod+L` | Lock session |
| `Alt+Tab` | Noctalia window switcher |
| `Mod+T` | Terminal |
| `Mod+E` | File manager |

Put personal Niri overrides in `~/.config/niri/local.kdl` or system-wide
overrides in `/etc/niri/local.kdl`. Curated Noctalia defaults live in
`~/.config/noctalia/config.toml`; GUI changes are stored separately by
Noctalia under `~/.local/state/noctalia/`.

## Containers

Both command styles use Podman:

```bash
podman compose up
docker compose up
```

Astral installs `podman-docker` and `podman-compose`, selects
`/usr/bin/podman-compose` as Podman's Compose provider, enables
`podman.socket` for each user, and exports the rootless socket through
`DOCKER_HOST`.

## System commands

On a running system, `ajust` is the user-facing command menu:

```bash
ajust
ajust status
ajust verify-image
ajust update
ajust --help
```

## Updates

The system image and Flatpaks update daily. A new image is staged until your
next normal reboot; automatic updates never restart your session. `ajust status`
shows update health, and a desktop notification repeats daily while an update
failure or pending reboot is unresolved. Firmware is checked daily and applied
with `ajust update-firmware`.

Homebrew's Brewfile dump and cleanup leave out Cargo, Flatpak, Go, Krew, npm,
uv, and VS Code packages; see `/etc/homebrew/brew.env`.

## Build

Install `mkosi`, `just`, and Podman, then build the image:

```bash
just build
```

The build runs in a Fedora 45 tools tree, independent of the host distribution.
Downloaded tools are checked against the SHA-256 hashes in
`build-scripts/tools.lock`, and Homebrew's bootstrap image is verified with its
[upstream signing key](https://github.com/ublue-os/brew/blob/main/cosign.pub).

GitHub Actions publishes the image to GHCR on every push to `main` and daily, so
Fedora security updates land without a manual commit. A manual workflow builds
an AMD64 installer ISO.

## Update Security

Every published image is signed with Astral's Sigstore key. The installed
container policy requires signatures matching `astral.pub` for images under
`ghcr.io/iiroan`, so `bootc` refuses updates that Astral did not sign. Each
image also carries GitHub SLSA build provenance, and CI verifies the signature
and provenance before a release counts as published. `ajust verify-image`
checks the local signature policy.

TPM disk auto-unlock is configured explicitly with `ajust toggle-tpm2`.
The setup helper requires Secure Boot/shim measurements and a verified recovery
credential before enrolling PCRs 7+14. See the [TPM recovery and validation guide](docs/tpm-unlock.md)
before replacing an existing enrollment.

## Upstream And Licensing

Astral is derived from
[Zirconium](https://github.com/zirconium-dev/zirconium). The main project
remains under the AGPL-3.0-or-later license. Vendored assets and dotfiles retain their
Apache-2.0 license files and upstream history attribution.
