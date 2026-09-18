# Speakers

PulseOS plays audio through whatever PipeWire reports as the **default sink**. That can be a
USB speaker or a Bluetooth one, but the two are not equally good ideas.

**Use a wired USB speaker.** Bluetooth is still supported and documented below, but it is no
longer the recommended path, and the fleet this project is developed against no longer uses it
anywhere.

---

## Why USB instead of Bluetooth

Every PulseOS display originally drove a Bluetooth speaker. In practice the Bluetooth path
generated more bugs, more support burden, and more dedicated code than any other part of the
audio stack. The specific problems, all of them things we actually hit:

| Problem | What it looked like |
|---|---|
| **Silent failure** | A speaker that is off or out of range does not error. Playback "succeeds", snapclient stays connected, and the room simply goes quiet. **A silent speaker also means a silent alarm** — which is the whole point of the device. |
| **Auto power-off** | Battery pods shut down after a few minutes idle. We had to invent a keepalive that plays an *inaudible 30 Hz tone* every 2 minutes purely to stop them sleeping — digital silence does not work, because the speaker's DSP does not count it as a signal. |
| **Slow, unreliable reconnect** | `bluetoothctl connect` blocks for ~60 seconds against a powered-off speaker, so every retry loop had to be written around that stall. |
| **Radio contention** | The Pi shares one 2.4 GHz radio between Wi-Fi and Bluetooth. BT paging against an absent speaker starved the Wi-Fi link badly enough to cause snapclient reconnect loops and clock-sync failures — a *Bluetooth* fault that presents as a *network* fault. |
| **Log spam** | A single offline speaker produced roughly 240 `avdtp_connect_cb() ... Host is down (112)` entries per day, drowning out real problems. |
| **Pairing is per-device state** | Trust data lives on the SD card. Reimage or swap the card and you re-pair every speaker by hand. |
| **Stale pairings mislead** | A display re-purposed between rooms keeps the old speaker's MAC, so "is it paired?" stops being a usable health check. Diagnosing which speaker a room *owns* needed its own documented rule. |
| **Audio quality** | Codec negotiation and A2DP renegotiation blips, with no way to pin a format. |

A USB speaker removes the entire category:

- **One cable carries power and audio.** No battery, no charging, no pairing, no radio.
- **It is a fixed sink.** WirePlumber remembers the default across reboots.
- **Failure is detectable.** The sink disappears from `pactl list sinks short`, so the
  speaker-offline badge can actually tell "unplugged" from "fine".
- **No keepalive needed.** It cannot go to sleep.
- **It frees the 2.4 GHz radio** — you can disable the Bluetooth core outright (see below).
- **Better audio.** A fixed 48 kHz stereo sink, rather than whatever the link negotiated.

The trade-off is real but small: the speaker has to be within cable reach of the Pi, and you
give up placing it across the room.

### Recommended speaker

