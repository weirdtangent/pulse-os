# Assistant Command Reference

This guide lists the phrases PulseOS handles *before* the LLM ever sees them. When the wake word pipeline hears one of these requests the assistant executes the task directly (using the on-device scheduler, MQTT, or the configured APIs) and only falls back to the LLM for everything else.

> **Prerequisites**
>
> - `PULSE_VOICE_ASSISTANT="true"` with working Wyoming endpoints (wake word, STT, TTS)
> - `HOME_ASSISTANT_*`/`PULSE_MEDIA_PLAYER_ENTITY` for music controls
> - `PULSE_NEWS_API_KEY`, `PULSE_WEATHER_LOCATION`, `PULSE_SPORTS_*` for real-time info (see `docs/voice-assistant.md`)

## Alarms

| Example phrase | What happens internally |
| --- | --- |
| “Set an alarm for 8 a.m.” | `_maybe_handle_schedule_shortcut` parses the time and calls `ScheduleService.create_alarm()` for a one-shot alarm. |
| “Set an alarm for 8 a.m. every day.” | The same shortcut detects `every day/weekdays/weekends` style tokens (via `parse_day_tokens`) and creates a repeating alarm. |
| “Cancel my alarm” / “Delete the 8 a.m. alarm.” | `_mentions_alarm_cancel` looks for “cancel/remove/delete” plus “alarm” and calls `ScheduleService.delete_event()` on the matching entry. |
| “When is my next alarm?” | `get_next_alarm()` returns the soonest alarm and the assistant speaks a summary (also published on MQTT). |
| “Show me my alarms.” | The assistant gathers the current alarm list, publishes an `info_card` payload (rendered as the on-screen list with red × buttons), and announces how many alarms exist. |

## Timers

| Example phrase | What happens internally |
| --- | --- |
| “Set a 5 minute timer.” | Duration is parsed (seconds/minutes/hours) and `ScheduleService.create_timer()` starts a local timer. |
| “Add three minutes to the timer.” | `_extend_timer_shortcut()` adds the requested time to the active (or labeled) timer. |
| “Cancel my timer.” | `_cancel_timer_shortcut()` stops the labeled or most recent timer. |
| “Cancel all timers.” | Calls `ScheduleService.cancel_all_timers()` and responds with how many timers were removed. |
| “Stop the timer/Stop the alarm.” | `_is_stop_phrase()` routes to `_stop_active_schedule()`, which in turn calls `ScheduleService.stop_event()` for the ringing alarm or timer. |

## Alarm & timer overlays

- Tapping “OK” on a ringing timer/alarm posts `/overlay/stop`, which maps to the MQTT `{"action": "stop"}` command.
- Saying “Show me my alarms” keeps the overlay open until you close it or delete alarms via the on-screen × buttons (each button issues `{"action": "delete_alarm", "event_id": "<id>"}` over MQTT).

## Real-time info (News, Weather, Sports)

| Category | Example phrase | Internal behavior |
| --- | --- | --- |
| Headlines | “What’s the news?” | The assistant bypasses the LLM, hits NewsAPI (or compatible endpoint) using `PULSE_NEWS_API_KEY`, and speaks the latest headlines. |
| Weather | “What’s the weather tomorrow?” / “Will it rain today?” | Uses Open-Meteo + the configured location (`PULSE_WEATHER_LOCATION`) to summarize the current or upcoming forecast. |
| Sports | “What are the NFL standings?” / “When do the Penguins play next?” | Fetches ESPN public feeds, honors `PULSE_SPORTS_DEFAULT_*` and favorites, and returns the requested standings/schedule. |

If any of the above APIs are offline the assistant still replies (and logs the failure) without involving the LLM so wake word requests stay fast.

## Music controls

Requires `HOME_ASSISTANT_*` credentials and `PULSE_MEDIA_PLAYER_ENTITY`.

- “Pause the music” / “Stop the music” / “Next song” → direct Home Assistant `media_player` service calls.
- “What song is this?” / “Who is this?” → Reads the current `media_title` / `media_artist` attributes and speaks them.

The assistant also auto-pauses the configured player when you say the wake word and resumes playback ~2 seconds after speaking a response.

## Home Assistant action slugs

When the LLM is in play you can still issue deterministic shortcuts without depending on natural language parsing:

```
ha.turn_on:light.kitchen
ha.turn_off:switch.projector
timer.start:duration=10m,label=cookies
reminder.create:when=2025-01-01T09:00,message=Example
```

These appear in the system prompt so the LLM can call them exactly as entered. Use them when you want guaranteed behavior (“run this shortcut”) instead of a conversational request.

## Tips

- Use `bin/tools/verify-conf.py` anytime alarms/timers don’t react; it validates MQTT, Wyoming, and HA connectivity.
- The assistant logs every shortcut it handles in `journalctl -u pulse-assistant.service`. Set `PULSE_ASSISTANT_LOG_LLM="false"` to suppress user transcript excerpts.
- All alarm/timer changes are mirrored over MQTT (`pulse/<hostname>/assistant/schedules/state`) so dashboards or automations stay in sync.

