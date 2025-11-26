# Home Assistant Photo Frame Guide

Use this guide to land a Pulse kiosk on a Lovelace “Pulse Photo Frame” dashboard that fades between random pictures, shows the local date/time overlay, and gracefully handles network hiccups.

---

## 1. Place photos where Home Assistant can serve them

1. On your Home Assistant host, expose the slideshow folder inside HA’s media tree. Easiest options:
   - Upload/copy the files directly under `config/media/Photos/Favorites`.
   - Or mount external storage via **Settings → System → Storage** (SMB/NFS, USB drive, etc.) and create a symlink inside `config/media` that points to that mount. HA only needs the final path (e.g., `config/media/Photos/Favorites`) to exist.
2. Confirm you can browse that folder via **Media Browser → Local Media**. If the images show there, HA will expose them at `/media/local/Photos/Favorites/...`.

---

## 2. Create helper sensors

Add these to `configuration.yaml` (or split packages) so HA randomly picks a photo and converts it into a media-source URL the kiosk can load.

```yaml
command_line:
  - sensor:
      name: Pulse Current Photo
      command: >
        find /media/Photos/Favorites -type f \( -iname '*.jpg' -o -iname '*.png' \) |
        shuf -n 1
      scan_interval: 60

template:
  - sensor:
      - name: Pulse Current Photo URL
        state: >
          {% set f = states('sensor.pulse_current_photo') %}
          {{ 'media-source://media_source/local' + f[6:] if f.startswith('/media') else f }}
```

- `scan_interval` controls how often the slideshow advances (seconds).
- The template converts `/media/...` paths into `media-source://media_source/local/...`, which let HA generate signed URLs for authenticated access.

Reload Template Entities (Developer Tools → YAML) or restart HA to register the sensors.

---

## 3. Install the custom Pulse photo card

### Option A: Install via HACS (Recommended)

1. In Home Assistant, go to **HACS → Frontend** → **+ Explore & Download Repositories**
2. Search for "Pulse Photo Card" or add this repository as a custom repository:
   - Repository: `https://github.com/weirdtangent/pulse-photo-card`
   - Category: `Plugin` (Lovelace card)
3. Click **Download** and restart Home Assistant
4. The card will be automatically registered as a resource

### Option B: Manual Installation

1. Download `dist/pulse-photo-card.js` from the [pulse-photo-card repository](https://github.com/weirdtangent/pulse-photo-card)
2. Copy it to your Home Assistant `config/www/` directory
3. In Home Assistant, go to **Settings → Dashboards → ⋮ → Resources → + Add Resource**:
   - URL: `/local/pulse-photo-card.js?v=1`
   - Resource type: `JavaScript Module`
4. Enable Advanced Mode in your HA profile (needed for the Resources menu if it's hidden).
5. When you update the card in the future, bump the `?v=` query or click "Reload resources" to bust the cache.

---

## 4. Build the Lovelace view

Create or edit a dashboard and add a panel view that uses the custom card:

```yaml
views:
  - title: Pulse Photo Frame
    path: photo-frame
    panel: true
    theme: midnight
    cards:
      - type: custom:pulse-photo-card
        entity: sensor.pulse_current_photo_url
        fade_ms: 1200          # optional, default 500
        now_playing_entity: auto   # optional (maps to sensor.<pulse_host>_now_playing)
```

Options:
- `fade_ms` sets the cross-fade length in milliseconds.
- `now_playing_entity` (optional) mirrors any Home Assistant `media_player` (Music Assistant, Snapcast, Sonos, etc.) or sensor that exposes artist/title text. When the entity reports `playing`, a Now Playing badge animates in above the clock. Set it to `auto` to follow `sensor.<pulse_host>_now_playing`, which PulseOS publishes per kiosk.
- The overlay clock automatically follows HA's locale/time zone (12h/24h).
- Because the card double-buffers images, it never shows a white flash between photos—even on slow networks.

Hard-refresh the dashboard (Cmd/Ctrl + Shift + R) after saving to ensure the browser loads the latest card code.

---

## 5. PulseOS overlay endpoint

The kiosk renders the clock/timer/notification overlay itself and serves it at `http://<pulse-host>:8800/overlay`. Instead of duplicating that logic in the card, you can embed the returned HTML (complete with inline CSS + JS) directly on top of the slideshow:

1. Make sure `PULSE_OVERLAY_ENABLED="true"` (default) and that Home Assistant can reach TCP port `8800` on the kiosk. Adjust `PULSE_OVERLAY_BIND`, `PULSE_OVERLAY_PORT`, and `PULSE_OVERLAY_ALLOWED_ORIGINS` if you need to lock it down.
2. Subscribe to `pulse/<hostname>/overlay/refresh`. Anytime the kiosk clocks, timers, alarms, now playing text, or notification bar changes, it publishes a tiny JSON hint (`{"version":12,"reason":"timers","ts":...}`). Treat the version as a cache key: when it bumps, fetch `/overlay` once. Keep a slow periodic refresh (e.g., every 2 minutes) just in case an MQTT message drops.
3. Inject the returned HTML into the overlay layer of `pulse-photo-card`. The markup already includes JS to keep the clock/timers ticking locally and uses CSS grid slots so urgent cards (timers/alarms) shade the center of the screen while the clock stays transparent in the bottom-left corner. If the fetch fails, immediately fall back to the card's built-in lower-left clock so the user always sees the local time.
4. (Optional) Set `PULSE_OVERLAY_CLOCK_24H="true"` if you prefer a 24‑hour clock; otherwise the overlay renders in 12‑hour format to match the original card.

You can customize the layout colors and clock label per kiosk via the `PULSE_OVERLAY_*` knobs in `pulse.conf`. A sample clock configuration might look like:

```
PULSE_OVERLAY_CLOCK="local=💓 Bedroom"
```

Or for a different timezone:

```
PULSE_OVERLAY_CLOCK="America/Chicago=💓 Office"
```

The clock appears in the bottom-left corner. Timers/alarms automatically occupy the center slots with darker translucent backgrounds so they're easy to spot. The optional top notification bar shows icons for “alarm scheduled”, “timer running”, and “Now Playing”.

---

## 6. Troubleshooting

- **Black screen** → the helper returned a path HA can’t serve. Verify `sensor.pulse_current_photo_url` looks like `media-source://media_source/local/...`.
- **401 Unauthorized in console** → you’re hitting `/local/...` or added your own query parameters. Let the card resolve the media-source path; don’t append cache busters, the signed `authSig` already handles caching.
- **Still using old JS** → bump the resource version (`/local/pulse-photo-card.js?v=2`) or use Advanced Mode → Resources → Reload.
- **Want even more flair?** The card CSS lives at the top of `pulse-photo-card.js`. Tweak fonts, overlay gradients, or add weather widgets there. See the [pulse-photo-card repository](https://github.com/weirdtangent/pulse-photo-card) for the source code.

