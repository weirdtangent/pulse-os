# Notes & Extras

Collected reference material that used to live in the README. Each section summarizes a feature and points to the deeper docs when available.

---

## Voice assistant (preview)

The `pulse-assistant` daemon streams wake audio to your Wyoming servers, calls the configured LLM, then speaks and displays the reply. Configure `PULSE_ASSISTANT_*`, `WYOMING_*`, and `OPENAI_*`/`GEMINI_*` in `pulse.conf`, rerun `./setup.sh`, and review [`docs/voice-assistant.md`](voice-assistant.md) for deployment diagrams.

### Real-time headlines, forecasts, and sports

Short prompts such as "What's the news?", "What's the weather tomorrow?", "What are the NFL standings?", or "When do the Penguins play next?" are intercepted before they reach the LLM. The assistant uses:

- **NewsAPI.org** (or any compatible endpoint) for the latest US/global headlines — set `PULSE_NEWS_API_KEY`, country, category, and language in `pulse.conf`.
- **Open-Meteo** forecasts for any location (`PULSE_WEATHER_LOCATION` accepts lat/lon, ZIP, city, Google Plus Code, or what3words with `WHAT3WORDS_API_KEY`). Adjust units/language/day count via `PULSE_WEATHER_UNITS`, `PULSE_WEATHER_LANGUAGE`, and `PULSE_WEATHER_FORECAST_DAYS`.
- **ESPN public feeds** for general sports headlines, league summaries, standings, and favorite teams. Configure default countries/leagues with `PULSE_SPORTS_DEFAULT_COUNTRY`, `PULSE_SPORTS_DEFAULT_LEAGUES`, and seed `PULSE_SPORTS_FAVORITE_TEAMS` so prompts like "When is the next Steelers game?" have context.

