"""Static CSS/JS assets for the Pulse overlay renderer."""

from __future__ import annotations

OVERLAY_CSS = """
:root {
  --overlay-text-color: #ffffff;
  --overlay-ambient-bg: rgba(0, 0, 0, 0.32);
  --overlay-alert-bg: rgba(0, 0, 0, 0.65);
  --overlay-accent-color: #88C0D0;
}

html {
  background: transparent !important;
}

body {
  margin: 0;
  width: 100vw;
  height: 100vh;
  background: transparent !important;
  font-family: "Inter", "Segoe UI", "Helvetica Neue", sans-serif, "Noto Color Emoji";
  color: var(--overlay-text-color);
}

.overlay-root {
  width: 100%;
  height: 100%;
  padding: 3vh;
  box-sizing: border-box;
  color: var(--overlay-text-color);
  background: transparent !important;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.overlay-notification-bar {
  display: flex;
  gap: 0.6rem;
  align-items: center;
  font-size: 0.95rem;
  min-height: 2.4rem;
  flex-shrink: 0;
}

.overlay-notification-bar--empty {
  visibility: hidden;
  pointer-events: none;
}

.overlay-badge {
  display: inline-flex;
  gap: 0.35rem;
  align-items: center;
  padding: 0.35rem 0.65rem;
  border-radius: 999px;
  background: var(--overlay-ambient-bg);
  backdrop-filter: blur(12px);
  cursor: default;
  user-select: none;
  transition: background 0.2s ease, transform 0.2s ease;
}

.overlay-badge[role="button"] {
  cursor: pointer;
}

.overlay-badge[role="button"]:hover {
  background: rgba(255, 255, 255, 0.2);
  transform: translateY(-1px);
}

.overlay-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  grid-template-rows: repeat(3, minmax(0, 1fr));
  grid-template-areas:
    "top-left top-center top-right"
    "middle-left center middle-right"
    "bottom-left bottom-center bottom-right";
  gap: 2vh;
  width: 100%;
  height: auto;
  flex: 1 1 auto;
  min-height: 0;
}

.overlay-info-card {
  grid-column: 2 / 4;
  grid-row: 1 / 4;
  background: var(--overlay-ambient-bg);
  backdrop-filter: blur(18px);
  border-radius: 1.5rem;
  padding: 2.5rem;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  gap: 1.2rem;
  box-shadow: 0 1.5rem 3rem rgba(0, 0, 0, 0.45);
}

.overlay-info-card__title {
  font-size: 1rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--overlay-accent-color);
  opacity: 0.9;
}

.overlay-info-card__text {
  font-size: clamp(1.6rem, 2.4vw, 2.8rem);
  line-height: 1.45;
  font-weight: 400;
  white-space: pre-line;
  overflow-y: auto;
  padding-right: 1rem;
  scrollbar-width: thin;
  scrollbar-color: var(--overlay-accent-color) transparent;
}

.overlay-info-card__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
}

.overlay-info-card__subtitle {
  font-size: 0.95rem;
  opacity: 0.85;
}

.overlay-info-card__close {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.4);
  color: inherit;
  border-radius: 999px;
  width: 2.2rem;
  height: 2.2rem;
  font-size: 1.2rem;
  cursor: pointer;
}

.overlay-info-card__close:hover {
  background: rgba(255, 255, 255, 0.15);
}

.overlay-info-card__body {
  width: 100%;
}

.overlay-info-card__alarm-list {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  margin-top: 1.2rem;
}

.overlay-info-card__alarm {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.7rem 0.2rem;
}

.overlay-info-card__alarm-body {
  flex: 1 1 auto;
}

.overlay-info-card__alarm-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.overlay-info-card__alarm-label {
  font-size: 1rem;
  font-weight: 600;
}

.overlay-info-card__alarm-meta {
  font-size: 0.85rem;
  opacity: 0.8;
}

.overlay-info-card__alarm-status {
  display: inline-block;
  margin-left: 0.4rem;
  padding: 0.1rem 0.6rem;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.3);
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.overlay-info-card__alarm-delete {
  border: none;
  background: rgba(255, 59, 48, 0.25);
  color: #fff;
  width: 2.4rem;
  height: 2.4rem;
  border-radius: 0.8rem;
  font-size: 1.25rem;
  cursor: pointer;
  transition: background 0.2s ease, transform 0.2s ease;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.overlay-info-card__alarm-toggle {
  border: none;
  width: 2.4rem;
  height: 2.4rem;
  border-radius: 0.8rem;
  font-size: 1.25rem;
  cursor: pointer;
  color: #fff;
  background: rgba(255, 214, 10, 0.25);
  transition: background 0.2s ease, transform 0.2s ease;
}

.overlay-info-card__alarm-toggle[data-toggle-alarm="resume"] {
  background: rgba(52, 199, 89, 0.3);
}

.overlay-info-card__alarm-delete:hover,
.overlay-info-card__alarm-toggle:hover {
  transform: translateY(-1px);
}

.overlay-info-card__alarm-delete:hover {
  background: rgba(255, 82, 69, 0.35);
}

.overlay-info-card__alarm-toggle:hover[data-toggle-alarm="resume"] {
  background: rgba(52, 199, 89, 0.45);
}

.overlay-info-card__alarm-toggle:hover[data-toggle-alarm="pause"] {
  background: rgba(255, 214, 10, 0.4);
}

.overlay-info-card__reminder {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.7rem 0.2rem;
}

.overlay-info-card__reminder-body {
  flex: 1 1 auto;
}

.overlay-info-card__reminder-label {
  font-size: 1.2rem;
  font-weight: 600;
}

.overlay-info-card__reminder-meta {
  font-size: 0.95rem;
  opacity: 0.8;
}

.overlay-info-card__reminder-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.overlay-info-card__empty {
  margin-top: 1rem;
  font-size: 1.1rem;
  opacity: 0.85;
}

.overlay-info-card__text strong {
  font-weight: 600;
}

.overlay-info-card__text em {
  font-style: italic;
}

.overlay-info-card__text::-webkit-scrollbar {
  width: 12px;
}

.overlay-info-card__text::-webkit-scrollbar-track {
  background: transparent;
}

.overlay-info-card__text::-webkit-scrollbar-thumb {
  background-color: var(--overlay-accent-color);
  border-radius: 999px;
  border: 3px solid transparent;
  background-clip: content-box;
}

.overlay-cell {
  display: flex;
  flex-direction: column;
  gap: 1.2vh;
}

.cell-top-left { grid-area: top-left; }
.cell-top-center { grid-area: top-center; }
.cell-top-right { grid-area: top-right; }
.cell-middle-left { grid-area: middle-left; }
.cell-center { grid-area: center; }
.cell-middle-right { grid-area: middle-right; }
.cell-bottom-left { grid-area: bottom-left; }
.cell-bottom-center { grid-area: bottom-center; }
.cell-bottom-right { grid-area: bottom-right; }

.overlay-card {
  padding: 1rem 1.2rem;
  border-radius: 1.2rem;
  backdrop-filter: blur(14px);
  color: inherit;
  box-shadow: 0 0.6rem 1.8rem rgba(0, 0, 0, 0.35);
}

.overlay-card--timer,
.overlay-card--ringing {
  flex: 1 1 auto;
  width: 100%;
  min-height: 0;
}

.overlay-card--clock {
  background: transparent;
  box-shadow: none;
  padding: 0;
  backdrop-filter: none;
}

.overlay-card__title {
  font-size: 0.95rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 0.25rem;
  color: var(--overlay-accent-color);
}

.overlay-clock__time {
  font-size: clamp(3.5rem, 8vw, 6.5rem);
  font-weight: 300;
  letter-spacing: -0.03em;
}

.overlay-clock__date {
  font-size: clamp(1.3rem, 3vw, 2.2rem);
  font-weight: 400;
  opacity: 0.85;
}

.overlay-card--ambient {
  background: var(--overlay-ambient-bg);
}

.overlay-card--alert {
  background: var(--overlay-alert-bg);
  border: 1px solid rgba(255, 255, 255, 0.2);
}

.overlay-card--ringing {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  text-align: center;
  gap: 2vh;
  padding: 3vh 3vw;
  animation: overlayPulse 1.2s ease-in-out infinite alternate;
}

.overlay-card--reminder {
  text-align: left;
}

.overlay-card__body--reminder {
  margin: 0.8rem 0 1.2rem;
  font-size: 1.1rem;
  line-height: 1.4;
}

.overlay-reminder__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: center;
}

.overlay-reminder__delays {
  display: flex;
  gap: 0.4rem;
}

.overlay-card--timer {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  text-align: center;
  gap: 1.8vh;
  padding: 3vh 2vw;
}

.overlay-card--timer .overlay-timer__remaining {
  font-size: clamp(3rem, 12vw, 7rem);
  font-weight: 700;
  letter-spacing: 0.08em;
  line-height: 1.1;
}

.overlay-card--timer .overlay-timer__remaining--long {
  font-size: clamp(2.5rem, 10vw, 5.6rem);
  letter-spacing: 0.05em;
}

.overlay-card--timer .overlay-timer__remaining--xlong {
  font-size: clamp(2.2rem, 8vw, 4.6rem);
  letter-spacing: 0.03em;
}

.overlay-card--expired {
  opacity: 0.75;
}

.overlay-card--now-playing {
  min-width: 16rem;
  margin-top: auto;
}

.overlay-now-playing__body {
  font-size: 1.1rem;
}

.overlay-button {
  margin-top: 1rem;
  padding: 0.75rem 1.5rem;
  background: rgba(255, 255, 255, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 0.5rem;
  color: inherit;
  font-size: 1rem;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s ease, border-color 0.2s ease;
}

.overlay-button:hover {
  background: rgba(255, 255, 255, 0.3);
  border-color: rgba(255, 255, 255, 0.4);
}

.overlay-button:active {
  background: rgba(255, 255, 255, 0.25);
}

.overlay-button--small {
  padding: 0.35rem 0.8rem;
  font-size: 0.85rem;
}

.overlay-button--primary {
  display: block;
  width: 100%;
  padding: 1.1rem 1.5rem;
  font-size: clamp(1.4rem, 3vw, 2.6rem);
  font-weight: 600;
  border-radius: 0.85rem;
}

.overlay-reminder__actions .overlay-button {
  margin-top: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 2.4rem;
  padding: 0 1.2rem;
}

.overlay-card__actions {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-top: 1rem;
}

.overlay-card__actions .overlay-button {
  margin-top: 0;
  width: 100%;
}

@media (min-width: 520px) {
  .overlay-card__actions--split {
    flex-direction: row;
  }

  .overlay-card__actions--split .overlay-button {
    flex: 1;
  }

  .overlay-card__actions--split .overlay-button--primary {
    flex: 1.25;
  }
}

.overlay-card__body--ringing {
  font-size: clamp(1.1rem, 3vw, 2.2rem);
  line-height: 1.35;
}

@keyframes overlayPulse {
  from {
    box-shadow: 0 0 0 rgba(255, 0, 0, 0.35);
  }
  to {
    box-shadow: 0 0 25px rgba(255, 0, 0, 0.65);
  }
}

@media (max-width: 720px) {
  .overlay-clock__time {
    font-size: 2rem;
  }
  .overlay-card {
    padding: 0.85rem;
  }
}
""".strip()

