# Pulse Voice Assistant

This document walks through the first end-to-end “Hey Pulse” loop that landed in `pulse-assistant`:

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
PULSE_ASSISTANT_WAKE_WORDS="okay_pulse"
```

You can swap in any Wyoming-compatible servers (vosk, porcupine, etc.) and adjust `PULSE_ASSISTANT_WAKE_WORDS` to match the model name you’ve installed.

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

---

## Display Overlay

`pulse-assistant-display.py` runs as a user-level service whenever `PULSE_VOICE_ASSISTANT="true"`. It subscribes to `pulse/<hostname>/assistant/response`, renders the text in a borderless Tk window, and auto-hides after `PULSE_ASSISTANT_DISPLAY_SECONDS` (default 8s). Tweak the font via `PULSE_ASSISTANT_FONT_SIZE`.

---

## Manual Test Checklist

1. **Wake word:** watch `journalctl -u pulse-assistant.service -f` and say “Okay Pulse”. You should see a detection log and an MQTT state message change to `listening`.
2. **STT sanity:** keep speaking after the chime; when you stop the transcript should be printed in the journal and published to `assistant/transcript`.
3. **LLM + speech:** set `OPENAI_API_KEY` and ask, “Hey Pulse, what’s the weather tomorrow?”. You should hear Piper speak and the overlay should show the text.
4. **Actions:** add the sample JSON above and say “Okay Pulse, turn on the desk lights.” Confirm the MQTT topic fired.

If something stalls, re-run `./setup.sh` (it restarts the services), then check:

* `journalctl -u pulse-assistant.service`
* `journalctl --user -u pulse-assistant-display.service`
* `aplay -l` / `arecord -l` for audio devices

Happy tinkering!

