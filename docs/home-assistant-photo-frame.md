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

1. Copy `config/www/pulse-photo-card.js` from this repo into your HA config:

   ```bash
   # On your workstation
   scp /opt/pulse-os/config/www/pulse-photo-card.js homeassistant:/config/www/
   ```

2. In Home Assistant, go to **Settings → Dashboards → ⋮ → Resources → + Add Resource**:
   - URL: `/local/pulse-photo-card.js?v=1`
   - Resource type: `JavaScript Module`

3. Enable Advanced Mode in your HA profile (needed for the Resources menu if it’s hidden).
4. When you update the card in the future, bump the `?v=` query or click “Reload resources” to bust the cache.

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
        secondary_urls:        # optional, array of URLs to cycle through on tap
          - /dashboard-pulse/0
          - /dashboard-weather
        tap_action:            # ignored if secondary_urls is set
          action: navigate
          navigation_path: /dashboard-pulse/0
        hold_action:
          action: none
```

Options:
- `fade_ms` sets the cross-fade length in milliseconds.
- `secondary_urls` (optional) is an array of navigation paths. When set, tapping anywhere on **any dashboard** (not just the photo frame) cycles through these URLs and back to the home screen. Each tap advances to the next URL in the array, wrapping back to home after the last one. This works globally across all dashboards, so you can tap on `/dashboard-pulse/0` to return to the photo frame, even if that dashboard doesn't have the pulse-photo-card.
- The overlay clock automatically follows HA's locale/time zone (12h/24h).
- Because the card double-buffers images, it never shows a white flash between photos—even on slow networks.

**Note:** The global tap handler intelligently skips interactive elements (buttons, links, inputs, etc.) so it won't interfere with normal dashboard interactions. It only handles taps on empty areas of the dashboard.

Hard-refresh the dashboard (Cmd/Ctrl + Shift + R) after saving to ensure the browser loads the latest card code.

---

## 5. Troubleshooting

- **Black screen** → the helper returned a path HA can’t serve. Verify `sensor.pulse_current_photo_url` looks like `media-source://media_source/local/...`.
- **401 Unauthorized in console** → you’re hitting `/local/...` or added your own query parameters. Let the card resolve the media-source path; don’t append cache busters, the signed `authSig` already handles caching.
- **Still using old JS** → bump the resource version (`/local/pulse-photo-card.js?v=2`) or use Advanced Mode → Resources → Reload.
- **Want even more flair?** The card CSS lives at the top of `pulse-photo-card.js`. Tweak fonts, overlay gradients, or add weather widgets there.

