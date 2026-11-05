# Per-monitor white-level correction

The Acer XB253Q with EDID vendor `ACR` and serial `TH5EE0088525` loses the
distinction between very light SDR grays such as `#f4f4f8` and white on this
machine. A neutral gamma reset did not help. Scaling all three RGB channels
to a maximum of 90% restored the distinction in both the browser and a native
comparison window, at the original 1920×1080 / 239.760 Hz mode.

This is a visually verified white-level adjustment. It preserves the linear
shape of the existing neutral ramp; it is not a measured ICC calibration or
an identification of which internal monitor, dock, or driver component is
responsible for the lost highlight detail.

## Implementation

`astral-display-color.service` runs an unprivileged Wayland client. It matches
the vendor, model, and serial in the kernel's EDID against the profile in
`/usr/share/astral/display-color/acer-xb253q.env`. Connector names may change
after reconnecting. A second XB253Q with a different serial stays untouched,
as do the laptop panel and other monitors. Ambiguous connector names across
DRM devices are skipped.

Only the matched output receives a `wlr-gamma-control` object and a neutral
RGB ramp scaled by 0.90. The client does not change the monitor's mode, layout,
hardware picture settings, or browser preferences. Hotplug events reapply the
profile, a changed EDID releases it, and the user service restarts if the
compositor connection fails. Stopping the service releases gamma control and
restores the compositor's original gamma state.

Fresh installations use the user-unit preset. Existing accounts receive a
separate, one-time migration through the deferred `update-user-setup` helper;
their existing update-timer marker does not skip this migration. Subsequent
logins preserve later disable choices, and existing masks are respected.

The protocol permits one gamma-control owner per output. Other gamma clients,
including Noctalia Night Light, cannot independently adjust the Predator
while this profile owns it. Other outputs are not claimed. If another client
already owns the Predator, the service reports that and retries every ten
seconds. Disable this service before using a different gamma client for this
monitor.

## Inspect, override, or disable

Check the match without changing color:

```bash
/usr/libexec/astral/display-color --vendor ACR --model XB253Q \
    --serial TH5EE0088525 --gain 0.9
systemctl --user status astral-display-color.service
journalctl --user -u astral-display-color.service
```

To adjust the profile, put overrides in `/etc/astral/display-color.env` or
`~/.config/astral/display-color.env`, then restart the service:

```ini
ASTRAL_DISPLAY_GAIN=0.90
```

The accepted gain range is 0.5–1.0. A gain of 1.0 leaves the output unclaimed.
To disable correction and restore the original gamma state:

```bash
systemctl --user mask --now astral-display-color.service
```

Re-enable it with `systemctl --user unmask astral-display-color.service` and
`systemctl --user enable --now astral-display-color.service`.

The current machine uses the local `astral-display-color-preview.service`
until it boots an image containing `/usr/libexec/astral/display-color`.
That preview is enabled across logins and skips startup once the image
provides the client. Until then, use the preview unit name in the status,
restart, and disable commands above. It reads the same optional overrides.

## Validation

`tests/test_display_color.py` compiles the actual image client and runs it
against a private Wayland compositor with synthetic EDIDs. It verifies ramp
contents, exact-device isolation, reconnects under a new connector name,
release on exit or identity changes, competing-client rejection/retry, invalid
gains, and check-only behavior. It never connects to the real desktop. The client is
built by `mkosi.build.chroot` using build-only compiler/header packages and
verified for presence by the post-installation hook on both image architectures.
