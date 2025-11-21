# Pulse Voice Assistant

This document walks through the first end-to-end “Hey Jarvis” loop that landed in `pulse-assistant`:

```
ReSpeaker mic → wyoming-openwakeword → wyoming-whisper → LLM → wyoming-piper → speakers + overlay
```

The heavy models (wake/STT/TTS) are expected to run on another box (NAS, HA server, etc.). The Pi only needs to stream PCM audio over the Wyoming protocol and call your LLM provider.

---

## Requirements

* A USB/ALSAmicrophone (we’ve validated with the ReSpeaker Mic Array v3). The default command is:
  ```
  PULSE_ASSISTANT_MIC_CMD="arecord -q -t raw -f S16_LE -c 1 -r 16000 -"
  ```
* Remote Wyoming services (Docker examples below)
* An LLM provider – OpenAI works out of the box via `OPENAI_API_KEY`, but the provider layer is pluggable.
* Optional: MQTT broker if you want automations or the on-screen overlay.

After updating `pulse.conf`, rerun `./setup.sh <location>` so the new systemd units are linked and enabled.

---

## Wyoming Services

Spin up the reference containers on your server/NAS and point the host/port vars at them:

```yaml
# docker-compose.yml (example)
services:
  openwakeword:
    image: rhasspy/wyoming-openwakeword:latest
    command: --uri tcp://0.0.0.0:10400 --trigger-level 2
    ports: ["10400:10400"]

  whisper:
    image: rhasspy/wyoming-faster-whisper:latest
    command: --uri tcp://0.0.0.0:10300 --model medium-int8
    ports: ["10300:10300"]
    volumes:
      - ./models:/data

  piper:
    image: rhasspy/wyoming-piper:latest
    command: --uri tcp://0.0.0.0:10200 --voice en-us-amy-medium
    ports: ["10200:10200"]
    volumes:
      - ./voices:/data
```

Then in `pulse.conf`:

```
PULSE_VOICE_ASSISTANT="true"
WYOMING_OPENWAKEWORD_HOST="your-nas"
WYOMING_WHISPER_HOST="your-nas"
WYOMING_PIPER_HOST="your-nas"
PULSE_ASSISTANT_WAKE_WORDS_PULSE="hey_jarvis"
```

You can swap in any Wyoming-compatible servers (vosk, porcupine, etc.) and adjust `PULSE_ASSISTANT_WAKE_WORDS_PULSE` to match the model name you’ve installed.

---

## Dual Wake Words & Home Assistant Pipelines

Pulse now supports two wake-word profiles:

| Variable | Purpose |
| --- | --- |
| `PULSE_ASSISTANT_WAKE_WORDS_PULSE` | “Pulse” pipeline (local LLM + direct Wyoming endpoints) |
| `PULSE_ASSISTANT_WAKE_WORDS_HA` | “Home Assistant” pipeline (routes audio through HA’s Assist stack) |
| `PULSE_ASSISTANT_WAKE_ROUTES` | Optional explicit map (`model=pipeline`) if you want per-model overrides |

Example:

```
PULSE_ASSISTANT_WAKE_WORDS_PULSE="hey_jarvis"
PULSE_ASSISTANT_WAKE_WORDS_HA="hey_house"
```

When `HOME_ASSISTANT_BASE_URL` + `HOME_ASSISTANT_TOKEN` are set, “Hey House …” streams through HA while “Hey Jarvis …” keeps using your configured LLM provider.

Optional helpers:

```
HOME_ASSISTANT_ASSIST_PIPELINE="Pulse Desk"
HOME_ASSISTANT_TIMER_ENTITY="timer.kitchen"
HOME_ASSISTANT_REMINDER_SERVICE="notify.mobile_app_pixel"
PULSE_ASSISTANT_WAKE_SOUND="true"
PULSE_ASSISTANT_SPEAKING_STYLE="normal"   # relaxed/normal/aggressive
PULSE_ASSISTANT_WAKE_SENSITIVITY="normal" # low/normal/high
```

