# Remove local NymVPN installations

Astral does not install NymVPN in its system image. A separately installed
Flatpak and manually copied daemon persist across image updates and need to be
removed on the running system.

Close NymVPN, then run these commands from your desktop account. They remove
both Flatpak installation scopes, the manual system daemon, its configuration
and state, and the current user's app permissions. Loose user files go to Trash.
The shared Flathub remote and other applications stay installed.

```bash
set -euo pipefail

app_id=net.nymtech.NymVPN
for scope in --system --user; do
    if flatpak info "$scope" "$app_id" >/dev/null 2>&1; then
        flatpak uninstall "$scope" --delete-data --assumeyes "$app_id"
    fi
done
flatpak override --user --reset "$app_id"
flatpak permission-reset "$app_id"

pkexec /usr/bin/bash -s <<'SH'
set -euo pipefail
if systemctl cat nym-vpnd.service >/dev/null 2>&1; then
    systemctl disable --now nym-vpnd.service
fi
rm -rf -- /etc/systemd/system/nym-vpnd.service \
    /etc/systemd/system/nym-vpnd.service.d \
    /usr/local/bin/nym-vpnd /opt/nym-vpn /etc/nym \
    /var/lib/nym-vpn /var/lib/nym-vpnd
flatpak override --system --reset net.nymtech.NymVPN
systemctl daemon-reload
SH

for path in \
    "$HOME/.local/bin/nym-vpnd" \
    "${XDG_CONFIG_HOME:-$HOME/.config}/nym-vpn-app" \
    "${XDG_CONFIG_HOME:-$HOME/.config}/net.nymtech.vpn" \
    "${XDG_CACHE_HOME:-$HOME/.cache}/nym-vpn-app" \
    "${XDG_DATA_HOME:-$HOME/.local/share}/nym-vpn-app" \
    "${XDG_DATA_HOME:-$HOME/.local/share}/net.nymtech.vpn" \
    "$HOME/.var/app/net.nymtech.NymVPN"; do
    if [[ -e "$path" || -L "$path" ]]; then
        gio trash -- "$path"
    fi
done
```

`/opt` and `/usr/local` are backed by `/var/opt` and `/var/usrlocal` on Astral;
removing these daemon paths also removes those persistent copies.

Check the result:

```bash
flatpak list --app --columns=application,name,installation
systemctl show nym-vpnd.service --property=LoadState,ActiveState
command -v nym-vpnd || true
```

NymVPN should be absent from the Flatpak list, the service should report
`LoadState=not-found` and `ActiveState=inactive`, and `command -v` should print
no daemon path. No image update or reboot is needed for these local installs.