[**Adafruit 3369 — Mini External USB Stereo Speaker**](https://www.adafruit.com/product/3369),
$12.50. 84 × 43 × 32 mm, 74 g, 2 × 2 W, 4 Ω, 60 dB SNR, 46" captive cable.

It is **USB-only** — a single USB 2.0 connection carries both 5 V power and the audio, and there
is no 3.5 mm jack to deal with. It enumerates as a standard USB audio class device (`4c4a:4155`,
a Jieli chipset) and needs no drivers.

Power is not a concern: a Pi 5 on the official 27 W supply reports `psu_max=5000mA`, and a
2 × 2 W speaker draws a few hundred mA.

Any USB audio class speaker or DAC works. The office unit in this fleet is a C-Media-based
Unitek Y-247A adapter feeding powered desktop speakers, configured exactly the same way.

---

## Setting up a USB speaker

1. **Plug it in**, then find the sink name:
   ```bash
   pactl list short sinks
   ```
   Look for the new entry. On a display with a ReSpeaker mic array you will see that too — it
   is a *microphone* and will also appear as a sink. Do not pick it.

2. **Make it the default.** WirePlumber persists this across reboots:
   ```bash
   pactl set-default-sink <full-sink-name>
   ```
   This step is required. Nothing in PulseOS selects a wired sink for you.

3. **Tell the watcher what to expect** by putting a substring of the sink name in
   `/opt/pulse-os/pulse.conf`:
   ```bash
   PULSE_BLUETOOTH_AUTOCONNECT="false"
   PULSE_SPEAKER_SINK="Jieli"
   ```
   Match on the chipset name (`Jieli`, `C-Media`), not the whole string — the serial embedded
   in the sink name is not a reliable identifier. Leaving `PULSE_SPEAKER_SINK` empty disables
   the check: without a name to expect, there is no way to distinguish "unplugged" from "never
   had one".

4. **If this display previously used Bluetooth, stop the autoconnect timer.**
   ```bash
   systemctl --user disable --now bt-autoconnect.timer bt-autoconnect.service
   ```
   Setting `PULSE_BLUETOOTH_AUTOCONNECT="false"` and re-running `setup.sh` prevents the units
   activating in future, but older releases did not stop a timer that was *already running* —
   it keeps paging a speaker nobody is listening to until the next reboot. That is worth doing
   properly rather than ignoring: BT paging competes with wifi for the Pi's single 2.4 GHz
   radio, so a stray timer degrades the network link. Confirm with
   `systemctl --user is-active bt-autoconnect.timer` (expect `inactive`) — note these are
   **user** units, so checking them in system scope always reports `inactive` even while they
   are actively running.

5. **Apply and verify:**
   ```bash
   sudo systemctl restart pulse-kiosk-mqtt
   # only if you run Snapcast (PULSE_SNAPCLIENT="true"; it defaults to false)
   systemctl is-enabled pulse-snapclient >/dev/null 2>&1 && sudo systemctl restart pulse-snapclient
   pactl get-default-sink
   paplay /usr/share/sounds/alsa/Front_Left.wav
   ```
   If you run Snapcast, restart `pulse-snapclient` **as well as** `pulse-kiosk-mqtt`. It runs
   with `--soundcard default`, and an already-running client keeps its stream on the old sink
   rather than migrating to the new default.

### Optional: turn the Bluetooth radio off

Once nothing needs Bluetooth, disabling the core frees the shared radio. On a Pi 5, append to
`/boot/firmware/config.txt`:

```
# BT radio off - audio is wired USB
dtoverlay=disable-bt-pi5
```

then `sudo systemctl disable --now bluetooth` and reboot.

Use `disable-bt-pi5`, not `disable-bt` — the latter targets `brcm,bcm2835` (pre-Pi 5) and also
restores UART0 on GPIO 14/15, which is not what you want here.

> **`config.txt` does not support inline comments.** Writing
> `dtoverlay=disable-bt-pi5   # some note` silently does nothing: the firmware treats the rest
> of the line as overlay parameters, the overlay never loads, and nothing logs an error. Put
> the comment on its own line, and verify after rebooting with `hciconfig | grep -c ^hci`
> (expect `0`) rather than trusting the edit.

---

## Legacy: pairing a Bluetooth speaker

Still supported. Prefer USB if you have the choice.

> **If you followed the radio-off step above, undo it first** — otherwise `bluetoothctl` has
> no adapter to work with and `scan on` finds nothing, with no obvious error:
> ```bash
> sudo sed -i '/^dtoverlay=disable-bt-pi5$/d' /boot/firmware/config.txt
> sudo systemctl enable --now bluetooth
> sudo reboot
> ```
> Then set `PULSE_BLUETOOTH_AUTOCONNECT="true"` in `pulse.conf` and re-run `setup.sh` before
> pairing. Confirm the adapter is back with `hciconfig | grep -c ^hci` (expect `1`).

Pairing is a one-time step per device — redo it if you reimage or replace the microSD card.
Commands assume you are the `pulse` user on the device.

1. **Put the speaker in pairing mode** so it is discoverable (check its manual for the button
   combo).
2. **Start the Bluetooth CLI:**
   ```bash
   sudo -u pulse bluetoothctl
   ```
3. **Enable power and scanning** at the prompt:
   ```
   power on
   agent on
   default-agent
   scan on
   ```
   Watch for a line such as `Device XX:XX:XX:XX:XX:XX My Speaker`.
4. **Pair, trust, and connect** using the MAC from the scan:
   ```
   pair XX:XX:XX:XX:XX:XX
   trust XX:XX:XX:XX:XX:XX
   connect XX:XX:XX:XX:XX:XX
   ```
   Then `quit`.
5. **Optional: pin the speaker** so autoconnect always targets one device rather than
   falling back to "first paired":
   ```bash
   PULSE_BT_MAC="XX:XX:XX:XX:XX:XX"
   ```
   > An exported `PULSE_BT_MAC` overrides the `pulse.conf` value, which is the quickest way
   > to try a different speaker without editing anything:
   > `PULSE_BT_MAC="XX:XX:XX:XX:XX:XX" /home/pulse/bin/bt-autoconnect.sh`

   > **Mind the quoting.** A normal trailing `# comment` after a value is fine, but an extra
   > quote silently corrupts it: `PULSE_BT_MAC="""   # note"` parses as `""` concatenated with
   > `"   # note"`, assigning the *comment text* as the MAC. Nothing errors. Verify an edit by
   > sourcing the file rather than eyeballing it:
   > `( set -a; . /opt/pulse-os/pulse.conf; set +a; echo "[$PULSE_BT_MAC]" )`
6. **Apply the changes:**
   ```bash
   cd /opt/pulse-os
   ./setup.sh <location-name>
   ```
   (Or tap the MQTT "Update" button if you use Home Assistant.)
7. **Test manually** if desired:
   ```bash
   /home/pulse/bin/bt-autoconnect.sh
   ```

### Tips

- If the speaker falls back to a generic name like "Zero", look for that label when scanning.
- Repeat the pairing flow whenever you reimage or swap SD cards — trust data lives on the device.
- Do not infer which speaker a room owns from `bluetoothctl devices Paired`; a re-purposed
  display keeps stale pairings. `PULSE_BT_MAC` and `PULSE_BLUETOOTH_AUTOCONNECT` are the source
  of truth, and they are what `bin/bt-autoconnect.sh` and the speaker-offline badge both follow.
