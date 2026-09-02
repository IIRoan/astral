# Astral

<img src="assets/logos/logo-astral.svg" width="180" alt="Astral logo" />

Astral is an opinionated Fedora bootc desktop built around Niri and Noctalia. It
keeps Zirconium's container-focused image foundation while giving the desktop,
developer tooling, image names, defaults, and release pipeline an independent
Astral identity.

## Included

- Niri with Noctalia v5 and Noctalia Greeter.
- Rootless Podman with Docker CLI compatibility.
- `docker compose` backed by `podman-compose`, with the Podman user socket
  enabled for Docker API-compatible tools.
- Visual Studio Code from Microsoft's official RPM repository.
- Virtual Machine Manager with a system QEMU/KVM backend and default NAT
  networking.
- Integrated wallpapers, logos, and default dotfiles. This repository has no
  asset or dotfile submodules.
- A stable Fedora 44 bootc image for AMD64 and ARM64.

## Install

Rebase an existing Fedora Atomic installation to the standard image:

```bash
sudo bootc switch ghcr.io/iiroan/astral:latest
```

The image must be published and signed by the repository workflow before this
command can be used.

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

## Build

Install `mkosi`, `just`, and Podman, then build the standard bootc image:

```bash
just build
```

GitHub Actions publishes the multi-architecture image to GHCR on pushes to the
default branch and on a weekly schedule so Fedora security updates land without a
manual commit. The separate manual ISO workflow builds an AMD64 installer and
stores it as a seven-day Actions artifact.

## Update Security

Published architecture images and the final multi-architecture manifest are
signed with Astral's Sigstore private key. The installed containers policy
requires signatures matching the vendored `astral.pub` key for images under
`ghcr.io/iiroan`, so `bootc` updates reject images that are not signed by
Astral. Each published manifest also receives GitHub-hosted SLSA build
provenance tied to its immutable digest. After publish, CI verifies the
Sigstore signature against `astral.pub` and the provenance attestation before
the workflow succeeds. Workflow actions are pinned to full commit hashes, and
the signing secret is only exposed to publishing steps.

## Upstream And Licensing

Astral is derived from
[Zirconium](https://github.com/zirconium-dev/zirconium). The main project
remains under the AGPL-3.0-or-later license. Vendored assets and dotfiles retain their
Apache-2.0 license files and upstream history attribution.
