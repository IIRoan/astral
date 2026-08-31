dotfiles_path := "/usr/share/astral/dotfiles"

default:
    @ajust --choose

# Show image, signature policy, and container status
[group('System')]
status:
    #!/usr/bin/env bash
    set -euo pipefail

    echo "=== Image ==="
    if [[ -e /sysroot/ostree || -e /sysroot/composefs ]] && command -v bootc >/dev/null 2>&1; then
        bootc status || sudo bootc status
    else
        echo "Not a bootc deployment"
    fi

    echo
    echo "=== Update security ==="
    if [[ -f /usr/share/pki/containers/astral.pub ]]; then
        echo "Signing key: /usr/share/pki/containers/astral.pub"
    else
        echo "Signing key: missing"
    fi
    if grep -q 'ghcr.io/iiroan' /etc/containers/policy.json 2>/dev/null \
        && grep -q 'sigstoreSigned' /etc/containers/policy.json 2>/dev/null; then
        echo "Signature policy: ghcr.io/iiroan requires astral.pub"
    else
        echo "Signature policy: not installed"
    fi
    if systemctl is-enabled uupd.timer >/dev/null 2>&1; then
        echo "Automatic updates: enabled"
    else
        echo "Automatic updates: disabled"
    fi

    echo
    echo "=== Containers ==="
    if command -v podman >/dev/null 2>&1; then
        podman --version
    else
        echo "Podman is not installed"
    fi
    if command -v docker >/dev/null 2>&1; then
        echo "Docker CLI: $(command -v docker)"
        docker compose version 2>/dev/null || true
    fi
    if [[ -n "${DOCKER_HOST:-}" ]]; then
        echo "DOCKER_HOST=${DOCKER_HOST}"
    fi
    if [[ -n "${XDG_RUNTIME_DIR:-}" && -S "${XDG_RUNTIME_DIR}/podman/podman.sock" ]]; then
        echo "Podman user socket: ${XDG_RUNTIME_DIR}/podman/podman.sock"
    fi

# Confirm the local signature policy and signing key
[group('System')]
verify-image:
    #!/usr/bin/env bash
    set -euo pipefail

    KEY="/usr/share/pki/containers/astral.pub"
    POLICY="/etc/containers/policy.json"
    REGISTRY_D="/etc/containers/registries.d/iiroan.yaml"

    fail() {
        echo "verify-image: $*" >&2
        exit 1
    }

    [[ -f "${KEY}" ]] || fail "missing ${KEY}"
    [[ -f "${POLICY}" ]] || fail "missing ${POLICY}"
    grep -q 'ghcr.io/iiroan' "${POLICY}" || fail "policy does not pin ghcr.io/iiroan"
    grep -q 'sigstoreSigned' "${POLICY}" || fail "policy does not require Sigstore signatures"
    grep -q '/usr/share/pki/containers/astral.pub' "${POLICY}" || fail "policy does not require astral.pub"
    [[ -f "${REGISTRY_D}" ]] || fail "missing ${REGISTRY_D}"
    grep -q 'use-sigstore-attachments: true' "${REGISTRY_D}" || fail "sigstore attachments are disabled"

    echo "Signature policy requires astral.pub for ghcr.io/iiroan"
    echo "bootc updates will reject unsigned images"

# Check for a signed image update without applying it
[group('System')]
check-update:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -e /sysroot/ostree || -e /sysroot/composefs ]]; then
        pkexec bootc upgrade --check
    else
        echo "Not a bootc system" >&2
        exit 1
    fi

# Simple wrapper over bootc/pompona for updating your system
[group('System')]
update:
    #!/usr/bin/env bash
    if [[ -e "/sysroot/ostree" || -e "/sysroot/composefs" ]] ; then
        pkexec bootc upgrade
    else
        pompona-update
    fi

# Update unmodified dotfiles from the image
[group('Desktop')]
update-dotfiles:
    #!/usr/bin/env bash
    echo "Updating non-overwritten dotfiles to their latest in-image state"
    yes s | chezmoi apply --no-tty --keep-going -S "{{ dotfiles_path }}" --verbose --config "$HOME/.config/astral/chezmoi/chezmoi.toml" &>/dev/null
    echo "Updated dotfiles. This should not have touched anything on your system that you've configured yourself."

