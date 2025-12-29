# Pulse Configuration Reference

This guide lists every `pulse.conf` variable, its default value from `pulse.conf.sample`, and what it controls. Copy the sample file, adjust the keys that apply to your kiosk, then rerun `./setup.sh` so services reload with the new values.

> 🔐 `setup.sh` enforces `chmod 600` and matches ownership to `PULSE_USER`, but keep `pulse.conf` out of version control and any shared backups—the file usually contains MQTT credentials, API tokens, and private URLs.

> **Tip:** anything left blank inherits safe defaults. Only override values that differ on your network (MQTT host, calendar feeds, Wyoming endpoints, etc.).

## Core Configuration

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_VERSION` | git tag | Automatically derived from the repo's git tag; used for OTA/update checks. |
| `PULSE_USER` | `pulse` | Linux account that auto logs in and runs the kiosk + services. |

## Kiosk & Browser

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_URL` | `http://homeassistant.local:8123/photo-frame/home?sidebar=hide` | Chromium start page (also the target of the MQTT “Home” button). |
| `PULSE_REVIVE_INTERVAL` | `2` | Minutes between cron-based watchdog sweeps that restart the kiosk stack if unhealthy. |
| `PULSE_WATCHDOG_URL` | `http://homeassistant.local:8123/static/icons/favicon.ico` | Lightweight URL Chromium fetches to prove connectivity. |
| `PULSE_WATCHDOG_LIMIT` | `5` | Consecutive watchdog fetch failures before Chromium is restarted. |
| `PULSE_WATCHDOG_INTERVAL` | `60` | Seconds between watchdog fetches. |
| `CHROMIUM_DEVTOOLS_URL` | `http://localhost:9222/json` | Remote debugging endpoint for kiosk automation. |
| `CHROMIUM_DEVTOOLS_TIMEOUT` | `3` | Timeout (seconds) for DevTools HTTP/WebSocket operations. |

## Overlay & Photo Frame

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_OVERLAY_ENABLED` | `true` | Enables the overlay HTTP server. |
| `PULSE_OVERLAY_PORT` | `8800` | TCP port serving `/overlay`. |
| `PULSE_OVERLAY_BIND` | `0.0.0.0` | Bind address for the overlay server. |
| `PULSE_OVERLAY_ALLOWED_ORIGINS` | `*` | Comma-separated CORS allow list for overlay requests. |
| `PULSE_OVERLAY_FONT_FAMILY` | `Inter` | Primary overlay font (fallback stack added automatically). |
| `PULSE_OVERLAY_AMBIENT_BG` | `rgba(0, 0, 0, 0.32)` | Background color for ambient cards. |
| `PULSE_OVERLAY_ALERT_BG` | `rgba(0, 0, 0, 0.65)` | Background color for alert cards. |
| `PULSE_OVERLAY_TEXT_COLOR` | `#FFFFFF` | Overlay text color. |
| `PULSE_OVERLAY_ACCENT_COLOR` | `#88C0D0` | Accent color for highlights. |
| `PULSE_OVERLAY_NOTIFICATION_BAR` | `true` | Toggles the badge row at the top of the overlay. |
| `PULSE_OVERLAY_CLOCK_24H` | `false` | Forces 24-hour clock labels when `true`. |

## Telemetry & MQTT

| Key | Default | Description |
| --- | --- | --- |
| `MQTT_HOST` | `mosquitto.local` | MQTT broker hostname/IP. |
| `MQTT_PORT` | `1883` | Broker TCP port. |
| `MQTT_USER` | *(empty)* | Optional MQTT username. |
| `MQTT_PASS` | *(empty)* | Optional MQTT password. |
| `MQTT_TLS_ENABLED` | `false` | Enable TLS for MQTT when `true`. |
| `MQTT_CERT` | *(empty)* | Client certificate path (TLS only). |
| `MQTT_KEY` | *(empty)* | Client key path (TLS only). |
| `MQTT_CA_CERT` | *(empty)* | CA certificate path (TLS only). |
| `PULSE_VERSION_CHECKS_PER_DAY` | `12` | Daily frequency for checking GitHub releases (2/4/6/8/12/24). |
| `PULSE_TELEMETRY_INTERVAL_SECONDS` | `15` | Seconds between telemetry publishes (minimum 5s). |

## Device Preferences & Toggles