Responses are spoken immediately (and published on the MQTT response topic) even if the LLM is offline. See the "Real-time news, weather, and sports" section in [`docs/voice-assistant.md`](voice-assistant.md#real-time-news-weather-and-sports) for the full variable list.

### Dual wake-word pipelines

Map each wake word to the local pipeline or the Home Assistant pipeline:

| Variable | Description |
| --- | --- |
| `PULSE_ASSISTANT_WAKE_WORDS_PULSE` | Comma-separated list for the local LLM flow (e.g., `hey_jarvis`). |
| `PULSE_ASSISTANT_WAKE_WORDS_HA` | List for HA Assist (e.g., `hey_house,hey_nabu`). |
| `PULSE_ASSISTANT_WAKE_ROUTES` | Optional explicit mapping (`hey_jarvis=pulse,hey_house=home_assistant`). |

`assistant/state` always includes the active pipeline so dashboards can display which route handled the request.

### Wake trigger level while Pulse is speaking

When the kiosk is already playing audio we enforce a higher openWakeWord trigger level so it does not react to its own speech. Tune `PULSE_ASSISTANT_SELF_AUDIO_TRIGGER_LEVEL` (default **7**) if you need to balance false positives vs. responsiveness:

- Raise the value if music at moderate volume still wakes Jarvis.
- Lower it (minimum **2**) if you often say the wake word softly while the kiosk is making noise.
- When Home Assistant access is configured and `PULSE_MEDIA_PLAYER_ENTITY` points at your Music Assistant player, the assistant auto-pauses that entity as soon as you say the wake word and resumes playback ~2 s after the spoken response finishes.

### Voice music controls

With `HOME_ASSISTANT_*` credentials and `PULSE_MEDIA_PLAYER_ENTITY` set, you can ask the Pulse pipeline to control or describe the active Music Assistant player directly. Example phrases:

- "Pause the music", "Stop the music", "Next song".
- "What song is this?" / "Who is this?" → pulls artist/title from the media player attributes and reads them back.

### Transcript logging opt-out

Local transcript/response logging to `journalctl -u pulse-assistant` is controlled by `PULSE_ASSISTANT_LOG_TRANSCRIPTS` (default false). Publishing those payloads to MQTT is controlled separately via `PULSE_ASSISTANT_LOG_LLM` (or the MQTT switch `pulse/<hostname>/assistant/preferences/log_llm`).

### Home Assistant actions, timers, and reminders

Set `HOME_ASSISTANT_BASE_URL` and `HOME_ASSISTANT_TOKEN`, then add `HOME_ASSISTANT_TIMER_ENTITY` / `HOME_ASSISTANT_REMINDER_SERVICE` if you use those helpers. The assistant can then:

- Execute action slugs such as `ha.turn_on:light.kitchen` or `ha.turn_off:switch.projector`.
- Start timers/reminders via `timer.start` and `reminder.create`. If the HA helpers are missing, the built-in scheduler handles both locally.
- Stream wake audio through HA Assist pipelines when a wake word is mapped to `home_assistant`. Pulse falls back to your Piper endpoint if HA does not return TTS audio.

### MQTT telemetry & knobs

Every Pulse assistant publishes real-time status and accepts config commands under `pulse/<hostname>/assistant/...`:

| Topic | Description |
| --- | --- |
| `assistant/state` | JSON payload with `state`, `pipeline`, `stage`, and `wake_word`. |
| `assistant/in_progress` | `ON` while a wake-word interaction is running; `OFF` otherwise. |
| `assistant/metrics` | JSON timing info per request (`pipeline`, `wake_word`, per-stage milliseconds). |
| `preferences/wake_sound/set` + `/state` | Turn the wake chime on/off (`on`/`off`). |
| `preferences/speaking_style/set` + `/state` | Pick `relaxed`, `normal`, or `aggressive` for the Pulse pipeline persona. |
| `preferences/wake_sensitivity/set` + `/state` | `low`, `normal`, or `high` (maps to openWakeWord trigger levels 5/3/2). |
| `preferences/llm_provider/set` + `/state` | `openai` or `gemini`; switches the active model without editing `pulse.conf`. |

Use these topics as MQTT selects/switches in Home Assistant or publish to them directly; they are retained so dashboards repopulate immediately after a reboot.

### Troubleshooting tips

- Run `bin/tools/verify-conf.py` whenever Assist calls fail; it confirms MQTT, Wyoming services, and HA credentials.
- For self-signed HA hosts, keep TLS verification on and provide the CA: set `REQUESTS_CA_BUNDLE=/path/to/ca.pem`, install the same CA into Chromium's profile with `certutil`, then restart `pulse-kiosk`.
- Store extra environment variables in systemd drop-ins (e.g., `/etc/systemd/system/pulse-assistant.service.d/override.conf`) because `pulse.conf` is regenerated from the sample.
- Confirm custom HA hostnames resolve to the actual HA server; mismatched DNS produces `/api/assist_pipeline/run` 404s.
- Watch `journalctl -u pulse-assistant.service -f` to see which pipeline handled each wake word and whether HA responded.

---

## MQTT buttons & telemetry sensors

PulseOS can expose Home/Update/Reboot buttons and a full health sensor suite over MQTT discovery. The setup, sudo requirements, topics, and tuning tips live in [`docs/mqtt-and-telemetry.md`](mqtt-and-telemetry.md) so the README can stay short and still link to the details.

---

## Home Assistant trusted-network example

I land most kiosks on a Home Assistant dashboard. To avoid session prompts (and long-lived tokens inside Chromium) I trust the kiosk's IP:

```yaml
http:
  use_x_forwarded_for: true

homeassistant:
  auth_providers:
    - type: trusted_networks
      trusted_networks:
        - 192.168.1.150/32
        - 127.0.0.1
      trusted_users:
        192.168.1.150:
          - <ha_user_id>
      allow_bypass_login: true
    - type: homeassistant
```

Duplicate the IP stanza for each kiosk you deploy.

---

## Home Assistant photo frame dashboard

Follow [`docs/home-assistant-photo-frame.md`](home-assistant-photo-frame.md) for the slideshow dashboard used on the kiosk.

- Random image helper sensors (command_line + template)
- Installing the custom `pulse-photo-card` resource
- Lovelace YAML for a full-screen panel view with double-buffered crossfades
- The kiosk-hosted `/overlay` endpoint + MQTT refresh topic that keeps clocks/timers/Now Playing overlays in sync with zero extra Lovelace code

The card keeps time/date overlaid, prevents white flashes between photos, and handles HA reconnects.

---

## Volume feedback thump

The notification tone that plays after each volume change (and when the assistant starts listening) lives in `assets/sounds/notification.wav`. Run `bin/tools/generate-notification-tone.py` if you ever want to regenerate or remix the WAV (for example, to tweak duration or pitch). The script copies the result into `assets/`, and the runtime automatically stages the file under the active user's `XDG_RUNTIME_DIR`.

---

## Cases & printable accessories

This enclosure fits the Pi Touch Display 2 with a Pi 5 mounted on the back:
<https://makerworld.com/en/models/789481-desktop-case-for-raspberry-pi-7-touch-display-2#profileId-1868464>

The `/models` directory also includes STL/SCAD files for the ReSpeaker stand, plate, and cover; and BoomPod cup. Mount the stand behind the display to keep the microphone array out of sight. The BoomPod can be glued down to one of the legs.

---

## Boot splash assets

`setup.sh` deploys the splash assets automatically:

- `assets/splash/graystorm-pulse_splash.png` becomes the Plymouth theme so Linux boot output stays hidden until X starts.
- `assets/splash/boot-splash.tga` (24-bit, 1280×720) is copied to `/lib/firmware/boot-splash.tga`, and the bootloader is set to `fullscreen_logo=1 fullscreen_logo_name=boot-splash.tga`.
- `assets/splash/boot-splash.rgb` (RGB565, 1280×720) is copied to `/boot/firmware/splash.rgb` for firmware builds that still expect the raw framebuffer format.
- Kernel args such as `quiet splash loglevel=3 vt.global_cursor_default=0 plymouth.ignore-serial-consoles` are enforced so messages and cursors stay out of sight.
- Plymouth quit units are delayed until `graphical.target`, so the splash stays up until X is ready.

Update either asset and rerun `./setup.sh <location>` to refresh the splash on an existing kiosk.

---

## Touch Display boot config pins

`setup.sh` also pins the Raspberry Pi Touch Display defaults so you don't have to edit boot files by hand:

- Adds `dtparam=i2c_arm=on` and `display_auto_detect=0` inside `/boot/firmware/config.txt`.
- Ensures the overlay `dtoverlay=vc4-kms-dsi-ili9881-7inch,rotation=90,dsi1,swapxy,invx` is present.
- Appends `video=DSI-2:720x1280M@60` to `/boot/firmware/cmdline.txt`.

---

## Development & CI

### Running tests locally

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package in development mode
pip install -e ".[dev]"

# Run linting and tests
ruff check .
black --check .
pytest
```

### GitHub Actions workflow

The repository includes a GitHub Actions workflow (`.github/workflows/build.yaml`) that runs on every push to `main` and on all pull requests:

| Job | Description |
| --- | ----------- |
| **Lint & Format** | Runs `ruff check` and `black --check` to ensure code quality |
| **Test (Python 3.13)** | Runs the full pytest suite on Python 3.13 |
| **Test (Python 3.14)** | Runs the full pytest suite on Python 3.14 |
| **Semantic Release** | Creates releases when commits follow conventional commit format (only on push to `main`) |

All lint and test jobs must pass before the release job runs. The VERSION file is automatically updated after each release.

### Branch protection (recommended)

To prevent broken code from being merged, set up a branch ruleset in your GitHub repository:

1. Go to **Settings → Rules → Rulesets**
2. Click **New ruleset → New branch ruleset**
3. Configure the ruleset:
   - **Ruleset name**: `Protect main`
   - **Enforcement status**: Active
   - **Target branches**: Add target → Include default branch (or type `main`)
4. Under **Rules**, enable:
   - ✅ **Require a pull request before merging**
   - ✅ **Require status checks to pass** → Add checks:
     - `Lint & Format`
     - `Test (Python 3.13)`
     - `Test (Python 3.14)`
   - ✅ **Block force pushes**
5. Click **Create**

With these settings, GitHub will block any PR merge until all CI checks pass.

> **Note**: You can also use **Settings → Branches → Add classic branch protection rule** for the legacy UI, but rulesets are GitHub's recommended approach.

---

## What's been built

This started as a hobby project and has grown into a fairly complete smart display platform:

- ✅ **Voice assistant** — Wyoming-based wake detection, Whisper STT, LLM processing (OpenAI/Gemini), Piper TTS, with dual pipeline support for local and Home Assistant flows
- ✅ **Photo frame dashboard** — Home Assistant integration with the custom `pulse-photo-card`, overlay clock/timers/notifications, and MQTT-driven refresh
- ✅ **Custom boot experience** — Plymouth splash theme and firmware boot logo for a polished startup
- ✅ **Real-time info** — News, weather, and sports queries intercepted before reaching the LLM
- ✅ **Alarms, timers, reminders** — Voice-controlled scheduling with on-screen overlay and music playback support
- ✅ **MQTT telemetry** — Full device health, volume/brightness controls, and Home Assistant discovery
- ✅ **CI/CD pipeline** — Automated linting, testing (Python 3.13/3.14), and semantic releases

## Future ideas

- Interactive overlay widgets (light controls, routine triggers)
- Calendar event display on the photo frame
- Multi-room audio coordination improvements
- Friendlier error screens when services are unavailable

One step at a time. 🙂
