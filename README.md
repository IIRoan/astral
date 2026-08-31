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
- Integrated wallpapers, logos, and default dotfiles. This repository has no
  asset or dotfile submodules.
- AMD/Intel and NVIDIA bootc image workflows for AMD64 and ARM64.

## Install

Rebase an existing Fedora Atomic installation to the standard image:

```bash
sudo bootc switch ghcr.io/iiroan/astral:latest
```

For supported NVIDIA GPUs, use:

```bash
sudo bootc switch ghcr.io/iiroan/astral-nvidia:latest
```

The image must be published and signed by the repository workflows before these
commands can be used. The NVIDIA kernel module is not signed, so Secure Boot is
not supported by the NVIDIA image.

## Desktop Defaults

Astral starts Noctalia directly from Niri and reserves these shortcuts:

| Shortcut | Action |
| --- | --- |
| `Mod+Space` | Noctalia launcher |
| `Mod+S` | Control center |
| `Mod+Ctrl+Comma` | Noctalia settings |
| `Mod+Alt+L` | Lock session |
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

## Build

Install `mkosi`, `just`, and Podman, then build the standard bootc image:

```bash
just build
```

The GitHub Actions workflows publish multi-architecture images to GHCR on pushes
to the default branch.

## Upstream And Licensing

Astral is derived from
[Zirconium](https://github.com/zirconium-dev/zirconium). The main project
remains under the AGPL-3.0-or-later license. Vendored assets and dotfiles retain their
Apache-2.0 license files and upstream history attribution.