> **Tip:** Many of these preferences can be changed at runtime via MQTT and are automatically persisted back to `pulse.conf`. See [MQTT and Telemetry → Assistant Preferences](mqtt-and-telemetry.md#assistant-preferences-mqtt--config) for the full mapping between MQTT topic keys and config variable names.

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_LOCATION` | *(empty)* | Preferred location string for weather and sunrise/sunset (`lat,lon`, ZIP, `City, ST`, plus code, or what3words). |
| `PULSE_LANGUAGE` | `en` | Default language for assistant, news, and weather. |
| `PULSE_DAY_NIGHT_AUTO` | `true` | Sunrise/sunset-driven backlight changes. |
| `PULSE_DAY_BRIGHTNESS` | `85` | Daytime brightness target (%) used by sunrise/sunset automation. |
| `PULSE_NIGHT_BRIGHTNESS` | `25` | Nighttime brightness target (%) used by sunrise/sunset automation. |
| `PULSE_TWILIGHT_MODE` | `OFFICIAL` | Twilight definition (`OFFICIAL`, `CIVIL`, `NAUTICAL`, `ASTRONOMICAL`). |
| `PULSE_BACKLIGHT_DEVICE` | `/sys/class/backlight/11-0045` | Backlight device path (auto-detect if unset). |
| `PULSE_VOLUME_TEST_SOUND` | `true` | Plays a short “thump” after MQTT volume changes. |
| `PULSE_BLUETOOTH_AUTOCONNECT` | `true` | Reconnects to the last paired Bluetooth speaker and sends keepalives. |
| `PULSE_BT_MAC` | *(empty)* | Optional explicit Bluetooth MAC to target. |

## Sounds

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_SOUNDS_DIR` | `~/.local/share/pulse/sounds` | Directory for user-provided WAV/OGG files. |
| `PULSE_SOUND_ALARM` | `alarm-digital-rise` | Default alarm sound (id or filename from the sound pack or custom dir). |
| `PULSE_SOUND_TIMER` | `timer-woodblock` | Default timer sound (falls back to `PULSE_SOUND_ALARM` if unset). |
| `PULSE_SOUND_REMINDER` | `reminder-marimba` | Default reminder sound. |
| `PULSE_SOUND_NOTIFICATION` | `notify-soft-chime` | Default notification/volume chime sound. |

## Logging & Reliability

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_REMOTE_LOGGING` | `true` | Forwards syslog entries to a remote host. |
| `PULSE_REMOTE_LOG_HOST` | `192.168.1.100` | Remote syslog target. |
| `PULSE_REMOTE_LOG_PORT` | `5514` | Remote syslog UDP port. |
| `PULSE_DAILY_REBOOT_ENABLED` | `false` | Enables the legacy 3 AM safe reboot timer. |
| `PULSE_REBOOT_MIN_UPTIME_SECONDS` | `300` | Minimum uptime before allowing another automatic reboot. |
| `PULSE_REBOOT_WINDOW_SECONDS` | `900` | Rolling window (seconds) to count automated reboots. |
| `PULSE_REBOOT_MAX_COUNT` | `3` | Maximum automated reboots within the window above. |

## Voice Assistant — Pipeline & Routing

> **Note:** Settings marked with ⚡ can be changed at runtime via MQTT selects/switches in Home Assistant and are automatically persisted to `pulse.conf`. See [MQTT and Telemetry → Assistant Preferences](mqtt-and-telemetry.md#assistant-preferences-mqtt--config) for the mapping between MQTT keys and config variables.

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_VOICE_ASSISTANT` | `false` | Enables the Wyoming + LLM assistant stack. |
| `PULSE_ASSISTANT_WAKE_WORDS_PULSE` | `hey_jarvis` | Comma-separated wake models handled locally. |
| `PULSE_ASSISTANT_WAKE_WORDS_HA` | `hey_house` | Models that should route through Home Assistant Assist. |
| `PULSE_ASSISTANT_WAKE_ROUTES` | *(empty)* | Explicit `model=pipeline` overrides. |
| `PULSE_ASSISTANT_LANGUAGE` | `en` | Language hint passed to STT/LLM layers. |
| `PULSE_ASSISTANT_WAKE_SOUND` | `true` | Plays a chime when a wake word fires. |
| `PULSE_ASSISTANT_SPEAKING_STYLE` | `normal` | Assistant persona (`relaxed`, `normal`, `aggressive`). |
| `PULSE_ASSISTANT_WAKE_SENSITIVITY` | `normal` | openWakeWord trigger sensitivity (`low`, `normal`, `high`). |
| `PULSE_ASSISTANT_HA_RESPONSE_MODE` | `full` | ⚡ Spoken response style for HA actions (`none`, `tone`, `minimal`, `full`). |
| `PULSE_ASSISTANT_HA_TONE_SOUND` | `alarm-sonar` | ⚡ Sound id to play when `HA_RESPONSE_MODE` is `tone` (built-in or custom sound id). |
| `PULSE_ASSISTANT_SELF_AUDIO_TRIGGER_LEVEL` | `7` | Trigger level enforced while kiosk audio is playing (min 2). |
| `PULSE_ASSISTANT_LOG_LEVEL` | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Set to `DEBUG` to see detailed logs including transcripts, responses, LLM actions, and calendar sync/event processing. |
| `PULSE_ASSISTANT_LOG_LLM` | `true` | Publish transcripts/responses to MQTT (for dashboards/automation). |
| `PULSE_ASSISTANT_LOG_TRANSCRIPTS` | `false` | Log transcripts/responses locally at INFO. |
| `PULSE_ASSISTANT_TOPIC_BASE` | `pulse/<hostname>/assistant` | Override MQTT topic base (leave blank unless you need a custom layout). |
| `PULSE_ASSISTANT_MIC_RATE` | `16000` | Override mic sample rate (advanced). |
| `PULSE_ASSISTANT_MIC_WIDTH` | `2` | Override mic sample width bytes (advanced). |
| `PULSE_ASSISTANT_MIC_CHANNELS` | `1` | Override mic channels (advanced). |
| `PULSE_ASSISTANT_MIC_CHUNK_MS` | `30` | Override mic chunk size in ms (advanced). |
| `PULSE_ASSISTANT_STT_MODEL` | *(empty)* | Optional STT model hint when backend exposes multiple models. |

## Media & Alerts

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_MEDIA_PLAYER_ENTITY` | *(empty)* | Primary media_player/sensor for kiosk audio; defaults to media_player.\<hostname\> if blank. |
| `PULSE_MEDIA_PLAYER_ENTITIES` | *(empty)* | Comma-separated list of additional media players. |
| `PULSE_ALERT_TOPICS` | *(empty)* | Comma-separated MQTT alert topics to subscribe to. |
| `PULSE_INTERCOM_TOPIC` | `pulse/<hostname>/assistant/intercom` | Custom intercom topic override. |

### Wyoming Endpoints

| Key | Default | Description |
| --- | --- | --- |
| `WYOMING_WHISPER_HOST` | *(empty)* | wyoming-whisper host (speech-to-text). |
| `WYOMING_WHISPER_PORT` | `10300` | wyoming-whisper port. |
| `WYOMING_PIPER_HOST` | *(empty)* | wyoming-piper host (text-to-speech). |
| `WYOMING_PIPER_PORT` | `10200` | wyoming-piper port. |
| `WYOMING_OPENWAKEWORD_HOST` | *(empty)* | wyoming-openwakeword host. |
| `WYOMING_OPENWAKEWORD_PORT` | `10400` | wyoming-openwakeword port. |

### Mic Capture & Phrase Detection

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_ASSISTANT_MIC_CMD` | `arecord -q -t raw -f S16_LE -c 1 -r 16000 -` | Command used to stream PCM audio into the assistant pipeline. |
| `PULSE_ASSISTANT_MIN_PHRASE_SECONDS` | `1.5` | Minimum captured speech length before accepting a phrase. |
| `PULSE_ASSISTANT_MAX_PHRASE_SECONDS` | `8` | Maximum capture duration for a single phrase. |
| `PULSE_ASSISTANT_SILENCE_MS` | `1200` | Silence threshold that ends a phrase. |
| `PULSE_ASSISTANT_RMS_THRESHOLD` | `120` | RMS floor for background noise rejection. |

### LLM Provider & Prompting

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_ASSISTANT_PROVIDER` | `openai` | Active LLM backend: `openai`, `gemini`, `anthropic`, `groq`, `mistral`, or `openrouter`. |
| `OPENAI_API_KEY` | *(empty)* | API key used when provider is OpenAI. |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI chat/completions model. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Optional OpenAI-compatible proxy. |
| `OPENAI_TIMEOUT_SECONDS` | `45` | Timeout for OpenAI requests. |
| `GEMINI_API_KEY` | *(empty)* | API key for Gemini provider. |
| `GEMINI_MODEL` | `gemini-1.5-flash-latest` | Gemini model name. |
| `GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta` | Optional Gemini proxy URL. |
| `GEMINI_TIMEOUT_SECONDS` | `45` | Timeout for Gemini requests. |
| `ANTHROPIC_API_KEY` | *(empty)* | API key for Anthropic Claude models. |
| `ANTHROPIC_MODEL` | `claude-3-5-haiku-20241022` | Anthropic Claude model identifier. |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com/v1` | Anthropic API endpoint. |
| `ANTHROPIC_TIMEOUT_SECONDS` | `45` | Request timeout for Anthropic calls. |
| `GROQ_API_KEY` | *(empty)* | API key for Groq inference service. |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model identifier. |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | Groq API endpoint. |
| `GROQ_TIMEOUT_SECONDS` | `30` | Request timeout for Groq calls. |
| `MISTRAL_API_KEY` | *(empty)* | API key for Mistral AI. |
| `MISTRAL_MODEL` | `mistral-small-latest` | Mistral model identifier. |
| `MISTRAL_BASE_URL` | `https://api.mistral.ai/v1` | Mistral API endpoint. |
| `MISTRAL_TIMEOUT_SECONDS` | `45` | Request timeout for Mistral calls. |
| `OPENROUTER_API_KEY` | *(empty)* | API key for OpenRouter. |
| `OPENROUTER_MODEL` | `meta-llama/llama-3.3-70b-instruct` | OpenRouter model identifier (see openrouter.ai/models). |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter API endpoint. |
| `OPENROUTER_TIMEOUT_SECONDS` | `45` | Request timeout for OpenRouter calls. |
| `PULSE_ASSISTANT_SYSTEM_PROMPT` | *(empty)* | Inline system prompt string. |
| `PULSE_ASSISTANT_SYSTEM_PROMPT_FILE` | *(empty)* | Path to a file containing the system prompt. |
| `PULSE_ASSISTANT_TTS_VOICE` | *(empty)* | Preferred Piper voice (falls back to server default). |
| `PULSE_ASSISTANT_ACTIONS_FILE` | `/opt/pulse-os/pulse-assistant-actions.json` | JSON file defining shortcut actions. |
| `PULSE_ASSISTANT_ACTIONS` | *(empty)* | Inline JSON string for shortcuts (same schema as the file). |

### On-screen Responses & Media

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_ASSISTANT_DISPLAY_SECONDS` | `8` | Duration (seconds) that the Tk response overlay remains visible. |
| `PULSE_ASSISTANT_FONT_SIZE` | `28` | Font size for the response overlay. |
| `PULSE_MEDIA_PLAYER_ENTITY` | *(empty)* | HA media_player entity ID that represents this kiosk (defaults to `media_player.<hostname>`). |

## Information Services

### News (NewsAPI-compatible)

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_NEWS_API_KEY` | *(empty)* | API key for NewsAPI.org (or compatible) endpoints. |
| `PULSE_NEWS_BASE_URL` | `https://newsapi.org/v2` | Base URL for news calls. |
| `PULSE_NEWS_COUNTRY` | `us` | Two-letter country code for top headlines. |
| `PULSE_NEWS_CATEGORY` | `general` | News category (general, sports, etc.). |
| `PULSE_NEWS_MAX_ARTICLES` | `5` | Maximum number of articles to read per request. |
| *(override)* `PULSE_NEWS_LANGUAGE` | *(empty)* | Optional language override (defaults to `PULSE_LANGUAGE`). |

### Weather (Open-Meteo)

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_LOCATION` | *(empty)* | Lat/long, city, ZIP, plus code, or what3words string. |
| `PULSE_WEATHER_BASE_URL` | `https://api.open-meteo.com/v1/forecast` | Base API URL. |
| `PULSE_WEATHER_UNITS` | `auto` | Units (`auto`, `imperial`, `metric`). |
| `PULSE_WEATHER_FORECAST_DAYS` | `3` | Number of daily forecasts (1–5). |
| `WHAT3WORDS_API_KEY` | *(empty)* | Optional key to resolve `what3words://` inputs. |
| *(override)* `PULSE_WEATHER_LANGUAGE` | *(empty)* | Optional language override (defaults to `PULSE_LANGUAGE`). |

### Sports (ESPN)

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_SPORTS_BASE_URL` | `https://site.api.espn.com/apis` | ESPN public API base. |
| `PULSE_SPORTS_DEFAULT_COUNTRY` | `us` | Default region for scoreboard lookups. |
| `PULSE_SPORTS_HEADLINE_COUNTRY` | `us` | Region for headline feeds. |
| `PULSE_SPORTS_DEFAULT_LEAGUES` | `nfl,nba,mlb,nhl` | Comma-separated leagues to pull automatically. |
| `PULSE_SPORTS_FAVORITE_TEAMS` | *(empty)* | Comma-separated “favorite teams” list for highlight bias. |

## Calendar Sync (ICS/WebCal)

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_CALENDAR_ICS_URLS` | *(empty)* | Comma-separated ICS/WebCal feed URLs (`webcal://` or `https://`). |
| `PULSE_CALENDAR_REFRESH_MINUTES` | `5` | Minutes between feed polls (minimum 1). |
| `PULSE_CALENDAR_LOOKAHEAD_HOURS` | `72` | Look-ahead window used for scheduling reminders and overlay snapshots. |
| `PULSE_CALENDAR_OWNER_EMAILS` | *(empty)* | Comma-separated attendee emails treated as "me" (declined events are shown but reminders are suppressed). |
| `PULSE_CALENDAR_DEFAULT_NOTIFICATIONS` | *(empty)* | Comma-separated default notification times (minutes before event start) to apply to all events. Supplements VALARM entries in ICS files. **Note:** Google Calendar's default notification (usually 10 minutes before) is NOT included in the ICS export, so you should set at least `"10"` here to mimic that behavior. Example: `"10,5"` adds 10-minute and 5-minute reminders to all events. You can add additional default notifications that will apply to ALL events. Duplicates (within 30 seconds) are automatically deduplicated. |
| `PULSE_CALENDAR_HIDE_DECLINED` | `false` | Set to `true` to hide declined events entirely. When `false` (default), declined events are shown but reminders are suppressed. |
| `PULSE_CALENDAR_OOO_MARKER` | `OOO` | Case-insensitive marker matched against all-day event summaries to treat them as “out of office” and skip alarms on those dates. |
| `PULSE_WORK_ALARM_SKIP_DATES` | *(empty)* | Comma-separated ISO dates (`YYYY-MM-DD`) to skip all alarms (useful for holidays). |
| `PULSE_WORK_ALARM_SKIP_DAYS` | *(empty)* | Comma-separated weekdays to skip alarms (`0`=Mon … `6`=Sun or names such as `Mon,Fri`). |

## Snapcast Client (Optional)

| Key | Default | Description |
| --- | --- | --- |
| `PULSE_SNAPCLIENT` | `false` | Enables Snapclient on the kiosk. |
| `PULSE_SNAPCAST_HOST` | *(empty)* | Snapserver host. |
| `PULSE_SNAPCAST_PORT` | `1704` | Snapserver PCM stream port. |
| `PULSE_SNAPCAST_CONTROL_PORT` | `1705` | Snapserver control port. |
| `PULSE_SNAPCLIENT_SOUNDCARD` | `default` | Soundcard argument passed to snapclient. |
| `PULSE_SNAPCLIENT_LATENCY_MS` | *(empty)* | Optional latency override (milliseconds). |
| `PULSE_SNAPCLIENT_EXTRA_ARGS` | `--player pulse` | Extra CLI flags for snapclient. |
| `PULSE_SNAPCLIENT_HOST_ID` | *(empty)* | Override the host ID that Snapserver sees (defaults to hostname). |

## Home Assistant Integration

| Key | Default | Description |
| --- | --- | --- |
| `HOME_ASSISTANT_BASE_URL` | `http://homeassistant.local:8123` | HA base URL for Assist/API calls. |
| `HOME_ASSISTANT_TOKEN` | *(empty)* | Long-lived token used for HA REST and Assist requests. |
| `HOME_ASSISTANT_VERIFY_SSL` | `true` | Enforce TLS certificate validation. |
| `HOME_ASSISTANT_ASSIST_PIPELINE` | *(empty)* | Optional Assist pipeline ID override. |
| `HOME_ASSISTANT_OPENWAKEWORD_HOST` | *(empty)* | HA-hosted wyoming-openwakeword host. |
| `HOME_ASSISTANT_OPENWAKEWORD_PORT` | *(empty)* | HA-hosted wyoming-openwakeword port. |
| `HOME_ASSISTANT_WHISPER_HOST` | *(empty)* | HA-hosted wyoming-whisper host. |
| `HOME_ASSISTANT_WHISPER_PORT` | *(empty)* | HA-hosted wyoming-whisper port. |
| `HOME_ASSISTANT_STT_MODEL` | *(empty)* | Model requested from the HA wyoming-whisper endpoint. |
| `HOME_ASSISTANT_PIPER_HOST` | *(empty)* | HA-hosted wyoming-piper host. |
| `HOME_ASSISTANT_PIPER_PORT` | *(empty)* | HA-hosted wyoming-piper port. |
| `HOME_ASSISTANT_TIMER_ENTITY` | *(empty)* | HA timer entity to manage via MQTT/voice (falls back to local scheduler when empty). |
| `HOME_ASSISTANT_REMINDER_SERVICE` | *(empty)* | HA notification service used for reminders. |

---

Need a quick refresher on what each feature does? Check the [README](../README.md) for the high-level overview, then come back to this reference when you're wiring up a new kiosk or migrating settings. Any new variables will always appear in `pulse.conf.sample`, and this document mirrors that file so you can diff the two during upgrades.

