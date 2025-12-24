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
* An LLM provider – OpenAI (`OPENAI_*`) or Google Gemini (`GEMINI_*`) work out of the box, but the provider layer is pluggable.
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

Pulse supports two wake-word profiles:

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
PULSE_ASSISTANT_SELF_AUDIO_TRIGGER_LEVEL="7"
PULSE_ASSISTANT_LOG_LLM="true"            # publish transcripts/responses to MQTT
PULSE_ASSISTANT_LOG_TRANSCRIPTS="false"   # log transcripts/responses locally at INFO
```

If you’re letting HA proxy the Wyoming services you can also point the assistant at HA’s ports via `HOME_ASSISTANT_OPENWAKEWORD_HOST`, `HOME_ASSISTANT_WHISPER_HOST`, `HOME_ASSISTANT_PIPER_HOST`, etc. If the HA Whisper endpoint exposes multiple models, set `HOME_ASSISTANT_STT_MODEL` so we request the correct one. Leave these blank to keep using your original servers.

### Home Assistant wake-word models

Wake words mapped to the `home_assistant` pipeline are now sent to the HA-managed `wyoming-openwakeword` endpoint while the local/Jarvis models keep using the primary server. To keep “Hey House” (or any other HA wake word) reliable:

1. Set `HOME_ASSISTANT_OPENWAKEWORD_HOST` / `HOME_ASSISTANT_OPENWAKEWORD_PORT` to the host/port exposed by the HA add-on or container that runs `wyoming-openwakeword`. The official HA add-on listens on port `10400` and stores custom models under `/share/openwakeword`.
2. Drop improved models (for example, a stronger `hey_house` model) into that directory and preload them. The add-on UI exposes a “Preload models” list; a vanilla container can use `--preload-model hey_house --custom-model-dir /share/openwakeword`. Copy both the `.tflite` and `.json` files from the OpenWakeWord project (or any custom training run) so the service advertises the exact model name you reference in `PULSE_ASSISTANT_WAKE_WORDS_HA`.
3. Run `bin/tools/list-wake-models.py --config /path/to/pulse.conf` to see which models each endpoint reports. `bin/tools/verify-conf.py` now fails fast if a configured wake word is missing so you know to preload the model before testing voice control.
4. (Optional) Record a short WAV clip of you saying the wake phrase (16-bit mono at 16 kHz) and run `bin/tools/list-wake-models.py --probe hey_house=/tmp/hey_house.wav`. The helper will stream that audio to the correct OpenWakeWord endpoint and confirm whether the detection fires, which is handy when debugging custom models.

If you want to train your own “Hey House” model, follow the [openWakeWord training guide](https://github.com/dscripka/openWakeWord) to generate a dataset (spoken samples + background noise), run the training pipeline, then copy the resulting artifacts into the HA directory mentioned above. Restart the HA add-on (or container) so it loads the new model, rerun `list-wake-models.py`, and the assistant will automatically start sending HA wake words to that endpoint.

### Music Assistant control

Set `PULSE_MEDIA_PLAYER_ENTITY="media_player.<your_player>"` (and the required `HOME_ASSISTANT_*` credentials) to let the Pulse pipeline pause/stop/skip music or describe what’s playing without extra automations. Example prompts: “Pause the music”, “Next song”, “What song is this?”, or “Who is this?”. Pulse calls the standard Home Assistant `media_player` services and responds verbally with the result.

### Transcript logging toggle

If you want local transcript/response logs at INFO, set `PULSE_ASSISTANT_LOG_TRANSCRIPTS="true"`. Publishing transcripts/responses to MQTT is controlled separately by `PULSE_ASSISTANT_LOG_LLM` (and the MQTT switch `pulse/<hostname>/assistant/preferences/log_llm`). Leave `LOG_TRANSCRIPTS` false if you don’t want local log lines; leave `LOG_LLM` true if you still want MQTT payloads for dashboards/automation.

### Ignoring Pulse’s own audio

When the kiosk is playing music (or speaking a TTS reply) the microphones used by `pulse-assistant` can hear that playback and occasionally fire the wake word, especially if the lyrics contain “Jarvis”. The assistant always watches the existing `pulse/<hostname>/telemetry/now_playing` feed published by `pulse-kiosk-mqtt.service` and tracks its own playback sessions. While self audio is active it temporarily bumps the openWakeWord trigger level (default 7) so ambient music is ignored but spoken wake words are still accepted. Tune the behavior with `PULSE_ASSISTANT_SELF_AUDIO_TRIGGER_LEVEL` if you need the assistant to be more/less strict while Pulse is playing audio. When Home Assistant access + `PULSE_MEDIA_PLAYER_ENTITY` are configured, the assistant will also pause that media player as soon as a wake word fires and resume playback roughly two seconds after the spoken response completes.

---

## LLM and Automations

`pulse-assistant` supports **6 LLM providers**: OpenAI, Google Gemini, Anthropic Claude, Groq, Mistral AI, and OpenRouter. Pick a provider and set the matching variables:

### OpenAI

```bash
PULSE_ASSISTANT_PROVIDER="openai"
OPENAI_API_KEY="sk-..."
OPENAI_MODEL="gpt-4o-mini"  # or gpt-4o, gpt-4-turbo, etc.
```

### Google Gemini

```bash
PULSE_ASSISTANT_PROVIDER="gemini"
GEMINI_API_KEY="AIza..."
GEMINI_MODEL="gemini-1.5-flash-latest"
```

### Anthropic Claude

Claude offers premium quality with industry-leading context windows. Best for complex queries and detailed responses.

```bash
PULSE_ASSISTANT_PROVIDER="anthropic"
ANTHROPIC_API_KEY="sk-ant-..."
ANTHROPIC_MODEL="claude-3-5-haiku-20241022"  # Fast and cost-effective
# Or use: claude-3-5-sonnet-20241022 for higher quality
```

**Models:**
- `claude-3-5-haiku-20241022` - Fastest, most cost-effective
- `claude-3-5-sonnet-20241022` - Balanced performance and quality
- `claude-opus-4-5-20251101` - Highest quality (Claude Opus 4.5)

### Groq (Ultra-Fast Inference)

Groq provides sub-second inference speeds with open-source models. Ideal for real-time voice interactions.

```bash
PULSE_ASSISTANT_PROVIDER="groq"
GROQ_API_KEY="gsk_..."
GROQ_MODEL="llama-3.3-70b-versatile"
```

**Popular models:**
- `llama-3.3-70b-versatile` - Best quality, fast
- `llama-3.1-8b-instant` - Fastest, good for simple queries
- `mixtral-8x7b-32768` - Large context window

Groq typically responds in under 1 second, making it excellent for voice assistant use cases.

### Mistral AI

Mistral offers cost-effective European AI models with strong multilingual support.

```bash
PULSE_ASSISTANT_PROVIDER="mistral"
MISTRAL_API_KEY="..."
MISTRAL_MODEL="mistral-small-latest"
```

**Models:**
- `mistral-small-latest` - Cost-effective, auto-updates
- `mistral-medium-latest` - Balanced performance
- `mistral-large-latest` - Highest quality

### OpenRouter (Model Aggregator)

OpenRouter provides access to 100+ models from various providers through a single API. Great for experimenting with different models.

```bash
PULSE_ASSISTANT_PROVIDER="openrouter"
OPENROUTER_API_KEY="sk-or-..."
OPENROUTER_MODEL="meta-llama/llama-3.3-70b-instruct"
```

**Popular models:**
- `meta-llama/llama-3.3-70b-instruct` - Open source, high quality
- `google/gemini-2.0-flash-exp` - Fast, experimental
- `anthropic/claude-3.5-sonnet` - Premium quality (requires credits)
- `mistralai/mistral-small` - Cost-effective

Browse the full catalog at [openrouter.ai/models](https://openrouter.ai/models).

### Base URL and Timeout Overrides

All providers support optional base URL and timeout overrides in case you proxy the traffic elsewhere:
- `<PROVIDER>_BASE_URL` - Custom API endpoint
- `<PROVIDER>_TIMEOUT_SECONDS` - Request timeout

### Runtime Provider Switching

You can keep all credential sets in `pulse.conf` and switch providers at runtime without restarting:

```bash
# Publish to MQTT to change provider
mosquitto_pub -h mqtt-host -t "pulse/hostname/preferences/llm_provider/set" -m "anthropic"

