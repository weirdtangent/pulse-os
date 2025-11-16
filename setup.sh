#!/usr/bin/env bash
set -euo pipefail

# setup config
REPO_DIR="/opt/pulse-os"
CONFIG_FILE="$REPO_DIR/pulse.conf"
if [ -f "$CONFIG_FILE" ]; then
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
else
  echo "Warning: no pulse.conf found, using defaults."
fi

PULSE_REMOTE_LOGGING="${PULSE_REMOTE_LOGGING:-true}"
PULSE_BACKLIGHT_SUN="${PULSE_BACKLIGHT_SUN:-true}"
PULSE_BLUETOOTH_AUTOCONNECT="${PULSE_BLUETOOTH_AUTOCONNECT:-true}"

export PULSE_REMOTE_LOG_HOST
export PULSE_REMOTE_LOG_PORT


log() {
    echo "[PulseOS] $*"
}

ensure_dir() {
    local d="$1"
    if [ ! -d "$d" ]; then
        mkdir -p "$d"
        log "Created directory: $d"
    fi
}

ensure_symlink() {
    local target="$1"
    local link="$2"

    if [ -L "$link" ] && [ "$(readlink -f "$link")" = "$target" ]; then
        return
    fi

    ln -sf "$target" "$link"
    log "Linked $link → $target"
}

configure_device_identity() {
    local location="$1"

    if [ -z "$location" ]; then
        echo "Usage: $0 <location>"
        echo "Example: $0 bedroom"
        exit 1
    fi

    HOSTNAME="pulse-$location"

    log "Configuring hostname…"
    current_host=$(hostname)
    if [ "$current_host" != "$HOSTNAME" ]; then
        sudo raspi-config nonint do_hostname "$HOSTNAME"
        log "Hostname set to $HOSTNAME"
    else
        log "Hostname already $HOSTNAME"
    fi

    log "Ensuring autologin on tty1…"
    # autologin creates a drop-in here
    if [ -d /etc/systemd/system/getty@tty1.service.d ] \
       && grep -q autologin /etc/systemd/system/getty@tty1.service.d/* 2>/dev/null; then
        log "Autologin already enabled"
    else
        sudo raspi-config nonint do_boot_behaviour B2
        log "Autologin enabled"
    fi

    # Optional metadata file for the device
    echo "$location" | sudo tee /etc/pulse-location >/dev/null
}

install_packages() {
    log "Installing APT packages…"
    sudo xargs apt install -y < "$REPO_DIR/config/apt/manual-packages.txt"
    sudo apt autoremove -y
}

setup_user_dirs() {
    log "Ensuring user config dirs…"
    ensure_dir "/home/$PULSE_USER/.config"
    ensure_dir "/home/$PULSE_USER/.config/nvim"
    ensure_dir "/home/$PULSE_USER/.config/systemd/user"
    ensure_dir "/home/$PULSE_USER/bin"

    sudo chown -R "$PULSE_USER:$PULSE_USER" "/home/$PULSE_USER/.config" "/home/$PULSE_USER/bin"
}

link_home_files() {
    log "Linking home files…"

    ensure_symlink "$REPO_DIR/bin/kiosk-wrap.sh" "/home/$PULSE_USER/bin/kiosk-wrap.sh"
    ensure_symlink "$REPO_DIR/bin/revive-pulse.sh" "/home/$PULSE_USER/bin/revive-pulse.sh"
    ensure_symlink "$REPO_DIR/bin/pulse-backlight-sun.py" "/home/$PULSE_USER/bin/pulse-backlight-sun.py"

    ensure_symlink "$REPO_DIR/config/x/xinitrc" "/home/$PULSE_USER/.xinitrc"
    ensure_symlink "$REPO_DIR/config/x/profile" "/home/$PULSE_USER/.profile"

    ensure_symlink "$REPO_DIR/config/home/vimrc" "/home/$PULSE_USER/.vimrc"
    ensure_symlink "$REPO_DIR/config/home/init.vim" "/home/$PULSE_USER/.config/nvim/init.vim"
}

link_system_files() {
    log "Linking systemd/system files…"


    sudo ln -sf "$REPO_DIR/config/system/pulse-backlight-sun.service" \
        /etc/systemd/system/pulse-backlight-sun.service

    sudo ln -sf "$REPO_DIR/config/system/pulse-daily-reboot.service" \
        /etc/systemd/system/pulse-daily-reboot.service

    sudo ln -sf "$REPO_DIR/config/system/pulse-daily-reboot.timer" \
        /etc/systemd/system/pulse-daily-reboot.timer

    # Only link remote logging config if enabled
    if [ "$PULSE_REMOTE_LOGGING" = "true" ]; then
        sudo ln -sf "$REPO_DIR/config/system/syslog-ng.service" \
            /usr/lib/systemd/system/syslog-ng.service

        sudo mkdir -p /etc/syslog-ng/conf.d

        # Render remote-log.conf from template using Pulse config
        sed \
          -e "s/__REMOTE_LOG_HOST__/${PULSE_REMOTE_LOG_HOST}/g" \
          -e "s/__REMOTE_LOG_PORT__/${PULSE_REMOTE_LOG_PORT}/g" \
          "$REPO_DIR/config/system/syslog-ng/remote-log.conf.template" \
          | sudo tee /etc/syslog-ng/conf.d/remote-log.conf >/dev/null

        sudo mkdir -p /etc/systemd/system/syslog-ng.service.d
        ensure_symlink "$REPO_DIR/config/system/syslog-ng.service.d/override.conf" \
          /etc/systemd/system/syslog-ng.service.d/override.conf
    fi

    sudo ln -sf "$REPO_DIR/config/system/pulse-kiosk-mqtt.service" \
        /etc/systemd/system/pulse-kiosk-mqtt.service

    sudo ln -sf "$REPO_DIR/config/system/pulse-backlight.conf" \
        /etc/pulse-backlight.conf

    log "Linking systemd/user files…"

    sudo mkdir -p /etc/systemd/user

    sudo ln -sf "$REPO_DIR/config/system-user/bt-autoconnect.service" \
        /etc/systemd/user/bt-autoconnect.service

    sudo ln -sf "$REPO_DIR/config/system-user/bt-autoconnect.timer" \
        /etc/systemd/user/bt-autoconnect.timer
}

enable_services() {
    log "Reloading systemd…"
    sudo systemctl daemon-reload

    log "Enabling system services…"
    if [ "$PULSE_REMOTE_LOGGING" = "true" ]; then
        log "Enabling remote logging (syslog-ng)…"
        sudo systemctl enable --now syslog-ng
    else
        log "Disabling remote logging (syslog-ng)…"
        sudo systemctl disable --now syslog-ng 2>/dev/null || true
    fi
    sudo systemctl enable --now pulse-daily-reboot.timer

    if [ "$PULSE_BACKLIGHT_SUN" = "true" ]; then
        log "Enabling sun-driven backlight control..."
        sudo systemctl enable --now pulse-backlight-sun.service
    else
        log "Disabling sun-driven backlight control..."
        sudo systemctl disable --now pulse-backlight-sun.service 2>/dev/null || true
    fi

    sudo systemctl enable --now pulse-kiosk-mqtt.service

    log "Enabling user services (user-global)…"
    # These create symlinks in /etc/systemd/user/
    # The pulse user's per-user systemd instance will load them automatically.
    if [ "$PULSE_BLUETOOTH_AUTOCONNECT" = "true" ]; then
        log "Enabling Bluetooth auto-connect..."
        sudo systemctl --global enable bt-autoconnect.service
        sudo systemctl --global enable bt-autoconnect.timer
    else
        log "Disabling Bluetooth auto-connect..."
        sudo systemctl --global disable bt-autoconnect.service 2>/dev/null || true
        sudo systemctl --global disable bt-autoconnect.timer 2>/dev/null || true
    fi
}

setup_crontab() {
    if sudo crontab -u root -l 2>/dev/null | grep -q revive-pulse.sh; then
        log "Crontab entry already exists."
        return
    fi

    (sudo crontab -u root -l 2>/dev/null; \
        echo "*/$PULSE_REVIVE_INTERVAL * * * * /home/$PULSE_USER/bin/revive-pulse.sh") \
        | sudo crontab -u root -

    log "Added revive-pulse.sh cron job."
}