If you’re letting HA proxy the Wyoming services you can also point the assistant at HA’s ports via `HOME_ASSISTANT_OPENWAKEWORD_HOST`, `HOME_ASSISTANT_WHISPER_HOST`, `HOME_ASSISTANT_PIPER_HOST`, etc. If the HA Whisper endpoint exposes multiple models, set `HOME_ASSISTANT_STT_MODEL` so we request the correct one. Leave these blank to keep using your original servers.

---

## LLM and Automations

`pulse-assistant` injects your options into the LLM system prompt. At minimum set:

```
PULSE_ASSISTANT_PROVIDER="openai"
OPENAI_API_KEY="sk-..."
OPENAI_MODEL="gpt-4o-mini"
```

You can override the persona via `PULSE_ASSISTANT_SYSTEM_PROMPT` or supply a path in `PULSE_ASSISTANT_SYSTEM_PROMPT_FILE`.

### MQTT Actions

Actions are described once (file or inline JSON) and referenced by slug in conversations. Example:

```json
[
  {
    "slug": "desk_lights_on",
    "description": "Turn on the desk lights",
    "topic": "home/desk/lights/set",
    "payload": {"state": "ON"}
  },
  {
    "slug": "office_lights_off",
    "description": "Turn everything off in the office",
    "topic": "home/office/all/set",
    "payload": "{\"state\":\"OFF\"}",
    "retain": true
  }
]
```

Point `PULSE_ASSISTANT_ACTIONS_FILE` at the JSON above (or set `PULSE_ASSISTANT_ACTIONS='[...]'`). During a reply the LLM may return:

```json
{"response": "Turning the desk lights on.", "actions": ["desk_lights_on"]}
```

The daemon publishes executed actions to `pulse/<hostname>/assistant/actions`.

### Home Assistant actions & timers

With HA credentials configured you get two built-in slugs:

```
ha.turn_on:light.kitchen
ha.turn_off:switch.projector
```

Timers and reminders are exposed in the same lightweight format:

```
timer.start:duration=10m,label=Tea
reminder.create:when=2025-01-01T09:00,message=Turn off humidifier
```

If you provide `HOME_ASSISTANT_TIMER_ENTITY` / `HOME_ASSISTANT_REMINDER_SERVICE` the assistant calls those services; otherwise it falls back to a local scheduler that publishes a response MQTT payload and speaks the reminder aloud.

---

## Using HA Assist audio end-to-end

When a wake word mapped to the HA pipeline fires:

1. The assistant records PCM audio using the same mic settings as the Pulse pipeline.
2. The raw bytes are base64-encoded and POSTed to `/api/assist_pipeline/run` with `start_stage=stt` / `end_stage=tts`.
3. Home Assistant runs its configured Assist pipeline (STT, intent, TTS) and responds with:
   - `stt_output.text` (the transcript),
   - `response.speech.plain.speech` (friendly text),
   - `tts_output` (optional base64 audio plus sample rate/width/channels).
4. If `tts_output` is present we play it directly via ALSA; otherwise we fall back to your configured Wyoming TTS endpoint.

### Assist checklist