OVERLAY_JS = """
(function () {
  const root = document.getElementById('pulse-overlay-root');
  if (!root) {
    return;
  }
  const stopEndpoint = root.dataset.stopEndpoint || '/overlay/stop';
  const clockNodes = root.querySelectorAll('[data-clock]');
  const timerNodes = root.querySelectorAll('[data-timer]');
  const infoEndpoint = root.dataset.infoEndpoint || '/overlay/info-card';
  const sizeClassMap = [
    { className: 'overlay-timer__remaining--xlong', active: (len) => len > 8 },
    { className: 'overlay-timer__remaining--long', active: (len) => len > 5 && len <= 8 },
  ];
  const hour12Attr = root.dataset.clockHour12;
  const hour12 = hour12Attr !== 'false';
  const timeOptions = { hour: 'numeric', minute: '2-digit', hour12 };
  const dateOptions = { weekday: 'long', month: 'long', day: 'numeric' };

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
    clockNodes.forEach((node) => {
      const tz = node.dataset.tz || undefined;
      const timeEl = node.querySelector('[data-clock-time]');
      const dateEl = node.querySelector('[data-clock-date]');
      if (timeEl) {
        timeEl.textContent = formatWithZone(now, tz, timeOptions);
      }
      if (dateEl) {
        dateEl.textContent = formatWithZone(now, tz, dateOptions);
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
      } else {
        node.classList.remove('overlay-card--expired');
      }
    });
  };

  tick();
  window.setInterval(tick, 1000);
  alignNowPlayingCard();
  window.addEventListener('resize', alignNowPlayingCard);

  // Handle stop timer button clicks
  root.addEventListener('click', (e) => {
    const closeCardButton = e.target.closest('[data-info-card-close]');
    if (closeCardButton) {
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

    const badgeButton = e.target.closest('[data-badge-action]');
    if (badgeButton) {
      const action = badgeButton.dataset.badgeAction;
      if (action === 'show_alarms') {
        fetch(infoEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'show_alarms' })
        });
      } else if (action === 'show_reminders') {
        fetch(infoEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'show_reminders' })
        });
      }
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

    const button = e.target.closest('[data-stop-timer]');
    if (!button) {
      return;
    }
    const eventId = button.dataset.eventId;
    if (!eventId) {
      return;
    }
    const previous = button.textContent;
    button.disabled = true;
    button.textContent = 'Stopping...';
    fetch(stopEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'stop', event_id: eventId })
    }).catch(() => {
      button.disabled = false;
      button.textContent = previous || 'Stop';
    });
  });
})();
""".strip()
