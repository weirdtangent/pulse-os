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
    this._homePath = null;
  }

  setConfig(config) {
    if (!config?.entity) {
      throw new Error('Set "entity" in pulse-photo-card config');
    }

    this._config = {
      fade_ms: 1000,
      secondary_urls: [],
      ...config,
    };

    // Ensure secondary_urls is an array
    if (!Array.isArray(this._config.secondary_urls)) {
      this._config.secondary_urls = [];
    }

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
          cursor: ${this._config.secondary_urls.length > 0 ? 'pointer' : 'default'};
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
          font-family: "Noto Sans", Verdana, "DejaVu Sans", sans-serif;
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
    
    // Store the home path (current path when card is initialized)
    this._homePath = window.location.pathname;
    
    // Set up tap handler if secondary URLs are configured
    if (this._config.secondary_urls.length > 0) {
      // Set up local tap handler for the card itself
      this._card.addEventListener('click', (e) => this._handleTap(e));
      
      // Also set up global tap handler that works on any dashboard
      if (window.PulsePhotoCardGlobalTap && window.PulsePhotoCardGlobalTap.setup) {
        window.PulsePhotoCardGlobalTap.setup(this._homePath, this._config.secondary_urls);
      }
      
      // Initialize the current index based on the current URL
      this._initializeUrlIndex();
    }
    
    this._startClock();
  }

  set hass(hass) {
    this._hass = hass;
    this._startClock();
    if (!this._config) {
      return;
    }

    // Re-initialize URL index if secondary URLs are configured
    // This handles cases where the page has navigated and the card needs to update its state
    if (this._config.secondary_urls && this._config.secondary_urls.length > 0 && this._homePath) {
      this._initializeUrlIndex();
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

  _findUrlIndex(path, secondaryUrls, homePath) {
    const normalizedPath = this._normalizePath(path);
    
    if (normalizedPath === this._normalizePath(homePath)) {
      return 0; // Home
    }
    
    const matchingIndex = secondaryUrls.findIndex(url => {
      return this._normalizePath(url) === normalizedPath;
    });
    
    return matchingIndex >= 0 ? matchingIndex + 1 : null;
  }

  _initializeUrlIndex() {
    const currentPath = window.location.pathname;
    const storageKey = `pulse-photo-card-url-index-${this._homePath}`;
    const index = this._findUrlIndex(currentPath, this._config.secondary_urls, this._homePath);
    
    if (index !== null) {
      localStorage.setItem(storageKey, index.toString());
    }
  }

  _handleTap(e) {
    if (!this._config.secondary_urls || this._config.secondary_urls.length === 0) {
      return;
    }

    // Skip tap handling if disable_km parameter is present (used for editing)
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.has('disable_km')) {
      return;
    }

    // Use home path as storage key to maintain state across navigation
    const storageKey = `pulse-photo-card-url-index-${this._homePath}`;
    
    // Get current index from localStorage, default to 0 (home screen)
    let currentIndex = parseInt(localStorage.getItem(storageKey) || '0', 10);
    
    // Increment index and wrap around (0 = home, 1+ = secondary URLs)
    currentIndex = (currentIndex + 1) % (this._config.secondary_urls.length + 1);
    
    // Navigate based on index
    if (currentIndex === 0) {
      // Navigate back to home
      this._navigateToPath(this._homePath);
    } else {
      // Navigate to secondary URL (index - 1 because 0 is home)
      const targetUrl = this._config.secondary_urls[currentIndex - 1];
      this._navigateToPath(targetUrl);
    }
    
    // Store the new index
    localStorage.setItem(storageKey, currentIndex.toString());
  }

  _normalizePath(path) {
    return path.startsWith('/') ? path : `/${path}`;
  }

  _preserveKioskParams(path) {
    const normalizedPath = this._normalizePath(path);
    const currentParams = new URLSearchParams(window.location.search);
    const kioskParams = new URLSearchParams();
    
    // Preserve parameters that control kiosk mode
    if (currentParams.has('sidebar')) {
      kioskParams.set('sidebar', currentParams.get('sidebar'));
    }
    if (currentParams.has('disable_km')) {
      kioskParams.set('disable_km', currentParams.get('disable_km'));
    }
    
    const newSearch = kioskParams.toString();
    return normalizedPath + (newSearch ? '?' + newSearch : '');
  }

  _navigateToPath(path) {
    const newUrl = this._preserveKioskParams(path);
    window.location.href = newUrl;
  }
}

