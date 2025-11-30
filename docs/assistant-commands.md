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

### Calendar sync (ICS/WebCal)

Set `PULSE_CALENDAR_ICS_URLS` to one or more ICS/WebCal links (Google “secret address,” iCloud shared calendar, work feed, trash pickup schedule, etc.) and every Pulse device with that config will watch the feed locally. Each kiosk polls its own URLs on a short cadence (`PULSE_CALENDAR_REFRESH_MINUTES`, default 5 min). New events are discovered on the next poll—even if you add them later the same day or reboot the device.

- If an event contains ICS `VALARM` blocks, Pulse fires reminders at the exact DISPLAY triggers defined there (multiple alarms are respected).
- If no `VALARM` exists, Pulse defaults to 5 minutes before the event start (or noon the day before for all-day entries).
- Calendar popups reuse the standard reminder tone/MQTT payloads but only show a single **OK** button (no delay options) and auto-dismiss roughly 15 minutes after they appear.
- Say “show me my calendar”, “show calendar events”, or “show my upcoming events” to pop open the cached list on-screen. A dedicated “Calendar events” badge in the overlay’s notification bar does the same thing if you prefer tapping.

Because feeds are stored per-device (there’s no shared server), removing a URL from `pulse.conf` and rerunning `setup.sh` clears those reminders instantly.

## Alarm, timer & reminder overlays

- Tapping “Stop” on a ringing timer or alarm posts `/overlay/stop`, which maps to the MQTT `{"action": "stop"}` command. Snooze sends `{"action": "snooze", "minutes": 5}` for alarms.
- Saying “Show me my alarms” keeps the overlay open until you close it or use the on-screen ⏸️ / ▶️ / 🗑️ buttons. They send `{"action": "pause_alarm"}`, `{"action": "resume_alarm"}`, or `{"action": "delete_alarm"}` (with the alarm `event_id`) over MQTT.
- Reminder overlays include Complete/+1h/+1d/+1w buttons. Reminder info cards mirror the alarm list so you can delete or complete entries directly from the screen.

## Real-time info (News, Weather, Sports)

| Category | Example phrase | What the assistant does |
| --- | --- | --- |
| Headlines | “What’s the news?” | Plays a short briefing with the latest headlines from your configured news source. |
| Weather | “What’s the weather tomorrow?” / “Will it rain today?” | Reads today’s or tomorrow’s forecast for your configured location. |
| Sports | “What are the NFL standings?” / “When do the Penguins play next?” | Gives the requested standings, scores, or upcoming games for your favorite leagues. |

If any of the above APIs are offline the assistant still replies (and logs the failure) without involving the LLM so wake word requests stay fast.

## Shopping list

> **Prerequisites**
>
> - `PULSE_SHOPPING_KEEP_CLIENT_ID`, `PULSE_SHOPPING_KEEP_CLIENT_SECRET`, `PULSE_SHOPPING_KEEP_REFRESH_TOKEN`
> - Optional: `PULSE_SHOPPING_LIST_TITLE` (defaults to “Shopping list”), `PULSE_SHOPPING_KEEP_NOTE_ID` (if you already know the note ID), `PULSE_SHOPPING_COMPOUND_ITEMS` (comma-separated phrases like `maple syrup,corn flour`)

| Example phrase | What the assistant does |
| --- | --- |
| “Add eggs to my shopping list.” | Splits the item name out of your sentence, normalizes it (case/plural insensitive), and appends it to the configured Google Keep checklist. |
| “Add eggs, peanut butter, sugar, waffles, and syrup to my shopping list.” | Handles multi-item commands (comma, “and,” or even space-delimited) and only inserts items that aren’t already active on the list. Previously checked-off items are reactivated. |
| “Remove butter from my shopping list.” | Finds the matching entry and deletes it from the Keep note. If it can’t find a match you’ll hear that the item isn’t on the list. |
| “Erase my shopping list.” / “Clean my shopping list.” / “Start over on my shopping list.” | Clears the entire Keep checklist, letting you start from scratch. |
| “What’s on my shopping list?” / “Show me my shopping list.” | Reads the total/remaining counts aloud and opens a scrollable info card on the overlay with trash-can icons for each entry. |

The on-screen shopping card mirrors the live Google Keep note, supports scrolling when the list grows long, and includes 🗑️ buttons next to each item. Tapping the trash icon sends a real-time `shopping_remove` command, and the overlay refreshes after each change. A **Clear** button appears when there are entries, which posts `shopping_clear` (the same as saying “erase my shopping list”).

### How to obtain the values

1. **Create/choose a Google Cloud project** and enable the **Google Keep API**. (Cloud Console → APIs & Services → Library → search “Keep API” → Enable.)
2. **Create an OAuth “Desktop” client ID** under APIs & Services → Credentials. The downloaded JSON contains `client_id` and `client_secret`; copy those into `PULSE_SHOPPING_KEEP_CLIENT_ID` and `PULSE_SHOPPING_KEEP_CLIENT_SECRET`.
3. **Generate a refresh token** with the installed-app flow:
   - Either run `oauth2l fetch --scope https://www.googleapis.com/auth/keep --credentials credentials.json` or any small Python script that opens the consent screen for the same scope.
   - After you sign in and approve access, the tool prints an `access_token` and `refresh_token`. Paste the refresh token into `PULSE_SHOPPING_KEEP_REFRESH_TOKEN`. (Keep the JSON private; it grants full control over your Keep notes.)
4. **Pick the target Keep note**:
   - If you already have a checklist note, open it at https://keep.google.com → click the note → copy the “NOTE/xxxxxxxxxxxxxxxx” ID from the URL and set `PULSE_SHOPPING_KEEP_NOTE_ID="notes/<that-id>"`.
   - Otherwise leave it blank and the assistant will search for (or create) a note whose title matches `PULSE_SHOPPING_LIST_TITLE`.
5. **Optional parsing tweaks**: set `PULSE_SHOPPING_COMPOUND_ITEMS` to a comma-separated list of phrases (e.g., `maple syrup,corn flour,bacon bits`) when STT tends to smash those words together. The parser treats each phrase as a single item.

Once those values are in `pulse.conf`, rerun `./setup.sh` (or restart `pulse-assistant.service`) so the new credentials are loaded.

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

