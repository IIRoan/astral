# Noctalia launcher

Astral uses Noctalia's built-in application launcher. Press `Mod+Space` or use
Noctalia's launcher widget to open it. Launcher settings, colors, session actions,
and updates are managed by Noctalia; no separate launcher daemon is needed.

## Returning from an image with Vicinae

Existing user overrides are preserved across image updates. If `Mod+Space`
still starts Vicinae, change its binding in `~/.config/niri/local.kdl` to:

```kdl
Mod+Space repeat=false hotkey-overlay-title="Application Launcher" {
    spawn "noctalia" "msg" "panel-toggle" "launcher";
}
```

Validate and reload the Niri configuration:

```sh
niri validate --config ~/.config/niri/config.kdl
niri msg action load-config-file
```

In Noctalia, restore the launcher widget's default action if it was customized
to `exec vicinae toggle`, disable the Vicinae community theme template, and
re-enable **Niri overview type to launch** if desired. Keep any other enabled
theme templates and launcher preferences.

While running an older image that still starts Vicinae, stop and mask its user
service to prevent it starting again at login:

```sh
systemctl --user mask --now vicinae.service
```

The next image without the Vicinae integration removes its package and session
startup hook. Existing personal Vicinae settings are not deleted by an image
update; they can be archived or removed independently.