_dotfiles-reset $FILE="":
    #!/usr/bin/env bash
    if [ "${FILE}" == "" ] ; then
        echo "This recipe is for resetting a specific file from the config to default again, do not use if you don't know what this does!"
        exit 0
    fi

    chezmoi apply --force -S "{{ dotfiles_path }}" --verbose --config "$HOME/.config/astral/chezmoi/chezmoi.toml" "${FILE}"

# Reset every managed config file to the image default
[group('Desktop')]
reset-all-configs:
    #!/usr/bin/env bash
    if gum confirm --default=no "Are you sure you want to reset all your configuration?" ; then
        chezmoi apply --force -S "{{ dotfiles_path }}" --verbose --config "$HOME/.config/astral/chezmoi/chezmoi.toml"
    fi

# Toggle display of the user-motd in terminal
[group('Desktop')]
toggle-user-motd:
    #!/usr/bin/bash
    MOTD_FLAG="${XDG_CONFIG_HOME:-$HOME/.config}/astral/no-show-user-motd"
    if [ -e "${MOTD_FLAG}" ] ; then
        if gum confirm "Would you like to enable the MOTD?" ; then
            rm -f "${MOTD_FLAG}"
            echo "MOTD will now be shown on login."
            exit 0
        fi
    fi
    if gum confirm "Would you like to disable the MOTD?" ; then
        mkdir -p "$(dirname "${MOTD_FLAG}")"
        touch "${MOTD_FLAG}"
        echo "MOTD will not be shown anymore"
        exit 0
    fi

# Toggle display autorotation
[group('Desktop')]
toggle-autorotation:
    #!/usr/bin/env bash

    if systemctl is-enabled --quiet --user iio-niri ; then
        if gum confirm "Would you like to disable autorotation?" ; then
            systemctl disable --quiet --now --user iio-niri.service
            systemctl mask --quiet --now --user iio-niri.service
            echo "Disabled autorotation"
            exit 0
        fi
    else
        if gum confirm "Would you like to enable autorotation?" ; then
            systemctl unmask --quiet --user iio-niri.service
            systemctl enable --quiet --now --user iio-niri.service
            echo "Enabled autorotation"
            exit 0
        fi
    fi

# Toggle automatic dotfile updates
[group('Desktop')]
toggle-automatic-dotfiles:
    #!/usr/bin/env bash

    if systemctl is-enabled --quiet --user chezmoi-update.timer ; then
        if gum confirm --default=no "Would you like to disable automatic dotfile updates?" ; then
            systemctl disable --quiet --now --user chezmoi-update.timer
            systemctl mask --quiet --now --user chezmoi-update.timer
            systemctl disable --quiet --now --user chezmoi-init.service
            systemctl mask --quiet --user chezmoi-init.service
            echo "Disabled dotfile updates"
            exit 0
        fi
    else
        if gum confirm "Would you like to enable dotfiles updates?" ; then
            systemctl unmask --quiet --user chezmoi-init.service
            systemctl enable --quiet --user chezmoi-init.service
            systemctl unmask --quiet --user chezmoi-update.timer
            systemctl enable --quiet --now --user chezmoi-update.timer
            echo "Enabled dotfile updates"
            exit 0
        fi
    fi

# Toggle the fcitx5 input method
[group('Desktop')]
toggle-fcitx5:
    #!/usr/bin/env bash

    if systemctl is-enabled --quiet --user fcitx5 ; then
        if gum confirm "Would you like to disable fcitx5?" ; then
            systemctl disable --quiet --now --user fcitx5.service
            systemctl mask --quiet --now --user fcitx5.service
            echo "Disabled fcitx5"
            exit 0
        fi
    else
        if gum confirm "Would you like to enable fcitx5?" ; then
            systemctl unmask --quiet --user fcitx5.service
            systemctl enable --quiet --now --user fcitx5.service
            echo "Enabled fcitx5"
            exit 0
        fi
    fi

# Reinstall the default Flatpaks
[group('Apps')]
preinstalled-flatpaks:
    #!/usr/bin/env bash
    if gum confirm "Would you like to (re)install the preinstalled flatpaks?" ; then
        flatpak preinstall --reinstall
    fi

# Reset niri to the default configuration
[group('Desktop')]
reset-niri:
    #!/usr/bin/env bash
    if gum confirm "Are you sure you want to reset your niri configuration?" ; then
        ajust _dotfiles-reset "${HOME}/.config/niri"
    fi

# Sync the greeter with this user's Noctalia settings
[group('Desktop')]
update-greeter:
    noctalia msg greeter-sync

