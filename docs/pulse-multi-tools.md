# Multi-device tools

These tools operate on multiple Pulse hosts. Hosts can be provided on the command line or read from `pulse-devices.conf` (one hostname per line, `#` for comments). A sample list is provided at `pulse-devices.conf.sample`.

## pulse-config-align

```
bin/tools/pulse-config-align [options] [host1 ...]
```

- `--set-var VAR=VALUE` (repeatable): set a value on every host; skips hosts already matching. With only `--set-var`, applies/pushes/runs setup in parallel and hides diffs unless `--compare`.
- `--ignore-var NAME` (repeatable): hide noisy host-specific vars (default ignores `PULSE_BT_MAC`).
- `--compare`: force diff/summary output (useful with overrides-only).
- `--devices-file PATH`: override devices list path (default `<repo>/pulse-devices.conf`).
- `--edit`, `--push`, `--confirm`, `--auto-apply`, `--remote-path`, `--local-config` as documented in `docs/pulse-config-align.md`.

See `docs/pulse-config-align.md` for full details.

## pulse-update

```
bin/tools/pulse-update [hosts...]
```

- Runs `setup.sh` on each host (SSH) to update/apply config.
- Hosts: CLI list or `pulse-devices.conf` if omitted.
- Env: `REPO_DIR` (default `/opt/pulse-os`), `DEVICES_FILE` to override list path.

## pulse-reboot

```
bin/tools/pulse-reboot [hosts...]
```

- Reboots each host via SSH.
- Hosts: CLI list or `pulse-devices.conf` if omitted.
- Env: `REPO_DIR` (default `/opt/pulse-os`), `DEVICES_FILE` to override list path.

