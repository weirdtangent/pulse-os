window.PulseOverlay = window.PulseOverlay || {};
window.PulseOverlay.clockInterval = null;
window.PulseOverlay.eventHandlers = null;
window.PulseOverlay.mutationObserver = null;

// Expose initialization function for use after DOM updates
window.PulseOverlay.initialize = function() {
(function () {
  const root = document.getElementById('pulse-overlay-root');
  if (!root) {
    return;
  }

  // Clean up previous event listeners to prevent duplicates
  if (window.PulseOverlay.clockInterval) {
    clearInterval(window.PulseOverlay.clockInterval);
    window.PulseOverlay.clockInterval = null;
  }

  if (window.PulseOverlay.eventHandlers) {
    const { clickHandler, inputHandler, resizeHandler } = window.PulseOverlay.eventHandlers;
    const oldRoot = window.PulseOverlay.eventHandlers.root;
    if (oldRoot) {
      oldRoot.removeEventListener('click', clickHandler);
      oldRoot.removeEventListener('input', inputHandler);
    }
    if (resizeHandler) {
      window.removeEventListener('resize', resizeHandler);
    }
    window.PulseOverlay.eventHandlers = null;
  }

  if (window.PulseOverlay.mutationObserver) {
    window.PulseOverlay.mutationObserver.disconnect();
    window.PulseOverlay.mutationObserver = null;
  }
  const stopEndpoint = root.dataset.stopEndpoint || '/overlay/stop';
  const timerNodes = root.querySelectorAll('[data-timer]');
  const infoEndpoint = root.dataset.infoEndpoint || '/overlay/info-card';
  let autoDismissTimer = null;
  const AUTO_DISMISS_DELAY = 120000; // 2 minutes in milliseconds
  const pendingDeviceUpdates = {};
  const sizeClassMap = [
    { className: 'overlay-timer__remaining--xlong', active: (len) => len > 8 },
    { className: 'overlay-timer__remaining--long', active: (len) => len > 5 && len <= 8 },
  ];
  const hour12Attr = root.dataset.clockHour12;
  const hour12 = hour12Attr !== 'false';
  const timeOptions = { hour: hour12 ? 'numeric' : '2-digit', minute: '2-digit', hour12 };
  const dateOptions = { weekday: 'long', month: 'long', day: 'numeric' };

  const clampPercent = (value) => {
    const numberValue = Number(value);
    if (!Number.isFinite(numberValue)) {
      return 0;
    }
    return Math.max(0, Math.min(100, Math.round(numberValue)));
  };

  const updateControlDisplay = (kind, value) => {
    if (!Number.isFinite(value)) {
      return;
    }
    const valueNode = root.querySelector(`[data-control-value="${kind}"]`);
    if (valueNode) {
      valueNode.textContent = `${value}%`;
    }
    const slider = root.querySelector(`[data-control-slider="${kind}"]`);
    if (slider) {
      slider.value = value;
    }
  };

  const sendDeviceControl = (kind, value) => {
    const action = kind === 'brightness' ? 'set_brightness' : 'set_volume';
    return fetch(infoEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, value })
    });
  };

  const queueDeviceControl = (kind, value) => {
    const clamped = clampPercent(value);
    updateControlDisplay(kind, clamped);
    if (pendingDeviceUpdates[kind]) {
      clearTimeout(pendingDeviceUpdates[kind]);
    }
    pendingDeviceUpdates[kind] = setTimeout(() => {
      sendDeviceControl(kind, clamped).finally(() => {
        pendingDeviceUpdates[kind] = null;
      });
    }, 150);
  };

  const alignNowPlayingCard = () => {
    const clockCard = root.querySelector('.overlay-card--clock');
    const nowPlayingCard = root.querySelector('.overlay-card--now-playing');
    if (!clockCard || !nowPlayingCard) {
      return;
    }
    const clockCell = clockCard.closest('.overlay-cell');
    const nowPlayingCell = nowPlayingCard.closest('.overlay-cell');
    if (!clockCell || !nowPlayingCell) {
      return;
    }
    const clockRect = clockCard.getBoundingClientRect();
    const clockCellRect = clockCell.getBoundingClientRect();
    const offset = Math.max(0, clockCellRect.bottom - clockRect.bottom);
    nowPlayingCard.style.marginBottom = offset ? `${offset}px` : '';
  };

  const formatWithZone = (date, tz, options) => {
    try {
      return new Intl.DateTimeFormat(undefined, { ...options, timeZone: tz || undefined }).format(date);
    } catch (error) {
      return new Intl.DateTimeFormat(undefined, options).format(date);
    }
  };

  const tick = () => {
    const now = new Date();
    const clockNodes = root.querySelectorAll('[data-clock]');
    clockNodes.forEach((node) => {
      const tz = node.dataset.tz || undefined;
      const timeEl = node.querySelector('[data-clock-time]');
      const dateEl = node.querySelector('[data-clock-date]');
      if (timeEl) {
        try {
          timeEl.textContent = formatWithZone(now, tz, timeOptions);
        } catch (err) {
          // Silently handle timezone formatting errors
        }
      }
      if (dateEl) {
        try {
          dateEl.textContent = formatWithZone(now, tz, dateOptions);
        } catch (err) {
          // Silently handle timezone formatting errors
        }
      }
    });

    const nowMs = now.getTime();
    timerNodes.forEach((node) => {
      const targetMs = Number(node.dataset.targetMs || 0);
      if (!Number.isFinite(targetMs) || targetMs <= 0) {
        return;
      }
      let remaining = Math.max(0, Math.round((targetMs - nowMs) / 1000));
      const hours = Math.floor(remaining / 3600);
      remaining -= hours * 3600;
      const minutes = Math.floor(remaining / 60);
      const seconds = remaining % 60;
      const formatted =
        hours > 0
          ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
          : `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
      const remainingEl = node.querySelector('[data-timer-remaining]');
      if (remainingEl) {
        remainingEl.textContent = formatted;
        const len = formatted.length;
        sizeClassMap.forEach(({ className, active }) => {
          if (active(len)) {
            remainingEl.classList.add(className);
          } else {
            remainingEl.classList.remove(className);
          }
        });
      }
      if (targetMs - nowMs <= 1000) {
        node.classList.add('overlay-card--expired');
        // Add fallback STOP button if not already present
        if (!node.querySelector('[data-stop-timer-fallback]')) {
          const eventId = node.dataset.eventId;
          if (eventId) {
            const remainingEl = node.querySelector('[data-timer-remaining]');
            if (remainingEl) {
              const btn = document.createElement('button');
              btn.className = 'overlay-button overlay-button--primary overlay-timer__stop-fallback';
              btn.dataset.stopTimer = '';
              btn.dataset.stopTimerFallback = '';
              btn.dataset.eventId = eventId;
              btn.textContent = 'Stop';
              remainingEl.replaceWith(btn);
            }
          }
        }
      } else {
        node.classList.remove('overlay-card--expired');
      }
    });
  };

  // Initial tick to set clock immediately
  tick();
  window.PulseOverlay.clockInterval = window.setInterval(tick, 1000);
  alignNowPlayingCard();

  // Store resize handler reference for cleanup
  const resizeHandler = alignNowPlayingCard;
  window.addEventListener('resize', resizeHandler);

  const forwardBlankTapToParent = () => {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ source: 'pulse-overlay', type: 'blank-tap' }, '*');
    }
  };

  const clearAutoDismissTimer = () => {
    if (autoDismissTimer) {
      clearTimeout(autoDismissTimer);
      autoDismissTimer = null;
    }
  };

  const startAutoDismissTimer = () => {
    clearAutoDismissTimer();
    autoDismissTimer = setTimeout(() => {
      const infoCard = root.querySelector('.overlay-info-card');
      if (infoCard) {
        fetch(infoEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'clear' })
        }).catch(() => {});
      }
      autoDismissTimer = null;
    }, AUTO_DISMISS_DELAY);
  };

  const updateScrollIndicators = (scrollableElement) => {
    if (!scrollableElement) {
      return;
    }
    const scrollId = scrollableElement.getAttribute('data-scroll-id');
    if (!scrollId) {
      return;
    }
    const infoCard = scrollableElement.closest('.overlay-info-card');
    if (!infoCard) {
      return;
    }
    const upArrow = infoCard.querySelector(`.overlay-info-card__scroll-indicator--up[data-scroll-target="${scrollId}"]`);
    const downArrow = infoCard.querySelector(`.overlay-info-card__scroll-indicator--down[data-scroll-target="${scrollId}"]`);

    if (!upArrow || !downArrow) {
      return;
    }

    const scrollTop = scrollableElement.scrollTop;
    const scrollHeight = scrollableElement.scrollHeight;
    const clientHeight = scrollableElement.clientHeight;
    const canScrollUp = scrollTop > 0;
    const canScrollDown = scrollTop + clientHeight < scrollHeight - 1; // -1 for floating point precision

    if (canScrollUp) {
      upArrow.classList.remove('overlay-info-card__scroll-indicator--hidden');
    } else {
      upArrow.classList.add('overlay-info-card__scroll-indicator--hidden');
    }

    if (canScrollDown) {
      downArrow.classList.remove('overlay-info-card__scroll-indicator--hidden');
    } else {
      downArrow.classList.add('overlay-info-card__scroll-indicator--hidden');
    }
  };

  const setupScrollIndicators = (infoCard) => {
    if (!infoCard) {
      return;
    }

    // Find scrollable elements
    const scrollableBody = infoCard.querySelector('.overlay-info-card__body');
    const scrollableText = infoCard.querySelector('.overlay-info-card__text');

    const setupForElement = (scrollableElement) => {
      if (!scrollableElement) {
        return;
      }

      // Check if indicators already exist for this element
      const elementId = scrollableElement.getAttribute('data-scroll-id');
      let scrollId = elementId;
      if (!scrollId) {
        scrollId = 'scroll-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
        scrollableElement.setAttribute('data-scroll-id', scrollId);
      }

      const existingUp = infoCard.querySelector(`.overlay-info-card__scroll-indicator--up[data-scroll-target="${scrollId}"]`);
      const existingDown = infoCard.querySelector(`.overlay-info-card__scroll-indicator--down[data-scroll-target="${scrollId}"]`);

      if (existingUp && existingDown) {
        // Already set up, just update
        updateScrollIndicators(scrollableElement);
        return;
      }

      // Ensure parent is a wrapper
      let wrapper = scrollableElement.parentElement;
      const needsWrapper = !wrapper.classList.contains('overlay-info-card__body-wrapper') &&
                           !wrapper.classList.contains('overlay-info-card__text-wrapper');

      if (needsWrapper) {
        // Create wrapper if needed
        wrapper = document.createElement('div');
        if (scrollableElement.classList.contains('overlay-info-card__body')) {
          wrapper.className = 'overlay-info-card__body-wrapper';
        } else {
          wrapper.className = 'overlay-info-card__text-wrapper';
        }
        scrollableElement.parentElement.insertBefore(wrapper, scrollableElement);
        wrapper.appendChild(scrollableElement);
      }

      // Create arrows if they don't exist
      if (!existingUp) {
        const upArrow = document.createElement('div');
        upArrow.className = 'overlay-info-card__scroll-indicator overlay-info-card__scroll-indicator--up overlay-info-card__scroll-indicator--hidden';
        upArrow.setAttribute('data-scroll-target', scrollId);
        upArrow.textContent = '↑';
        upArrow.setAttribute('aria-hidden', 'true');
        wrapper.insertBefore(upArrow, scrollableElement);
      }

      if (!existingDown) {
        const downArrow = document.createElement('div');
        downArrow.className = 'overlay-info-card__scroll-indicator overlay-info-card__scroll-indicator--down overlay-info-card__scroll-indicator--hidden';
        downArrow.setAttribute('data-scroll-target', scrollId);
        downArrow.textContent = '↓';
        downArrow.setAttribute('aria-hidden', 'true');
        wrapper.appendChild(downArrow);
      }

      // Update indicators
      updateScrollIndicators(scrollableElement);

      // Listen for scroll events
      const handleScroll = () => {
        updateScrollIndicators(scrollableElement);
      };
      scrollableElement.addEventListener('scroll', handleScroll);

      // Also check on resize
      const handleResize = () => {
        // Use requestAnimationFrame to ensure layout is complete
        requestAnimationFrame(() => {
          updateScrollIndicators(scrollableElement);
        });
      };
      window.addEventListener('resize', handleResize);
    };

    if (scrollableBody) {
      setupForElement(scrollableBody);
    }
    if (scrollableText) {
      setupForElement(scrollableText);
    }
  };

  // Watch for info card appearance
  const infoCardObserver = new MutationObserver(() => {
    const infoCard = root.querySelector('.overlay-info-card');
    if (infoCard) {
      startAutoDismissTimer();
      // Setup scroll indicators after a brief delay to ensure layout is complete
      setTimeout(() => {
        setupScrollIndicators(infoCard);
      }, 100);
    } else {
      clearAutoDismissTimer();
    }
  });

  infoCardObserver.observe(root, {
    childList: true,
    subtree: true
  });

  // Store observer reference for cleanup on next initialization
  window.PulseOverlay.mutationObserver = infoCardObserver;

  // Check on initial load
  const initialInfoCard = root.querySelector('.overlay-info-card');
  if (initialInfoCard) {
    startAutoDismissTimer();
    setTimeout(() => {
      setupScrollIndicators(initialInfoCard);
    }, 100);
  }

  // Handle stop timer button clicks
  const clickHandler = (e) => {
    const closeCardButton = e.target.closest('[data-info-card-close]');
    if (closeCardButton) {
      clearAutoDismissTimer();
      closeCardButton.disabled = true;
      fetch(infoEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'clear' })
      }).catch(() => {
        closeCardButton.disabled = false;
      });
      return;
    }

    // Clear auto-dismiss timer on any click within info card
    const infoCard = root.querySelector('.overlay-info-card');
    if (infoCard && infoCard.contains(e.target)) {
      clearAutoDismissTimer();
      startAutoDismissTimer();
    }

    const badgeButton = e.target.closest('[data-badge-action]');
    if (badgeButton) {
      e.preventDefault();
      e.stopPropagation();
      const action = badgeButton.dataset.badgeAction;
      const badge = badgeButton;
      badge.style.opacity = '0.7';
      const resetOpacity = () => {
        badge.style.opacity = '';
      };
      if (action === 'show_alarms') {
        fetch(infoEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'show_alarms' })
        }).then(resetOpacity).catch(resetOpacity);
        return;
      } else if (action === 'show_reminders') {
        fetch(infoEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'show_reminders' })
        }).then(resetOpacity).catch(resetOpacity);
        return;
      } else if (action === 'show_calendar') {
        fetch(infoEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'show_calendar' })
        }).then(resetOpacity).catch(resetOpacity);
        return;
      } else if (action === 'show_config') {
        fetch(infoEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'show_config' })
        }).then(resetOpacity).catch(resetOpacity);
        return;
      } else if (action === 'show_sounds') {
        fetch(infoEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'show_sounds' })
        }).then(resetOpacity).catch(resetOpacity);
        return;
      } else if (action === 'toggle_earmuffs') {
        fetch(infoEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'toggle_earmuffs' })
        }).then(resetOpacity).catch(resetOpacity);
        return;
      } else if (action === 'trigger_update') {
        fetch(infoEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'trigger_update' })
        }).then(resetOpacity).catch(resetOpacity);
        return;
      }
    }

    const configButton = e.target.closest('[data-config-action]');
    if (configButton) {
      e.preventDefault();
      e.stopPropagation();
      const action = configButton.dataset.configAction;
      if (!action) {
        return;
      }
      configButton.disabled = true;
      fetch(infoEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action })
      }).finally(() => {
        configButton.disabled = false;
      });
      return;
    }

    const stepButton = e.target.closest('[data-step-control]');
    if (stepButton) {
      e.preventDefault();
      e.stopPropagation();
      const kind = stepButton.dataset.stepControl;
      if (!kind) {
        return;
      }
      const step = Number(stepButton.dataset.step || '0');
      const slider = root.querySelector(`[data-control-slider="${kind}"]`);
      if (!slider || slider.disabled) {
        return;
      }
      const current = clampPercent(slider.value || 0);
      const next = clampPercent(current + step);
      slider.value = next;
      queueDeviceControl(kind, next);
      return;
    }

    const playButton = e.target.closest('[data-play-sound]');
    if (playButton) {
      e.preventDefault();
      e.stopPropagation();
      const mode = playButton.dataset.playSound || 'once';
      const soundId = playButton.dataset.soundId;
      const soundKind = playButton.dataset.soundKind || 'alarm';
      playButton.disabled = true;
      fetch(infoEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'play_sound', sound_id: soundId, mode, kind: soundKind })
      }).finally(() => {
        playButton.disabled = false;
      });
      return;
    }

    const deleteAlarmButton = e.target.closest('[data-delete-alarm]');
    if (deleteAlarmButton) {
      const alarmId = deleteAlarmButton.dataset.deleteAlarm;
      if (!alarmId) {
        return;
      }
      const previous = deleteAlarmButton.textContent;
      deleteAlarmButton.disabled = true;
      deleteAlarmButton.textContent = '…';
      fetch(infoEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete_alarm', event_id: alarmId })
      }).catch(() => {
        deleteAlarmButton.disabled = false;
        deleteAlarmButton.textContent = previous;
      });
      return;
    }

    const toggleAlarmButton = e.target.closest('[data-toggle-alarm]');
    if (toggleAlarmButton) {
      const eventId = toggleAlarmButton.dataset.eventId;
      const toggleAction = toggleAlarmButton.dataset.toggleAlarm || 'pause';
      if (!eventId) {
        return;
      }
      const previous = toggleAlarmButton.textContent;
      toggleAlarmButton.disabled = true;
      toggleAlarmButton.textContent = '…';
      const command = toggleAction === 'resume' ? 'resume_alarm' : 'pause_alarm';
      fetch(infoEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: command, event_id: eventId })
      }).catch(() => {
        toggleAlarmButton.disabled = false;
        toggleAlarmButton.textContent = previous;
      });
      return;
    }

    const toggleDayButton = e.target.closest('[data-toggle-pause-day]');
    if (toggleDayButton) {
      const date = toggleDayButton.dataset.date;
      const paused = toggleDayButton.dataset.paused === 'true';
      if (!date) {
        return;
      }
      const previous = toggleDayButton.textContent;
      toggleDayButton.disabled = true;
      toggleDayButton.textContent = '…';
      const action = paused ? 'resume_day' : 'pause_day';
      fetch(infoEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, date })
      }).catch(() => {
        toggleDayButton.disabled = false;
        toggleDayButton.textContent = previous;
      });
      return;
    }

    // Handle enable/disable day clicks (for paused alarms)
    const toggleEnableDayButton = e.target.closest('[data-toggle-enable-day]');
    if (toggleEnableDayButton) {
      const date = toggleEnableDayButton.dataset.date;
      const alarmId = toggleEnableDayButton.dataset.alarmId;
      const paused = toggleEnableDayButton.dataset.paused === 'true';
      if (!date || !alarmId) {
        return;
      }
      const previous = toggleEnableDayButton.textContent;
      toggleEnableDayButton.disabled = true;
      toggleEnableDayButton.textContent = '…';
      const action = paused ? 'enable_day' : 'disable_day';
      fetch(infoEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, date, alarm_id: alarmId })
      }).catch(() => {
        toggleEnableDayButton.disabled = false;
        toggleEnableDayButton.textContent = previous;
      });
      return;
    }

    const deleteReminderButton = e.target.closest('[data-delete-reminder]');
    if (deleteReminderButton) {
      const reminderId = deleteReminderButton.dataset.deleteReminder;
      if (!reminderId) {
        return;
      }
      const previous = deleteReminderButton.textContent;
      deleteReminderButton.disabled = true;
      deleteReminderButton.textContent = '…';
      fetch(infoEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete_reminder', event_id: reminderId })
      }).catch(() => {
        deleteReminderButton.disabled = false;
        deleteReminderButton.textContent = previous;
      });
      return;
    }

    const completeReminderButton = e.target.closest('[data-complete-reminder]');
    if (completeReminderButton) {
      const eventId = completeReminderButton.dataset.eventId;
      if (!eventId) {
        return;
      }
      completeReminderButton.disabled = true;
      completeReminderButton.textContent = '…';
      fetch(infoEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'complete_reminder', event_id: eventId })
      }).catch(() => {
        completeReminderButton.disabled = false;
        completeReminderButton.textContent = 'Complete';
      });
      return;
    }

    const delayReminderButton = e.target.closest('[data-delay-reminder]');
    if (delayReminderButton) {
      const eventId = delayReminderButton.dataset.eventId;
      const seconds = Number(delayReminderButton.dataset.delaySeconds || '0');
      if (!eventId || !Number.isFinite(seconds) || seconds <= 0) {
        return;
      }
      delayReminderButton.disabled = true;
      fetch(infoEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delay_reminder', event_id: eventId, seconds })
      }).catch(() => {
        delayReminderButton.disabled = false;
      });
      return;
    }

    const snoozeButton = e.target.closest('[data-snooze-alarm]');
    if (snoozeButton) {
      const eventId = snoozeButton.dataset.eventId;
      if (!eventId) {
        return;
      }
      let minutes = Number(snoozeButton.dataset.snoozeMinutes || '5');
      if (!Number.isFinite(minutes) || minutes <= 0) {
        minutes = 5;
      }
      minutes = Math.max(1, Math.round(minutes));
      const previous = snoozeButton.textContent;
      snoozeButton.disabled = true;
      snoozeButton.textContent = 'Snoozing...';
      fetch(stopEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'snooze', event_id: eventId, minutes })
      }).catch(() => {
        snoozeButton.disabled = false;
        snoozeButton.textContent = previous;
      });
      return;
    }

    // Block background navigation when tapping inside the info card or notification bar
    const infoCardElement = root.querySelector('.overlay-info-card');
    if (infoCardElement && infoCardElement.contains(e.target)) {
      return;
    }
    const notificationBar = root.querySelector('.overlay-notification-bar');
    if (notificationBar && notificationBar.contains(e.target)) {
      return;
    }

    const button = e.target.closest('[data-stop-timer]');
    if (!button) {
      forwardBlankTapToParent();
      return;
    }
    const eventId = button.dataset.eventId;
    if (!eventId) {
      return;
    }
    // Icon-only buttons (cancel X) have no meaningful textContent - just disable them
    const isIconButton = button.classList.contains('overlay-timer__cancel');
    const previous = button.textContent;
    button.disabled = true;
    if (!isIconButton) {
      button.textContent = 'Stopping...';
    }
    fetch(stopEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'stop', event_id: eventId })
    }).catch(() => {
      button.disabled = false;
      if (!isIconButton) {
        button.textContent = previous || 'Stop';
      }
    });
  };

  const inputHandler = (e) => {
    const slider = e.target.closest('[data-control-slider]');
    if (!slider || slider.disabled) {
      return;
    }
    const kind = slider.dataset.controlSlider;
    if (!kind) {
      return;
    }
    const value = clampPercent(slider.value);
    clearAutoDismissTimer();
    startAutoDismissTimer();
    queueDeviceControl(kind, value);
  };

  // Attach event listeners
  root.addEventListener('click', clickHandler);
  root.addEventListener('input', inputHandler);

  // Store handler references for cleanup on next initialization
  window.PulseOverlay.eventHandlers = {
    root,
    clickHandler,
    inputHandler,
    resizeHandler
  };
})();
};

// Initialize on first load
window.PulseOverlay.initialize();

