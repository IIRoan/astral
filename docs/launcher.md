# Vicinae launcher

Astral includes Vicinae from Terra as its default application launcher. Niri's
`Mod+Space` shortcut and Noctalia's launcher bar widget both toggle Vicinae.
Noctalia continues to provide the bar, notifications, lock screen, wallpaper
picker, Control Center, and `Alt+Tab` window switcher.

## Startup and configuration

Niri starts the packaged `vicinae.service` user unit. It inherits the graphical
session environment, restarts the daemon on failure, and stops with the session.
There is no second `vicinae server` process in Niri's startup configuration.

Before the daemon starts, Astral seeds `~/.config/vicinae/settings.json` only if
it does not already exist. The file imports `/usr/share/astral/vicinae-defaults.json`:
image updates can change those defaults while GUI settings in the user file
take precedence. `XDG_CONFIG_HOME` is respected.

The defaults use the Wayland overlay layer so the launcher opens above
fullscreen apps, show a compact search bar before typing, and reset navigation
when closed. Noctalia's `vicinae` community template keeps the colors in sync.
The first theme download requires network access; Vicinae's built-in theme
works before the template is available.

The image grants `cap_dac_override=ep` only to Vicinae's dedicated
`vicinae-input-server`, as required upstream for automatic pasting and snippet
expansion. The main launcher remains unprivileged. This is applied and checked
on every image build, including when Terra updates the helper.

## Existing users

Personal Niri bindings override image defaults. If `~/.config/niri/local.kdl`
still opens Noctalia's launcher, replace that binding with:

```kdl
Mod+Space repeat=false hotkey-overlay-title="Open Launcher" {
    spawn "vicinae" "toggle";
}
```

Inside the existing `binds` block, keep every other shortcut. Validate and reload
with `niri validate` and `niri msg action load-config-file`.

Noctalia GUI overrides take precedence over its managed `config.toml`. In
Settings, disable **Niri overview type to launch**, enable the **Vicinae**
community template under Templates, and set the launcher widget's left-click
action to `exec vicinae toggle` if you previously customized these settings.
Keep any other enabled community templates.

If Vicinae was already configured, add `/usr/share/astral/vicinae-defaults.json`
to its existing `imports` array to opt into Astral's defaults. Existing settings
are never overwritten automatically.

Typing directly in Niri's overview is a Noctalia-specific integration that
opens its built-in launcher. Astral disables it by default; use `Mod+Space` to
open Vicinae from the overview. Re-enabling it restores the Noctalia launcher
for that gesture. Noctalia plugins and launcher search prefixes are not
automatically converted into Vicinae extensions.

## Updates and troubleshooting

Vicinae and its dependencies are part of Astral's image. Daily image builds
pick up Terra updates, and the normal automatic updater or `ajust update`
stages the image for the next reboot. Do not use `rpm-ostree install vicinae`:
host package layering prevents the existing `bootc` updater from upgrading.
Extensions installed through Vicinae's stores have their own lifecycle inside
Vicinae; they are not image packages.

Inspect or restart the launcher with:

```bash
systemctl --user status vicinae.service
journalctl --user -u vicinae.service
systemctl --user restart vicinae.service
vicinae toggle
```

If you use an input method and its suggestions appear behind the launcher,
override `launcher_window.layer_shell.layer` to `top` in Vicinae's settings.
That trades fullscreen visibility for correct input-method popup stacking.

Vicinae initially indexes the configured file-search directories. Narrow those
paths or disable file search in Vicinae's settings if the initial scan is too
expensive.