| Problem | Fix |
| --- | --- |
| 401/403 errors | Run `bin/tools/verify-conf.py` to confirm the token, or reissue a HA long-lived token. |
| Silence after Assist | Check if `tts_output` is included; if not, ensure your HA pipeline ends with a TTS stage or provide `HOME_ASSISTANT_PIPER_HOST` so the fallback path works. |
| SSL errors | Set `HOME_ASSISTANT_VERIFY_SSL="false"` for self-signed certs or install your CA bundle and point `REQUESTS_CA_BUNDLE` to it. For system-wide trust, copy the CA into `/usr/local/share/ca-certificates/homeassistant-ca.crt` and run `sudo update-ca-certificates`. For the Chromium kiosk, install `libnss3-tools`, ensure the profile dir exists (`sudo -u pulse mkdir -p /home/pulse/.config/kiosk-profile/Default`), and import the CA with `sudo -u pulse certutil -d sql:/home/pulse/.config/kiosk-profile/Default -A -t "C,," -n homeassistant -i /path/to/ha-root-ca.pem`; confirm via `certutil -L` before restarting `pulse-kiosk`. Keep `--user-data-dir` away from `/tmp` or the cert will be lost on every reboot. Don’t stash `REQUESTS_CA_BUNDLE` inside `pulse.conf`—`bin/tools/sync-pulse-conf.py` drops unknown keys the next time it runs. Use a systemd drop-in to add `Environment=REQUESTS_CA_BUNDLE=…` for `pulse-assistant.service` (and any other unit that needs it). |
| 404 on `/api/assist_pipeline/run` | Home Assistant only mounts the Assist Pipeline REST routes when the request reaches the actual HA instance and the integration is loaded. Make sure `HOME_ASSISTANT_BASE_URL` resolves to the real HA IP (no stale DNS/hosts entries) and that the `assist_pipeline` integration is enabled with at least one pipeline. A mismatched hostname or proxy that points elsewhere causes 404s even though Assist is configured. |
| Legacy/Unknown block in `pulse.conf` | `bin/tools/sync-pulse-conf.py` copies every variable from `pulse.conf.sample`. If you still see a “Legacy/Unknown Variables” section after syncing, those keys aren’t recognized anymore. Either delete them or rename them to the current names (check `pulse.conf.sample` for the latest). Only leave values there temporarily; nothing in that block is consumed by Pulse services. |
| Wrong pipeline triggered | Confirm the wake-word list contains the exact model name exposed by `wyoming-openwakeword`. |

---

## MQTT telemetry & controls

Pulse publishes assistant-specific topics alongside the existing kiosk telemetry:

| Topic | Direction | Notes |
| --- | --- | --- |
| `pulse/<host>/assistant/state` | out | JSON payload containing `state`, `pipeline`, `stage`, `wake_word`. |
| `pulse/<host>/assistant/in_progress` | out | Binary sensor (`ON` during an interaction). |
| `pulse/<host>/assistant/metrics` | out | Timing info (total + per-stage milliseconds, wake word, pipeline, status). |
| `pulse/<host>/preferences/wake_sound/set` | in | `on`/`off`; state mirrored at `/state`. |
| `pulse/<host>/preferences/speaking_style/set` | in | `relaxed`, `normal`, `aggressive`. |
| `pulse/<host>/preferences/wake_sensitivity/set` | in | `low`, `normal`, `high` (mapped to openWakeWord trigger levels 5/3/2). |
| `pulse/<host>/preferences/ha_pipeline/set` | in | Free-form pipeline ID (leave blank to use HA’s default). |

All preference states are retained so dashboards instantly reflect the last-known values after reboots. Async listeners can treat the `/set` topics as switches/selects in Home Assistant, while the `assistant/in_progress` topic mirrors the stock HA Voice binary sensor.

---

## Display Overlay

`pulse-assistant-display.py` runs as a user-level service whenever `PULSE_VOICE_ASSISTANT="true"`. It subscribes to `pulse/<hostname>/assistant/response`, renders the text in a borderless Tk window, and auto-hides after `PULSE_ASSISTANT_DISPLAY_SECONDS` (default 8s). Tweak the font via `PULSE_ASSISTANT_FONT_SIZE`.

Now-playing metadata from Home Assistant can be pinned to the lower-right corner of the overlay. Point it at any `media_player` entity (Snapcast clients, Sonos, etc.) by setting:

```
PULSE_DISPLAY_SHOW_NOW_PLAYING="true"
PULSE_MEDIA_PLAYER_ENTITY="media_player.pulse_<location>_2"
PULSE_DISPLAY_NOW_PLAYING_INTERVAL_SECONDS=5
```

