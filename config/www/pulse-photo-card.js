class PulsePhotoCard extends HTMLElement {
  constructor() {
    super();
    this._frontLayer = 'a';
    this._currentRaw = undefined;
    this._currentUrl = undefined;
    this._pendingLoadId = 0;
    this._clockInterval = null;
    this._timeEl = null;
    this._dateEl = null;
  }

  setConfig(config) {
    if (!config?.entity) {
      throw new Error('Set "entity" in pulse-photo-card config');
    }

    this._config = {
      fade_ms: 1000,
      ...config,
    };

    if (!this.shadowRoot) {
      this.attachShadow({ mode: 'open' });
    }

    this.shadowRoot.innerHTML = `
      <style>
        :host,
        ha-card {
          height: 100vh;
          width: 100vw;
          margin: 0;
          background: #000;
        }

        ha-card {
          position: relative;
          overflow: hidden;
        }

        .frame {
          position: absolute;
          inset: 0;
          background: #000;
        }

        img {
          position: absolute;
          inset: 0;
          width: 100%;
          height: 100%;
          object-fit: cover;
          background: #000;
          opacity: 0;
          transition: opacity var(--fade-ms, 500ms) ease-in-out;
        }

        img.visible {
          opacity: 1;
        }

        .overlay {
          position: absolute;
          inset: 0;
          display: flex;
          align-items: flex-end;
          justify-content: flex-start;
          pointer-events: none;
          padding: 48px;
          background: linear-gradient(
            180deg,
            rgba(0, 0, 0, 0) 0%,
            rgba(0, 0, 0, 0.35) 65%,
            rgba(0, 0, 0, 0.75) 100%
          );
        }

        .clock {
          text-shadow: 0 4px 12px rgba(0, 0, 0, 0.9);
          color: #fff;
          font-family: "Product Sans", "Google Sans", "SF Pro Display", "Roboto", sans-serif;
          line-height: 1.1;
        }

        .clock__time {
          font-size: clamp(3.5rem, 8vw, 6.5rem);
          font-weight: 300;
          letter-spacing: -0.03em;
        }

        .clock__date {
          font-size: clamp(1.3rem, 3vw, 2.2rem);
          font-weight: 400;
          opacity: 0.85;
        }
      </style>

      <ha-card>
        <div class="frame">
          <img class="layer layer-a visible" />
          <img class="layer layer-b" />
        </div>
        <div class="overlay">
          <div class="clock">
            <div class="clock__time">--:--</div>
            <div class="clock__date">Loading…</div>
          </div>
        </div>
      </ha-card>
    `;

    this.style.setProperty('--fade-ms', `${this._config.fade_ms}ms`);
    this._card = this.shadowRoot.querySelector('ha-card');
    this._layers = {
      a: this.shadowRoot.querySelector('.layer-a'),
      b: this.shadowRoot.querySelector('.layer-b'),
    };
    this._timeEl = this.shadowRoot.querySelector('.clock__time');
    this._dateEl = this.shadowRoot.querySelector('.clock__date');
    this._startClock();
  }

  set hass(hass) {
    this._hass = hass;
    this._startClock();
    if (!this._config) {
      return;
    }

    const entity = hass.states?.[this._config.entity];
    if (!entity) {
      return;
    }

    const newRaw = entity.state;
    if (!newRaw || newRaw === 'unknown' || newRaw === 'unavailable') {
      return;
    }

    if (newRaw === this._currentRaw && this._currentUrl) {
      return;
    }

    this._currentRaw = newRaw;
    this._loadNewImage(newRaw);
  }

  async _loadNewImage(rawPath) {
    const loadId = ++this._pendingLoadId;
    const resolvedUrl = await this._resolveUrl(rawPath);

    if (
      !resolvedUrl ||
      loadId !== this._pendingLoadId ||
      resolvedUrl === this._currentUrl
    ) {
      return;
    }

    this._swapImage(resolvedUrl);
  }

  async _resolveUrl(rawPath) {
    if (!rawPath) {
      return null;
    }

    if (rawPath.startsWith('media-source://')) {
      try {
        const resolved = await this._hass.callWS({
          type: 'media_source/resolve_media',
          media_content_id: rawPath,
        });

        if (resolved?.url) {
          return this._hass.hassUrl(resolved.url);
        }
      } catch (err) {
        console.error('pulse-photo-card: failed to resolve media source', err);
        return null;
      }

      return null;
    }

    if (rawPath.startsWith('/')) {
      return this._hass.hassUrl(rawPath);
    }

    if (/^https?:\/\//.test(rawPath)) {
      return rawPath;
    }

    return null;
  }

  _swapImage(url) {
    const current = this._layers[this._frontLayer];
    const nextLayerKey = this._frontLayer === 'a' ? 'b' : 'a';
    const next = this._layers[nextLayerKey];

    next.onload = () => {
      current.classList.remove('visible');
      next.classList.add('visible');
      next.onload = null;
      next.onerror = null;
      this._frontLayer = nextLayerKey;
      this._currentUrl = url;
    };

    next.onerror = (err) => {
      console.error('pulse-photo-card: failed to load image', err);
      next.onerror = null;
      next.onload = null;
    };

    // Force reflow so browser treats same URL as new request
    next.src = '';
    next.src = url;
  }

  getCardSize() {
    return 1;
  }

  disconnectedCallback() {
    if (this._clockInterval) {
      clearInterval(this._clockInterval);
      this._clockInterval = null;
    }
  }

  _startClock() {
    if (!this._timeEl || !this._dateEl || this._clockInterval || !this.isConnected) {
      return;
    }

    this._updateClock();
    this._clockInterval = window.setInterval(() => this._updateClock(), 1000);
  }

  _updateClock() {
    if (!this._timeEl || !this._dateEl) {
      return;
    }

    const locale = this._hass?.locale?.language || navigator.language || 'en-US';
    const tz = this._hass?.config?.time_zone;
    const use12h = this._hass?.locale?.time_format === '12';
    const now = new Date();

    const timeFormatter = new Intl.DateTimeFormat(locale, {
      hour: 'numeric',
      minute: '2-digit',
      hour12: use12h ?? true,
      timeZone: tz,
    });

    const dateFormatter = new Intl.DateTimeFormat(locale, {
      weekday: 'long',
      month: 'long',
      day: 'numeric',
      timeZone: tz,
    });

    this._timeEl.textContent = timeFormatter.format(now);
    this._dateEl.textContent = dateFormatter.format(now);
  }
}

customElements.define('pulse-photo-card', PulsePhotoCard);

