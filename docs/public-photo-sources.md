# Public Photo Collections for Pulse Photo Card

If you'd rather use public images instead of personal favorites, the public-domain and CC0 collections below give `pulse-photo-card` attractive defaults while staying inside open-license boundaries.

---

> **What is CC0?** Creative Commons Zero is a public-domain dedication that lets you use the work without attribution or license worries, similar to U.S. government public-domain releases.

## Quick Reference

| Collection | License | Browse/API root | Highlights |
| --- | --- | --- | --- |
| NASA Image and Video Library | U.S. public domain | https://images.nasa.gov/#/ | Space, Earth observation, rockets, ISS |
| NASA Astronomy Picture of the Day (APOD) | U.S. public domain | https://api.nasa.gov/planetary/apod | Daily hero shot with metadata; requires free API key |
| Smithsonian Open Access | CC0 | https://api.si.edu/openaccess/api/v1.0/ | 4M+ museum objects; art, natural history, space |
| The Metropolitan Museum of Art (Open Access) | CC0 | https://collectionapi.metmuseum.org/public/collection/v1/ | High-res fine art, strong metadata |
| Art Institute of Chicago API | CC0 | https://api.artic.edu/docs/ | Contemporary + classic art with IIIF-ready images |
| Wikimedia Commons (public-domain subset) | Mixed, filter to CC0/Public Domain | https://commons.wikimedia.org/wiki/Special:ApiSandbox | Global subjects, structured data via Wikidata |

---

## Wiring Workflow

1. **Pick a collection** & decide on the theme (e.g., “NASA auroras”, “Met impressionism”).
2. **Create a Home Assistant data source** (REST sensor, command line sensor, or automation) that fetches JSON, stores the direct image URL, title, and attribution.
3. **Template to a photo entity** whose state is the image URL (HTTPS or `media-source://`); add attributes for `title`, `source`, etc. so the overlay or other UI elements can display credits.
4. **Reference that entity in `custom:pulse-photo-card`** as the `entity`. The card already handles caching and cross-fades; give it any reachable URL.
5. Optional: expose multiple feeds (`entity`, `secondary_urls`, or separate dashboards) so users can toggle between “My Photos” and “Public Collections.”

---

## Source Playbooks

### NASA Image and Video Library (space playlists)

Create an `input_text` helper named `input_text.pulse_nasa_topic` (Settings → Devices & Services → Helpers) if you want to live-switch the search keyword from the UI; otherwise replace the helper reference with a hard-coded string in the template below. The same pattern applies to other helpers such as `input_text.pulse_smithsonian_topic`.

- **API**: `https://images-api.nasa.gov/search?q=<topic>&media_type=image`
- **Auth**: none
- **Tip**: build a short list of topics (e.g., aurora, nebula, rocket) and randomize pages to avoid duplicates.

Home Assistant example:

```
rest:
  - resource_template: >
      https://images-api.nasa.gov/search?q={{ states('input_text.pulse_nasa_topic') | default('nebula') | urlencode }}&media_type=image&page={{ range(1,5) | random }}
    headers:
      User-Agent: PulsePhotoCard/1.0 (Home Assistant)
    scan_interval: 900
    sensor:
      - name: pulse_nasa_photo_raw
        value_template: "{{ value_json.collection.version }}"
        json_attributes_path: "$.collection"
        json_attributes:
          - items

template:
  - sensor:
      - name: pulse_nasa_photo
        state: >
          {% set items = state_attr('sensor.pulse_nasa_photo_raw', 'items') or [] %}
          {% set pick = items | random if items else None %}
          {{ pick.links[0].href if pick else 'unknown' }}
        attributes:
          title: "{{ pick.data[0].title if pick else '' }}"
          photographer: "{{ pick.data[0].photographer | default('NASA') if pick else '' }}"
```

Keep the `resource_template` on one line (CloudFront rejects requests containing stray whitespace, resulting in 403 errors) and use `urlencode` so helper values like “deep space” generate valid query strings. The custom `User-Agent` header helps avoid generic bot filtering.

Point the photo card at `sensor.pulse_nasa_photo`:

```
cards:
  - type: custom:pulse-photo-card
    entity: sensor.pulse_nasa_photo
    fade_ms: 1200
    now_playing_entity: auto
```

### NASA APOD (single hero image)

- **API**: `https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY&thumbs=true`
- **Auth**: free API key (use `DEMO_KEY` for low-volume tests).
- **Flow**: fetch once per day; APOD already returns one curated image.

```
rest:
  - resource: https://api.nasa.gov/planetary/apod?api_key=YOUR_KEY&thumbs=true
    headers:
      User-Agent: PulsePhotoCard/1.0 (Home Assistant)
    scan_interval: 21600   # 6 hours
    sensor:
      - name: pulse_nasa_apod
        value_template: "{{ value_json.url }}"
        json_attributes:
          - title
          - explanation

template:
  - sensor:
      - name: pulse_nasa_apod_caption
        state: "{{ state_attr('sensor.pulse_nasa_apod', 'title') }}"
```

### Smithsonian Open Access (museum + STEM)