install_bluetooth_audio() {
    # Don’t try to start user services during install — no DBus.
    if [ "$PULSE_BLUETOOTH_AUTOCONNECT" = "true" ]; then
        log "Enabling PipeWire audio stack..."
        sudo systemctl --global enable pipewire.service
        sudo systemctl --global enable pipewire-pulse.service
        sudo systemctl --global enable wireplumber.service
    else
        log "PipeWire left untouched (Bluetooth autoconnect disabled)"
    fi
}

print_feature_summary() {
    echo
    echo "────────────────────────────────────────────────────────"
    echo " PulseOS Feature Summary"
    echo "────────────────────────────────────────────────────────"
    printf "  %-28s : %s\n" "Kiosk URL" "${PULSE_URL:-<default>}"
    printf "  %-28s : %ss\n" "Watchdog Interval" "${PULSE_WATCHDOG_INTERVAL:-120}"
    printf "  %-28s : %s\n" "Sun Backlight Control" \
        "$( [ "${PULSE_BACKLIGHT_SUN:-true}" = "true" ] && echo enabled || echo disabled )"
    printf "  %-28s : %s\n" "Bluetooth Autoconnect" \
        "$( [ "${PULSE_BLUETOOTH_AUTOCONNECT:-true}" = "true" ] && echo enabled || echo disabled )"
    printf "  %-28s : %s\n" "Remote Logging" \
        "$( [ "${PULSE_REMOTE_LOGGING:-true}" = "true" ] && echo enabled || echo disabled )"
    if [ "$PULSE_REMOTE_LOGGING" = "true" ]; then
      printf "  %-28s : %s\n" "Remote Log Host" "${PULSE_REMOTE_LOG_HOST:-<unset>}"
    fi
    printf "  %-28s : %s\n" "Pulse User" "${PULSE_USER:-pulse}"
    echo "────────────────────────────────────────────────────────"
    echo
}


manual_notes() {
    echo
    echo "──────────────────────────────────────────────────────"
    echo " MANUAL CONFIG NEEDED (once per device)"
    echo "──────────────────────────────────────────────────────"
    echo "Edit /boot/firmware/config.txt"
    echo "  • uncomment dtparam=i2c_arm=on"
    echo "  • set display_auto_detect=0"
    echo "  • add Touch Display overlay:"
    echo "      dtoverlay=vc4-kms-dsi-ili9881-7inch,rotation=90,dsi1,swapxy,invx"
    echo
    echo "Edit /boot/firmware/cmdline.txt"
    echo "  • append: video=DSI-2:720x1280M@60"
    echo
    echo "Bluetooth BoomPod pairing instructions (optional)…"
}

### Run steps

configure_device_identity "$1"

install_packages
setup_user_dirs
link_home_files
link_system_files
enable_services
setup_crontab
install_bluetooth_audio
manual_notes
print_feature_summary

log "PulseOS setup complete!"