# Or use the Home Assistant "LLM Provider" select entity
```

The assistant rebuilds its LLM client immediately when the provider changes.

### Runtime Model Selection

You can also change models at runtime without restarting the assistant:

```bash
# Change OpenAI model
mosquitto_pub -t "pulse/hostname/preferences/openai_model/set" -m "gpt-4o"

# Change Anthropic model
mosquitto_pub -t "pulse/hostname/preferences/anthropic_model/set" -m "claude-3-5-sonnet-20241022"

# Change Groq model
mosquitto_pub -t "pulse/hostname/preferences/groq_model/set" -m "llama-3.1-8b-instant"
```

Or use the Home Assistant select entities (one per provider). Each provider remembers its preferred model when you switch between providers.

**Pro tip:** Pre-configure your preferred models for each provider, then switch providers based on your needs (speed vs quality vs cost).

### Custom System Prompt

You can override the persona via `PULSE_ASSISTANT_SYSTEM_PROMPT` or supply a path in `PULSE_ASSISTANT_SYSTEM_PROMPT_FILE`.

---

## Real-time news, weather, and sports

When the assistant hears short requests such as “What’s the news?”, “What’s the forecast today?”, or “What are the NFL headlines?”, the Pulse pipeline now answers directly without sending those prompts to the generic LLM. Responses are stitched together from public APIs so the kiosk can deliver fresh information even when the LLM prompt or cache would otherwise push toward short, vague answers.

While the answer is spoken, the kiosk overlay temporarily dedicates the six right-hand cells (top-center → bottom-right) to a large info card that mirrors the summary text. As soon as TTS finishes, the overlay clears the card and the regular timers/now-playing tiles slide back into place.

### News (NewsAPI.org)

Set `PULSE_NEWS_API_KEY` with a free NewsAPI.org key (or a compatible proxy endpoint) and optionally override:

```
PULSE_NEWS_COUNTRY="us"      # two-letter ISO country
PULSE_NEWS_CATEGORY="general"
PULSE_NEWS_LANGUAGE="en"
PULSE_NEWS_MAX_ARTICLES="5"  # used to build the 3–5 sentence summary
```

News prompts (“What’s the latest news?”, “Give me the headlines.”) summarize the newest top-headlines feed for the configured country/category. The assistant caches each fetch for ~5 minutes so consecutive questions don’t thrash the API quota.

### Weather (Open-Meteo)

The weather service relies on Open-Meteo’s free forecast endpoint. Provide any of the following in `PULSE_LOCATION`:

- Latitude/longitude pair (`37.7749,-122.4194`)
- ZIP/postal code (`30301`)
- City name (`"Pittsburgh, PA"`)
- Google Plus Code (`"849VCWC8+R9"`)
- what3words (`"index.home.raft"` + `WHAT3WORDS_API_KEY`)

Optional helpers:

```
PULSE_LANGUAGE="en"             # default language for assistant/news/weather
PULSE_WEATHER_UNITS="auto"      # auto | imperial | metric
PULSE_WEATHER_FORECAST_DAYS="3" # 1–3 day spoken summary
```

Open-Meteo is geo-only (no API key). If you provide a what3words string, drop the key in `WHAT3WORDS_API_KEY` and the assistant will translate it to coordinates before calling the forecast API.

### Sports (ESPN public endpoints)

Pulse uses ESPN’s public JSON feeds for both general sports updates and league/team drill-downs. Configure the defaults with:

```
PULSE_SPORTS_DEFAULT_COUNTRY="us"
PULSE_SPORTS_HEADLINE_COUNTRY="us"
PULSE_SPORTS_DEFAULT_LEAGUES="nfl,nba,mlb,nhl"
PULSE_SPORTS_FAVORITE_TEAMS="nfl:steelers,nhl:penguins"
```

The assistant recognizes phrases like “What’s happening in sports?”, “What are the NHL standings?”, “When is the next Steelers game?”, or “Give me the NASCAR headlines.” Favorite teams influence phrasing (“your Penguins play tomorrow night…”) but the service works without them. No key is required for the ESPN feeds.

All three services time out quickly and fall back to the LLM if an API is down. When everything is configured, the answers feel instantaneous (~1–2 seconds faster than routing through the LLM) and—most importantly—always reflect the latest public data.

While the assistant is speaking one of these summaries, the kiosk overlay temporarily replaces the six tiles from `top-center` through `bottom-right` with a large “Assistant” card that shows the exact text being read. As soon as speech finishes, the overlay reverts to the previous cards (timers, now playing, etc.) automatically.

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

When the local scheduler handles reminders it assumes:

- “Morning” is 8 AM, “afternoon” is 1 PM, “evening” is 5 PM, and “night/tonight” is 8 PM (if no explicit time is given, it defaults to 8 AM).
- Phrases like “every month” or “every 6 months” start on the current day; if that time already passed it moves forward to the next interval.
- “Show me my reminders” opens the on-device overlay with Complete/Delete/+delay buttons that mirror the MQTT commands listed below.

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
| `pulse/<host>/preferences/llm_provider/set` | in | `openai` or `gemini`; switches the active LLM without editing `pulse.conf`. |

All preference states are retained so dashboards instantly reflect the last-known values after reboots. Async listeners can treat the `/set` topics as switches/selects in Home Assistant, while the `assistant/in_progress` topic mirrors the stock HA Voice binary sensor.

---

## Display Overlay

`pulse-assistant-display.py` runs as a user-level service whenever `PULSE_VOICE_ASSISTANT="true"`. It subscribes to `pulse/<hostname>/assistant/response`, renders the text in a borderless Tk window, and auto-hides after `PULSE_ASSISTANT_DISPLAY_SECONDS` (default 8s). Tweak the font via `PULSE_ASSISTANT_FONT_SIZE`.

Now-playing metadata is displayed by the PulseOS overlay HTML (see `pulse/<hostname>:8800/overlay`), which automatically shows "Now Playing" information when music is active. The overlay is integrated with the `pulse-photo-card` in Home Assistant and displays timers, alarms, reminders, clocks, and now-playing information.

---

## Snapcast Multiroom Output

Pulse can appear as a Snapcast player so Music Assistant (or anything that can send audio to Snapserver) can target each kiosk directly.

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
3. **LLM + speech:** set the appropriate API key for your provider (`OPENAI_API_KEY` or `GEMINI_API_KEY`) and ask, “Hey Jarvis, what’s the weather tomorrow?”. You should hear Piper speak and the overlay should show the text.
4. **Actions:** add the sample JSON above and say “Okay Pulse, turn on the desk lights.” Confirm the MQTT topic fired.

If something stalls, re-run `./setup.sh` (it restarts the services), then check:

* `journalctl -u pulse-assistant.service`
* `journalctl --user -u pulse-assistant-display.service`
* `aplay -l` / `arecord -l` for audio devices

Happy tinkering!