- **API**: `https://api.si.edu/openaccess/api/v1.0/search?q=<topic>`
- **Auth**: API key required (request one at https://www.si.edu/openaccess/devtools).
- **Tip**: filter to media type `Images` and `online_media_type:"Images"` to guarantee downloadable files.

Steps:

1. Request a key via the Developer Tools form (you’ll get an email immediately).
2. Use the search endpoint to retrieve a batch; read `content.descriptiveNonRepeating.online_media.media[0].content` for the direct file.
3. Cache the image on HA’s `www/` if you’d like deterministic URLs (optional).

Example automation snippet (partial):

```
rest:
  - resource_template: >
      https://api.si.edu/openaccess/api/v1.0/search?q={{ states('input_text.pulse_smithsonian_topic') | default('space') | urlencode }}&media_type=Images&api_key=YOUR_KEY
    headers:
      User-Agent: PulsePhotoCard/1.0 (Home Assistant)
    scan_interval: 1800
    sensor:
      - name: pulse_smithsonian_photo
        value_template: >
          {% set rows = value_json.response.rows %}
          {% set pick = rows | random if rows else None %}
          {{ pick.content.descriptiveNonRepeating.online_media.media[0].content if pick else 'unknown' }}
        json_attributes:
          - response
```

### The Metropolitan Museum of Art (Open Access)

- **API**: two-step flow
  1. Search for object IDs: `https://collectionapi.metmuseum.org/public/collection/v1/search?hasImages=true&q=<topic>`
  2. Fetch object details: `https://collectionapi.metmuseum.org/public/collection/v1/objects/<objectID>`
- **Auth**: none
- **Tip**: store a small curated list of object IDs you like to avoid re-querying search on every update.

Example sequence:

```
rest:
  - resource: https://collectionapi.metmuseum.org/public/collection/v1/objects/436121
    headers:
      User-Agent: PulsePhotoCard/1.0 (Home Assistant)
    scan_interval: 7200
    sensor:
      - name: pulse_met_photo
        value_template: "{{ value_json.primaryImage or value_json.primaryImageSmall }}"
        json_attributes:
          - title
          - artistDisplayName
          - objectDate
```

Keep a helper `input_select.pulse_met_playlist` with favorite object IDs and swap the `resource` via an automation that updates an `input_text` + `homeassistant.update_entity`.

### Art Institute of Chicago

- **API**: `https://api.artic.edu/api/v1/artworks/search?q=<topic>&fields=id,title,image_id`
- **Image URL**: `https://www.artic.edu/iiif/2/<image_id>/full/843,/0/default.jpg`
- **Auth**: none

```
rest:
  - resource: https://api.artic.edu/api/v1/artworks/search?q=landscape&fields=id,title,image_id&limit=50
    headers:
      User-Agent: PulsePhotoCard/1.0 (Home Assistant)
    scan_interval: 3600
    sensor:
      - name: pulse_aic_photo
        value_template: >
          {% set data = value_json.data %}
          {% set pick = data | random if data else None %}
          {% set image_id = pick.image_id if pick else '' %}
          {{ 'https://www.artic.edu/iiif/2/' ~ image_id ~ '/full/843,/0/default.jpg' if image_id else 'unknown' }}
        json_attributes:
          - data
```

### Wikimedia Commons (public-domain filter)

- **API**: `https://commons.wikimedia.org/w/api.php`, combine with Wikidata queries to ensure license.
- **Tip**: pre-build a list of file titles via SPARQL (e.g., “Featured pictures of nature”) and store it in a HA helper. Use the MediaWiki API to resolve the raw image URL via `action=query&titles=File:...&prop=imageinfo&iiprop=url`.

Outline:

1. Use https://query.wikidata.org/ with a SPARQL such as “all public-domain featured pictures of nebulas” and export the resulting file titles.
2. Create an `input_select.pulse_commons_playlist` with those titles.
3. Command line sensor:
   ```
   command_line:
     - sensor:
         name: pulse_commons_photo
         command: >
           python3 - <<'PY'
           import json, random, requests
           titles = {{ states('input_text.pulse_commons_titles') }}
           # pick random title and fetch image URL
           PY
   ```
4. Feed the resulting HTTPS URL into the card.

---

## Surfacing the Feeds in the Photo Card

Add a tab or toggle to your Lovelace dashboard so kiosks can switch between private libraries and public sources. One pattern is to use an `input_select` to pick the active feed and a template sensor to proxy the correct URL:

```
input_select:
  pulse_photo_feed:
    options:
      - My Library
      - NASA Public
      - Met Classics
    icon: mdi:image-multiple

template:
  - sensor:
      - name: pulse_active_photo_feed
        state: >
          {% set feed = states('input_select.pulse_photo_feed') %}
          {% if feed == 'NASA Public' %}
            {{ states('sensor.pulse_nasa_photo') }}
          {% elif feed == 'Met Classics' %}
            {{ states('sensor.pulse_met_photo') }}
          {% else %}
            {{ states('sensor.pulse_photo_primary') }}
          {% endif %}

cards:
  - type: custom:pulse-photo-card
    entity: sensor.pulse_active_photo_feed
    fade_ms: 1200
    global_tap_mode: auto
```

Alternative: dedicate an entire dashboard/view per feed and use `secondary_urls` to hop between them. Because every feed is just a Home Assistant entity that returns a URL, you can layer as many curated collections as you like without touching the kiosk firmware.

---

## Attribution & Caching

- Include source/title text in the `sensor` attributes so overlays or other cards can display credits.
- For very slow APIs, cache images on Home Assistant via the `download_file` service or by writing into `config/www/public-playlists/`. Then point your sensor at `/local/public-playlists/<name>.jpg` and let Home Assistant serve it reliably.
- All collections above are CC0 or public domain, but keeping the original attribution visible is good etiquette and helps debugging when users ask, “What am I looking at?”


