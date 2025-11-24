# Assistant Command Reference

This guide lists the phrases the Pulse display understands immediately—no cloud model required. When you say any of these commands, the kiosk performs them on its own and only calls the LLM for everything else.

> **Prerequisites**
>
> - `PULSE_VOICE_ASSISTANT="true"` with working Wyoming endpoints (wake word, STT, TTS)
> - `HOME_ASSISTANT_*`/`PULSE_MEDIA_PLAYER_ENTITY` for music controls
> - `PULSE_NEWS_API_KEY`, `PULSE_WEATHER_LOCATION`, `PULSE_SPORTS_*` for real-time info (see `docs/voice-assistant.md`)

## Alarms

| Example phrase | What the assistant does |
| --- | --- |
| “Set an alarm for 8 a.m.” | Schedules a one-time alarm for 8:00 AM and reads the confirmation aloud. |
| “Set an alarm for 8 a.m. every day.” | Creates a repeating alarm with the cadence you mentioned (every day, weekdays, weekends, etc.). |
| “Cancel my alarm” / “Delete the 8 a.m. alarm.” | Finds the alarm that matches your description, removes it, and confirms it’s gone. |
| “When is my next alarm?” | Reads the next alarm on your calendar and shows a short on-screen summary. |
| “Show me my alarms.” | Opens the alarm list on the display (with delete buttons) and tells you how many are scheduled. |

## Timers

| Example phrase | What the assistant does |
| --- | --- |
| “Set a 5 minute timer.” | Starts a five-minute countdown and shows it on the overlay. |
| “Set a 9 minute timer for pasta.” | Starts a timer, labels it “pasta,” and lets you refer to it by name later (“add a minute to the pasta timer”). |
| “Add three minutes to the timer.” | Extends the active (or named) timer and confirms the new duration. |
| “Cancel my timer.” | Stops your most recent or specified timer and lets you know it’s cancelled. |
| “Cancel all timers.” | Stops every running timer and tells you how many were cleared. |
| “Stop the timer/Stop the alarm.” | Silences whichever timer or alarm is currently ringing. |

## Reminders

| Example phrase | What the assistant does |
| --- | --- |
| “Remind me on monday at 8am to take out the trash.” | Schedules a one-time reminder for the next matching Monday at 8:00 AM and confirms it aloud. |
| “Remind me every monday at 8am to take out the trash.” | Creates a weekly reminder with a Monday cadence and shows it under “Show me my reminders.” |
| “Remind me every monday morning to take out the trash.” | Assumes “morning” means 8:00 AM and builds the same weekly reminder as above. |
| “Remind me every month to pay the electric bill.” | Starts a monthly reminder beginning today at 8:00 AM (or the next morning if it’s already past 8). |
| “Remind me every 6 months to replace the HVAC filters.” | Builds a repeating reminder that fires every six months, starting today at 8:00 AM. |
| “Show me my reminders.” | Opens the reminder list overlay with Complete/Delete buttons for each entry. |

Local reminders beep once, display the message on the overlay, and offer on-screen “Complete” or “Remind me in 1 hour / 1 day / 1 week” buttons. When you omit an exact time, the assistant assumes **morning = 8 AM**, **afternoon = 1 PM**, **evening = 5 PM**, and **night = 8 PM** (otherwise it defaults to 8 AM).

## Alarm, timer & reminder overlays

- Tapping “OK” on a ringing timer/alarm posts `/overlay/stop`, which maps to the MQTT `{"action": "stop"}` command.
- Saying “Show me my alarms” keeps the overlay open until you close it or delete alarms via the on-screen × buttons (each button issues `{"action": "delete_alarm", "event_id": "<id>"}` over MQTT).
- Reminder overlays include Complete/+1h/+1d/+1w buttons. Reminder info cards mirror the alarm list so you can delete or complete entries directly from the screen.

## Real-time info (News, Weather, Sports)

| Category | Example phrase | What the assistant does |
| --- | --- | --- |
| Headlines | “What’s the news?” | Plays a short briefing with the latest headlines from your configured news source. |
| Weather | “What’s the weather tomorrow?” / “Will it rain today?” | Reads today’s or tomorrow’s forecast for your configured location. |
| Sports | “What are the NFL standings?” / “When do the Penguins play next?” | Gives the requested standings, scores, or upcoming games for your favorite leagues. |

If any of the above APIs are offline the assistant still replies (and logs the failure) without involving the LLM so wake word requests stay fast.

## Music controls

Requires that your Pulse display is linked to Home Assistant.

- “Pause the music” / “Stop the music” / “Next song” → Pauses, stops, or skips the connected player.
- “What song is this?” / “Who is this?” → Announces the current artist and track pulled from the player’s metadata.

The assistant also auto-pauses the configured player when you say the wake word and resumes playback ~2 seconds after speaking a response.

## Home Assistant action slugs

If you ever need a guaranteed action (without depending on casual phrasing), you can speak a shortcut slug exactly as shown:

```
ha.turn_on:light.kitchen
ha.turn_off:switch.projector
timer.start:duration=10m,label=cookies
reminder.create:when=2025-01-01T09:00,message=Example
```

These appear in the system prompt so the LLM executes them precisely. They’re handy when you want an exact action instead of a conversational request.

## Tips

- Use `bin/tools/verify-conf.py` anytime alarms/timers don’t react; it validates MQTT, Wyoming, and HA connectivity.
- The assistant logs every shortcut it handles in `journalctl -u pulse-assistant.service`. Set `PULSE_ASSISTANT_LOG_LLM="false"` to suppress user transcript excerpts.
- All alarm/timer changes are mirrored over MQTT (`pulse/<hostname>/assistant/schedules/state`) so dashboards or automations stay in sync.

