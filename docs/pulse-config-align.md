# pulse-config-align

Compare and optionally align `pulse.conf` across multiple Pulse devices.

## Usage

```
bin/tools/pulse-config-align.py [options] [host1 ...]
```

Common flags:
- `--set-var VAR=VALUE` (repeatable): set a value on every host. If a host already has the value, it is skipped. Use `VAR=` (empty) to remove a setting and fall back to default. With only `--set-var`, changes are applied and `setup.sh` is run on all hosts in parallel and quiet (no diffs unless `--compare`).
- `--ignore-var NAME` (repeatable): hide noisy host-specific vars in diffs (default ignores `PULSE_BT_MAC`).
- `--compare`: force diff/summary output (helpful when running overrides-only).
- `--devices-file PATH`: file with hostnames (one per line) used when you omit hosts; defaults to `<repo>/pulse-devices.conf`. A sample is provided at `pulse-devices.conf.sample`.
- `--edit`: open each fetched config in `$EDITOR` after showing diffs.
- `--push`: scp the (edited/overridden) config back, then run `setup.sh`.
- `--confirm`: prompt between hosts in edit/push mode.
- `--auto-apply`: skip per-host prompts in edit/push mode.
- `--remote-path PATH`: override remote config path (default `/opt/pulse-os/pulse.conf`).
- `--local-config PATH`: use a specific local config for diffs; otherwise uses repo/pulse.conf or warns if missing.

## Behavior

1) Fetch all configs first; show diffs (local vs remote, pairwise across remotes) unless you’re running overrides-only without `--compare`.
2) Overrides:
   - If `--set-var` is used without `--edit/--push`, overrides apply/push/setup in parallel; hosts already matching are skipped, and diffs are hidden unless `--compare` is set.
   - In edit/push mode, overrides apply per-host only when values differ; if unchanged, they’re skipped but you can still edit/push.
3) `setup.sh` output is suppressed on success; errors still surface.

## Examples

Set a shared key everywhere (fast/parallel), skipping hosts already matching:
```
bin/tools/pulse-config-align.py --set-var GEMINI_API_KEY=abc pulse-office pulse-bedroom
```

Inspect diffs, then edit and push with overrides pre-applied:
```
bin/tools/pulse-config-align.py --set-var GEMINI_API_KEY=abc --edit --push pulse-office pulse-bedroom
```

Hide host-specific noise:
```
bin/tools/pulse-config-align.py --ignore-var PULSE_HOSTNAME --ignore-var PULSE_BT_MAC pulse-office pulse-bedroom
```