# Turn automatic updates on or off
[group('System')]
toggle-updates ACTION="prompt":
    #!/usr/bin/bash
    ACTION="{{ ACTION }}"
    CURRENT_STATE="$(systemctl is-enabled uupd.timer)"

    enable_updates() {
        sudo systemctl enable uupd.timer
        echo "Updates have been enabled"
    }

    disable_updates() {
        sudo systemctl disable uupd.timer
        echo "Updates have been disabled"
    }

    case "${ACTION}" in
        enable|on)
            enable_updates
            ;;
        disable|off)
            disable_updates
            ;;
        prompt)
            if [ "${CURRENT_STATE}" == "enabled" ] ; then
                if gum confirm --default=no "Would you like to disable automatic updates?" ; then
                    disable_updates
                fi
            else
                if gum confirm --default=yes "Would you like to enable automatic updates?" ; then
                    enable_updates
                fi
            fi
            ;;
        *)
            echo "Unknown action: ${ACTION} (use prompt, enable, or disable)" >&2
            exit 1
            ;;
    esac

alias toggle-tpm2 := toggle-luks-tpm2

# TPM2 auto-unlock toggle
[group('System')]
toggle-luks-tpm2:
    @/usr/bin/luks-tpm2-autounlock

# Check for local overrides
[group('System')]
check-local-overrides:
    #!/usr/bin/bash
    RESET="\x1b[0m"
    if [ "${NO_COLOR}" == 1 ]; then
        BLUE_COLOR=""
        GREEN_COLOR=""
        YELLOW_COLOR=""
    else
        BLUE_COLOR="${BLUE_COLOR:-\x1b[38;5;117m}"
        GREEN_COLOR="${GREEN_COLOR:-\x1b[38;5;85m}"
        YELLOW_COLOR="${YELLOW_COLOR:-\x1b[93m}"
    fi

    sudo diff -qr \
        --suppress-common-lines \
        --color="always" \
        --exclude "passwd*" \
        --exclude "group*" \
        --exclude="subgid*" \
        --exclude="subuid*" \
        --exclude="machine-id" \
        --exclude="adjtime" \
        --exclude="fstab" \
        --exclude="system-connections" \
        --exclude="shadow*" \
        --exclude="gshadow*" \
        --exclude="ssh_host*" \
        --exclude="cmdline" \
        --exclude="crypttab" \
        --exclude="hostname" \
        --exclude="localtime" \
        --exclude="locale*" \
        --exclude="*lock" \
        --exclude=".updated" \
        --exclude="*LOCK" \
        --exclude="vconsole*" \
        --exclude="00-keyboard.conf" \
        --exclude="grub" \
        --exclude="system.control*" \
        --exclude="cdi" \
        --exclude="default.target" \
        /usr/etc /etc 2>/dev/null | sed \
            -e '/Binary\ files\ /d ; /Common subdirectories/d' \
            -e "/Files/ s/^/${BLUE_COLOR}&/g" \
            -e "/Files/ s/\$/&${RESET}/g" \
            -e "/Only in/ s/^/${GREEN_COLOR}&/g" \
            -e "/Only in/ s/\$/&${RESET}/g" \
            -e "s/\:.*/${RESET}${YELLOW_COLOR}&${RESET}/g"

# Gather device info to a pastebin
[group('System')]
generate-bug-report:
    #!/usr/bin/bash
    if ! gum confirm "Submit information to fpaste?" ; then
        exit 0
    fi

    REPORT_DATA="$(mktemp)"
    trap 'rm -f "${REPORT_DATA}"' EXIT
    cat &> "${REPORT_DATA}" <<EOF
    --#--# sudo bootc status --verbose:
    $(sudo bootc status --verbose)
    --#--# fpaste --sysinfo --printonly:
    $(fpaste --sysinfo --printonly)
    --#--# flatpak list --columns=application,version,options:
    $(flatpak list --columns=application,version,options)
    --#--# podman info --format json:
    $(podman info --format json 2>/dev/null || echo "podman unavailable")
    --#-# BIOS info:
    Manufacturer:
    $(dmidecode -s baseboard-manufacturer 2>/dev/null)
    Product Name:
    $(dmidecode -s baseboard-product-name 2>/dev/null)
    Version:
    $(dmidecode -s bios-version 2>/dev/null)
    Release Date:
    $(dmidecode -s bios-release-date 2>/dev/null)
    EOF

    gzip -9c "${REPORT_DATA}" | base64 | fpaste -t "astral report $(date +%Y%m%d%H%M%S)" --private=1
