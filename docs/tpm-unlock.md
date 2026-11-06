# TPM disk auto-unlock

Astral's setup helper enrolls LUKS2 keys against PCRs **7+14**: Secure Boot
policy and shim/MOK policy. Existing enrollments need deliberate re-enrollment
through the helper after recovery verification. An image update does not run
the helper or replace existing keys.

The helper requires enabled Secure Boot, enabled shim validation, and initialized
SHA-256 measurements for both PCRs. It checks a separately entered LUKS
passphrase or recovery key with token auto-unlocking bypassed. It prepares the
replacement on a root-only temporary LUKS header in `/run`, wiping the copied
TPM slots before enrolling a fresh binding. This also repairs stale bindings
after a firmware TPM reset with unchanged PCR measurements. Failed enrollment
leaves the disk header unchanged. After enrollment succeeds, the helper checks
that the original header has not changed during setup and applies the prepared
header. Password and recovery slots are retained; temporary headers are removed
on exit.

PCRs 7+14 bind the trust policy, rather than the exact kernel or initramfs
contents. They do not provide the same assurance as a measured UKI with a
signed PCR policy. Changes to Secure Boot keys, dbx, or MOK policy can prevent
automatic unlocking and require the recovery credential.

References: [systemd 259 enrollment and PCR documentation](https://www.freedesktop.org/software/systemd/man/259/systemd-cryptenroll.html)
and [shim TPM measurements](https://github.com/rhboot/shim/blob/main/README.tpm).

## Prepare recovery

For guided setup, run:

```sh
ajust secure-tpm2
```

The wizard checks Secure Boot/shim measurements, lets you use an existing
recovery credential or create one through the current TPM enrollment, and asks
for an existing folder on protected external recovery storage. It refuses
temporary filesystems and the encrypted system filesystem. Confirm that the
storage is separate and protected; a different filesystem alone does not prove
that it is on a separate physical device.

It saves protected LUKS header backups before changes, after adding a recovery
key, and after verified enrollment. A newly generated recovery key is displayed
directly in the terminal and must be saved offline. It is never written to a
log or passed as a command argument. Cancellation after creating a recovery key
retains that new credential and the original TPM enrollment.

The existing enrollment helper then checks the recovery credential independently
and offers PIN selection. Choose **Enable or replace TPM2 enrollment** when
prompted. The wizard checks PCRs 7+14 and retention of all independent key slots
afterward. It leaves rebooting and boot-time recovery tests to you.
Canceling the enrollment helper stops the wizard without reporting successful
setup; any recovery credential already created and header backups are retained.

The standalone `/usr/libexec/astral/secure-tpm2` checks the current policy without
changing enrollment by default. Add `--apply` for guided setup and `--device`
to select a particular encrypted device. The manual commands below remain
available.

Use the LUKS device UUID, rather than the UUID of the filesystem inside it.
For multiple encrypted devices, choose the intended device explicitly.

```sh
device=/dev/disk/by-uuid/YOUR-LUKS-UUID
sudo cryptsetup isLuks --type luks2 "$device"
```

Keep a known passphrase or create an independent recovery key:

```sh
sudo systemd-cryptenroll --recovery-key "$device"
```

Store the displayed recovery key offline. Keep an encrypted backup of important
data and a LUKS header backup on separate recovery media:

```sh
sudo cryptsetup luksHeaderBackup "$device" \
  --header-backup-file /media/RECOVERY/astral-luks-header.img
sudo chmod 0600 /media/RECOVERY/astral-luks-header.img
```

Protect the header backup: it preserves the old key slots and their old TPM
policy. Recovery media should also provide a bootable rescue environment.

## Enroll or replace a legacy enrollment

```sh
ajust toggle-tpm2
# Or choose the exact device:
luks-tpm2-autounlock --device "$device"
```

Select **Enable or replace TPM2 enrollment**, confirm recovery readiness, and
enter the recovery credential for the independent unlock check. Choose whether
to require a TPM PIN. Escape and the default **Cancel** choice leave enrollment
unchanged, including at the PIN menu.
`systemd-cryptenroll` then asks for the credential again to authorize enrollment.
The disk receives the replacement only after successful enrollment on the
temporary header, while preserving the independent password/recovery slots.
Do not run other disk-enrollment tools concurrently with this operation.

The helper refuses ambiguous kernel command lines containing multiple LUKS
UUIDs; use `--device` in that case. It never reboots the machine automatically.

## Reboot and recovery validation

Complete these checks on the target boot stack before relying on auto-unlock.
Use a disposable VM or a backed-up test machine for deliberate TPM or trust
policy changes.

1. Verify that a previously unbound enrollment has been replaced by a TPM
   token whose `tpm2-pcrs` contains 7 and 14, with independent recovery still
   enrolled. `sudo systemd-cryptenroll "$device"` lists the remaining slot types.
   To inspect only the public TPM policy fields:

   ```sh
   sudo cryptsetup luksDump --dump-json-metadata "$device" |
     python3 -c 'import json,sys; d=json.load(sys.stdin); print([t.get("tpm2-pcrs") for t in d["tokens"].values() if t["type"]=="systemd-tpm2"])'
   ```
2. Reboot normally. Verify TPM auto-unlock, and the PIN prompt if enabled.
3. Test boot-time passphrase/recovery fallback with the TPM unavailable or a
   changed trust policy. Confirm that the recovery credential opens the same
   volume and the system reaches a working session.
4. Restore the intended Secure Boot/shim configuration, re-enroll if its
   measurements changed, and reboot normally again.
5. Install a normal Astral image update and verify another reboot. Keep the
   recovery credential ready for future firmware, dbx, and MOK updates.

For rollback to passphrase unlocking, choose **Disable TPM2 auto-unlock** in the
helper. This also requires successful independent recovery verification before wiping TPM
slots. It works when Secure Boot or TPM auto-unlock is unavailable.

## Automated validation

The Python tests exercise cancellation, PIN selection, wrong recovery
credentials, disabled Secure Boot/shim validation, missing measurements,
ambiguous devices, and enrollment failures without touching real devices.

`tests/check-tpm-policy` uses a fresh 64 MiB LUKS2 file and a private `swtpm`
socket. It verifies migration from an unbound enrollment, independent PCR 7
and PCR 14 rejection, recovery after each failure, restoration after vTPM
restarts, PIN-protected unlocking, replacement after a TPM reset with unchanged
PCRs, failed replacement without disk-header changes, and preservation of the
recovery slot when TPM unlocking is disabled.
CI runs this test as part of the required `Image checks` result.

On Fedora, the test prerequisites are `swtpm`, `tpm2-tools`, `cryptsetup`, and
`systemd-cryptenroll`. A non-root invocation uses an unprivileged user namespace.
The emulator test does not replace the target-machine reboot checklist above.