The display daemon reuses `HOME_ASSISTANT_BASE_URL` / `HOME_ASSISTANT_TOKEN` and polls the entity for `media_title` / `media_artist`. When the player’s state is `playing`, you’ll see “Artist — Title” rendered on-screen; it hides automatically when playback stops.

---

## Snapcast Multiroom Output

Pulse can now appear as a Snapcast player so Music Assistant (or anything that can send audio to Snapserver) can target each kiosk directly.

1. **Run Snapserver** somewhere on your network. The `ivdata/snapserver` image exposes the latest upstream bits and works well in Docker/Compose environments: [ivdata/snapserver](https://hub.docker.com/r/ivdata/snapserver/). A minimal compose file looks like:

   ```yaml
   version: "3.9"
   services:
     snapserver:
       image: ivdata/snapserver:latest
       container_name: snapserver
       network_mode: host            # simplest way to expose ports 1704/1705
       restart: unless-stopped
       environment:
         - SNAPSERVER_STREAMS=pipe:///tmp/snapfifo?name=MusicAssistant
         - SNAPSERVER_LOGLEVEL=info
       volumes:
         - /opt/snapserver/config/snapserver.conf:/etc/snapserver.conf:ro
         - /opt/snapserver/state:/var/lib/snapserver
         - /opt/snapserver/tmp:/tmp
   ```

2. **Point Music Assistant at the new server.** In `Settings → Player Providers → Snapcast`, toggle “Use existing Snapserver” and drop in the IP/port you used above (1705 is the default control port).

3. **Enable the client on each Pulse.** Set the following in `pulse.conf`:

   ```
   PULSE_SNAPCLIENT="true"
   PULSE_SNAPCAST_HOST="192.168.1.100"   # Snapserver host
   PULSE_SNAPCAST_PORT="1704"            # optional, defaults shown
   PULSE_SNAPCAST_CONTROL_PORT="1705"
   PULSE_SNAPCLIENT_SOUNDCARD="default"  # 'default' routes through PipeWire/Pulse; override for ALSA hw
   ```

   Optional helpers:

   - `PULSE_SNAPCLIENT_LATENCY_MS` — hand-tune the local buffer size.
   - `PULSE_SNAPCLIENT_EXTRA_ARGS` — add extra `snapclient` flags (defaults to `--player pulse` so audio goes through PipeWire/Pulse).
   - `PULSE_SNAPCLIENT_HOST_ID` — override the friendly name (defaults to the hostname).

4. **Run `./setup.sh <location>`** so the new `pulse-snapclient.service` picks up your config. When `PULSE_SNAPCLIENT="true"` the setup script installs `snapclient`, writes `/etc/default/pulse-snapclient`, and enables the service.

Once the service reports in, the Snapcast provider surfaces the Pulse device as a MA player, and Home Assistant creates a `media_player.snapcast_client_*` entity for automations or voice actions.

---

## Manual Test Checklist

1. **Wake word:** watch `journalctl -u pulse-assistant.service -f` and say “Okay Pulse”. You should see a detection log and an MQTT state message change to `listening`.
2. **STT sanity:** keep speaking after the chime; when you stop the transcript should be printed in the journal and published to `assistant/transcript`.
3. **LLM + speech:** set `OPENAI_API_KEY` and ask, “Hey Jarvis, what’s the weather tomorrow?”. You should hear Piper speak and the overlay should show the text.
4. **Actions:** add the sample JSON above and say “Okay Pulse, turn on the desk lights.” Confirm the MQTT topic fired.

If something stalls, re-run `./setup.sh` (it restarts the services), then check:

* `journalctl -u pulse-assistant.service`
* `journalctl --user -u pulse-assistant-display.service`
* `aplay -l` / `arecord -l` for audio devices

Happy tinkering!