customElements.define('pulse-photo-card', PulsePhotoCard);

// Global tap handler for navigation cycling
// This allows taps to work on any dashboard, not just the photo frame
(function() {
  'use strict';
  
  let globalTapHandler = null;
  
  // Detect if we're running on a Pulse device (kiosk mode)
  // Only enable global tap handler on Pulse, not on desktop browsers
  function isPulseDevice() {
    // Check for explicit disable flag in URL
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.has('pulse_tap_disable')) {
      return false;
    }
    
    // Check if we're in fullscreen/kiosk mode
    // Pulse runs Chromium with --kiosk flag, which puts it in fullscreen
    const isFullscreen = window.innerHeight === screen.height && 
                         window.innerWidth === screen.width &&
                         !window.navigator.standalone; // Exclude iOS standalone mode
    
    // Additional check: if window.chrome exists and we're in a very specific fullscreen state
    // This is a heuristic - Pulse devices run in kiosk mode
    const isLikelyKiosk = isFullscreen && 
                          document.fullscreenElement === null && // Not programmatic fullscreen
                          window.screen.availHeight === window.innerHeight; // Using full screen
    
    return isLikelyKiosk;
  }
  
  // Shared utility functions
  function normalizePath(path) {
    return path.startsWith('/') ? path : `/${path}`;
  }
  
  function preserveKioskParams(path) {
    const normalizedPath = normalizePath(path);
    const currentParams = new URLSearchParams(window.location.search);
    const kioskParams = new URLSearchParams();
    
    if (currentParams.has('sidebar')) {
      kioskParams.set('sidebar', currentParams.get('sidebar'));
    }
    if (currentParams.has('disable_km')) {
      kioskParams.set('disable_km', currentParams.get('disable_km'));
    }
    
    const newSearch = kioskParams.toString();
    return normalizedPath + (newSearch ? '?' + newSearch : '');
  }
  
  function findUrlIndex(path, secondaryUrls, homePath) {
    const normalizedPath = normalizePath(path);
    const normalizedHome = normalizePath(homePath);
    
    if (normalizedPath === normalizedHome) {
      return 0; // Home
    }
    
    const matchingIndex = secondaryUrls.findIndex(url => {
      return normalizePath(url) === normalizedPath;
    });
    
    return matchingIndex >= 0 ? matchingIndex + 1 : null;
  }
  
  function setupGlobalTapHandler(homePath, secondaryUrls) {
    // Only enable on Pulse devices, not on desktop browsers
    if (!isPulseDevice()) {
      // Clear any existing config and handler if we're not on Pulse
      if (globalTapHandler) {
        document.removeEventListener('click', globalTapHandler, true);
        globalTapHandler = null;
      }
      const configKey = `pulse-photo-card-config-${homePath}`;
      localStorage.removeItem(configKey);
      return;
    }
    
    // Remove existing handler if any
    if (globalTapHandler) {
      document.removeEventListener('click', globalTapHandler, true);
      globalTapHandler = null;
    }
    
    if (!secondaryUrls || secondaryUrls.length === 0) {
      // Clear stored config if URLs are removed
      const configKey = `pulse-photo-card-config-${homePath}`;
      localStorage.removeItem(configKey);
      return;
    }
    
    // Store config in localStorage so it persists across page navigations
    const configKey = `pulse-photo-card-config-${homePath}`;
    localStorage.setItem(configKey, JSON.stringify({ homePath, secondaryUrls }));
    
    globalTapHandler = function(e) {
      // Double-check we're on Pulse device (in case detection changes)
      if (!isPulseDevice()) {
        return;
      }
      
      // Load config from localStorage
      const keys = Object.keys(localStorage);
      let config = null;
      for (const key of keys) {
        if (key.startsWith('pulse-photo-card-config-')) {
          try {
            const configStr = localStorage.getItem(key);
            if (configStr) {
              const candidate = JSON.parse(configStr);
              if (candidate.secondaryUrls && candidate.secondaryUrls.length > 0) {
                config = candidate;
                break;
              }
            }
          } catch (err) {
            // Invalid config, continue
          }
        }
      }
      
      if (!config || !config.secondaryUrls || config.secondaryUrls.length === 0) {
        return;
      }
      
      const { homePath: configHomePath, secondaryUrls } = config;
      
      // Skip tap handling if disable_km parameter is present (used for editing)
      const urlParams = new URLSearchParams(window.location.search);
      if (urlParams.has('disable_km')) {
        return;
      }
      
      // Only handle taps on the main content area, not on interactive elements
      const target = e.target;
      if (target.tagName === 'A' || 
          target.tagName === 'BUTTON' || 
          target.tagName === 'INPUT' || 
          target.tagName === 'SELECT' ||
          target.tagName === 'TEXTAREA' ||
          target.closest('ha-button') ||
          target.closest('mwc-button') ||
          target.closest('paper-button') ||
          target.closest('a') ||
          target.closest('button') ||
          target.closest('[role="button"]') ||
          target.closest('ha-card[tabindex]')) {
        return;
      }
      
      // Skip if clicking on cards that have their own tap actions
      if (target.closest('hui-card') && target.closest('hui-card').config?.tap_action) {
        return;
      }
      
      const storageKey = `pulse-photo-card-url-index-${configHomePath}`;
      const currentPath = window.location.pathname;
      
      // Determine current index - check stored value first, then verify against actual path
      let currentIndex = parseInt(localStorage.getItem(storageKey) || '0', 10);
      const actualIndex = findUrlIndex(currentPath, secondaryUrls, configHomePath);
      
      // If stored index doesn't match actual path, use actual index
      if (actualIndex !== null) {
        if (currentIndex !== actualIndex) {
          currentIndex = actualIndex;
        }
      } else if (currentIndex > 0) {
        // Stored index suggests we're on a secondary URL, but path doesn't match
        // This might be a stale state, so don't handle
        return;
      }
      
      // Increment index and wrap around (0 = home, 1+ = secondary URLs)
      currentIndex = (currentIndex + 1) % (secondaryUrls.length + 1);
      
      // Determine target path
      const targetPath = currentIndex === 0 ? configHomePath : secondaryUrls[currentIndex - 1];
      
      // Prevent default behavior and stop propagation
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      
      // Store the new index
      localStorage.setItem(storageKey, currentIndex.toString());
      
      // Navigate with preserved kiosk parameters
      window.location.href = preserveKioskParams(targetPath);
    };
    
    // Use capture phase to catch clicks early, but allow other handlers to work
    document.addEventListener('click', globalTapHandler, true);
  }
  
  // Initialize on page load if we're in cycling mode
  function initializeOnLoad() {
    // Only initialize on Pulse devices
    if (!isPulseDevice()) {
      // Clean up any leftover config from desktop testing
      const keys = Object.keys(localStorage);
      for (const key of keys) {
        if (key.startsWith('pulse-photo-card-config-') || 
            key.startsWith('pulse-photo-card-url-index-')) {
          localStorage.removeItem(key);
        }
      }
      return;
    }
    
    // Check if we have any stored config indicating we're in cycling mode
    // Look for localStorage keys that match the pattern
    const keys = Object.keys(localStorage);
    for (const key of keys) {
      if (key.startsWith('pulse-photo-card-config-')) {
        try {
          const configStr = localStorage.getItem(key);
          if (!configStr) continue;
          
          const config = JSON.parse(configStr);
          const { homePath, secondaryUrls } = config;
          
          if (secondaryUrls && secondaryUrls.length > 0) {
            // Set up the global handler with stored config
            setupGlobalTapHandler(homePath, secondaryUrls);
            break;
          }
        } catch (e) {
          // Invalid config, skip
          console.warn('pulse-photo-card: invalid stored config', e);
        }
      }
    }
  }
  
  // Run initialization when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeOnLoad);
  } else {
    initializeOnLoad();
  }
  
  // Expose setup function for pulse-photo-card to use
  window.PulsePhotoCardGlobalTap = {
    setup: setupGlobalTapHandler,
  };
})();

