#!/usr/bin/env bash
set -euo pipefail

# setup config
REPO_DIR="/opt/pulse-os"
CONFIG_FILE="$REPO_DIR/pulse.conf"
BOOT_MOUNT="/boot"
if [ -d /boot/firmware ]; then
    BOOT_MOUNT="/boot/firmware"
fi
BOOT_CONFIG="$BOOT_MOUNT/config.txt"
BOOT_CMDLINE="$BOOT_MOUNT/cmdline.txt"
BOOT_SPLASH="$BOOT_MOUNT/splash.rgb"
FIRMWARE_LOGO="/lib/firmware/boot-splash.tga"
LOCATION_FILE="/etc/pulse-location"
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

usage() {
    cat <<EOF
Usage: $0 <location>

Provide the physical location identifier (e.g. kitchen). After the first
successful run, the script remembers the last location written to
$LOCATION_FILE and you may omit the argument to reuse it.
EOF
}

read_stored_location() {
    if [ ! -f "$LOCATION_FILE" ]; then
        return 1
    fi

    local contents=""
    if [ -r "$LOCATION_FILE" ]; then
        contents=$(<"$LOCATION_FILE")
    elif contents=$(sudo cat "$LOCATION_FILE" 2>/dev/null); then
        :
    fi

    contents=$(printf '%s' "$contents" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    if [ -z "$contents" ]; then
        return 1
    fi

    printf '%s\n' "$contents"
}

sanitize_location() {
    local raw="$1"
    local lower sanitized

    lower=$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')
    sanitized=$(printf '%s' "$lower" | sed -E 's/[^a-z0-9]+/-/g; s/-+/-/g; s/^-+//; s/-+$//')

    if [ -z "$sanitized" ]; then
        return 1
    fi

    printf '%s\n' "$sanitized"
}

resolve_location() {
    local provided="${1:-}"
    local raw=""

    if [ -n "$provided" ]; then
        raw="$provided"
    else
        local stored
        if stored=$(read_stored_location); then
            log "No location argument supplied; reusing stored location '${stored}' from $LOCATION_FILE" >&2
            raw="$stored"
        else
            usage
            exit 1
        fi
    fi

    local sanitized
    if ! sanitized=$(sanitize_location "$raw"); then
        echo "Invalid location '$raw'. Hostnames may only contain letters and numbers."
        usage
        exit 1
    fi

    if [ "$raw" != "$sanitized" ]; then
        log "Normalized location '$raw' → '$sanitized'" >&2
    fi

    printf '%s\n' "$sanitized"
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
    local parent
    parent=$(dirname "$link")

    if [ -L "$link" ] && [ "$(readlink -f "$link")" = "$target" ]; then
        return
    fi

    if [ ! -w "$parent" ]; then
        sudo ln -sf "$target" "$link"
    else
        ln -sf "$target" "$link"
    fi
    log "Linked $link → $target"
}

ensure_boot_config_line() {
    local line="$1"
    local file="$BOOT_CONFIG"

    if [ ! -f "$file" ]; then
        log "Warning: boot config $file not found (skipping line $line)"
        return
    fi

    if sudo grep -Fxq "$line" "$file"; then
        return
    fi

    echo "$line" | sudo tee -a "$file" >/dev/null
    log "Added $line to $(basename "$file")"
}

ensure_boot_config_kv() {
    local key="$1"
    local value="$2"
    local file="$BOOT_CONFIG"

    if [ ! -f "$file" ]; then
        log "Warning: boot config $file not found (skipping $key)"
        return
    fi

    if sudo grep -q "^${key}=" "$file"; then
        if ! sudo grep -q "^${key}=${value}$" "$file"; then
            sudo sed -i "s/^${key}=.*/${key}=${value}/" "$file"
            log "Updated ${key}=${value} in $(basename "$file")"
        fi
    else
        echo "${key}=${value}" | sudo tee -a "$file" >/dev/null
        log "Added ${key}=${value} to $(basename "$file")"
    fi
}

ensure_cmdline_arg() {
    local arg="$1"
    local file="$BOOT_CMDLINE"

    if [ ! -f "$file" ]; then
        log "Warning: boot cmdline $file not found (skipping $arg)"
        return
    fi

    local current
    current=$(sudo cat "$file")
    if [[ " $current " == *" $arg "* ]]; then
        return
    fi

    sudo sed -i "1s|$| $arg|" "$file"
    log "Added kernel arg: $arg"
}

ensure_cmdline_kv() {
    local key="$1"
    local value="$2"
    local file="$BOOT_CMDLINE"

    if [ ! -f "$file" ]; then
        log "Warning: boot cmdline $file not found (skipping $key)"
        return
    fi

    local current
    current=$(sudo cat "$file")
    local escaped="${value//\//\\/}"
    if echo "$current" | grep -qE "(^|[[:space:]])${key}="; then
        current=$(echo "$current" | sed -E "s/(^|[[:space:]])${key}=[^ ]*/\\1${key}=${escaped}/")
    else
        current="$current ${key}=${value}"
    fi

    printf '%s\n' "$current" | sudo tee "$file" >/dev/null
    log "Set kernel arg ${key}=${value}"
}

configure_display_stack() {
    log "Configuring Touch Display boot parameters…"
    ensure_boot_config_line "dtparam=i2c_arm=on"
    ensure_boot_config_kv "display_auto_detect" "0"
    ensure_boot_config_line "dtoverlay=vc4-kms-dsi-ili9881-7inch,rotation=90,dsi1,swapxy,invx"
    ensure_cmdline_arg "video=DSI-2:720x1280M@60"
}

configure_device_identity() {
    local location="$1"

    if [ -z "$location" ]; then
        usage
        exit 1
    fi

    HOSTNAME="pulse-$location"

    log "Configuring hostname…"
    local current_host
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
    echo "$location" | sudo tee "$LOCATION_FILE" >/dev/null
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

    sudo mkdir -p /etc/systemd/system/plymouth-quit-wait.service.d
    ensure_symlink "$REPO_DIR/config/system/plymouth-quit-wait.service.d/override.conf" \
        /etc/systemd/system/plymouth-quit-wait.service.d/override.conf

    sudo mkdir -p /etc/systemd/system/plymouth-quit.service.d
    ensure_symlink "$REPO_DIR/config/system/plymouth-quit.service.d/override.conf" \
        /etc/systemd/system/plymouth-quit.service.d/override.conf
}

install_boot_splash() {
    local firmware_src="$REPO_DIR/assets/boot-splash.rgb"
    local firmware_logo_src="$REPO_DIR/assets/boot-splash.tga"

    if [ -f "$firmware_src" ] && [ -n "$BOOT_SPLASH" ]; then
        if ! sudo cmp -s "$firmware_src" "$BOOT_SPLASH" 2>/dev/null; then
            sudo install -m 0644 "$firmware_src" "$BOOT_SPLASH"
            log "Installed firmware splash → $BOOT_SPLASH"
        else
            log "Firmware splash already up to date"
        fi
    else
        log "Warning: firmware splash source missing ($firmware_src)"
    fi

    if [ -f "$firmware_logo_src" ]; then
        if ! sudo cmp -s "$firmware_logo_src" "$FIRMWARE_LOGO" 2>/dev/null; then
            sudo install -m 0644 "$firmware_logo_src" "$FIRMWARE_LOGO"
            log "Installed bootloader splash → $FIRMWARE_LOGO"
        else
            log "Bootloader splash already up to date"
        fi
    else
        log "Warning: TGA splash source missing ($firmware_logo_src)"
    fi

    local theme_src_dir="$REPO_DIR/config/plymouth/pulse"
    local theme_dst_dir="/usr/share/plymouth/themes/pulse"
    local theme_updated=0

    if [ -d "$theme_src_dir" ]; then
        if ! sudo cmp -s "$theme_src_dir/pulse.plymouth" "$theme_dst_dir/pulse.plymouth" 2>/dev/null; then
            sudo install -Dm0644 "$theme_src_dir/pulse.plymouth" "$theme_dst_dir/pulse.plymouth"
            theme_updated=1
        fi
        if ! sudo cmp -s "$theme_src_dir/pulse.script" "$theme_dst_dir/pulse.script" 2>/dev/null; then
            sudo install -Dm0644 "$theme_src_dir/pulse.script" "$theme_dst_dir/pulse.script"
            theme_updated=1
        fi
        if [ -f "$REPO_DIR/assets/graystorm-pulse_splash.png" ]; then
            if ! sudo cmp -s "$REPO_DIR/assets/graystorm-pulse_splash.png" "$theme_dst_dir/splash.png" 2>/dev/null; then
                sudo install -Dm0644 "$REPO_DIR/assets/graystorm-pulse_splash.png" "$theme_dst_dir/splash.png"
                theme_updated=1
            fi
        else
            log "Warning: splash PNG missing (assets/graystorm-pulse_splash.png)"
        fi

        sudo update-alternatives --install \
            /usr/share/plymouth/themes/default.plymouth default.plymouth \
            "$theme_dst_dir/pulse.plymouth" 200 >/dev/null
        sudo update-alternatives --set default.plymouth "$theme_dst_dir/pulse.plymouth" >/dev/null 2>&1 \
            || true

        if [ "$theme_updated" -eq 1 ]; then
            sudo update-initramfs -u
            log "Regenerated initramfs with Pulse splash"
        else
            log "Plymouth splash already current"
        fi
    else
        log "Warning: Plymouth theme sources missing at $theme_src_dir"
    fi

    ensure_boot_config_kv "disable_splash" "0"
    ensure_boot_config_kv "disable_overscan" "1"

    ensure_cmdline_arg "quiet"
    ensure_cmdline_arg "splash"
    ensure_cmdline_arg "loglevel=3"
    ensure_cmdline_arg "vt.global_cursor_default=0"
    ensure_cmdline_arg "plymouth.ignore-serial-consoles"
    ensure_cmdline_kv "fullscreen_logo" "1"
    ensure_cmdline_kv "fullscreen_logo_name" "$(basename "$FIRMWARE_LOGO")"
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

publish_summary_to_mqtt() {
    local summary_text="$1"
    local mqtt_host="${MQTT_HOST:-}"
    local mqtt_port="${MQTT_PORT:-1883}"

    if [ -z "$mqtt_host" ] || [ "$mqtt_host" = "<not set>" ]; then
        return 0  # MQTT not configured, skip silently
    fi

    local hostname
    hostname=$(hostname 2>/dev/null || echo "pulse-unknown")
    local topic="pulse/${hostname}/setup/summary"

    # mosquitto-clients is installed via manual-packages.txt
    # Use -s so mosquitto_pub reads the full summary from stdin as a single payload
    if echo "$summary_text" | mosquitto_pub -s -h "$mqtt_host" -p "$mqtt_port" -t "$topic" -r 2>/dev/null; then
        log "Published setup summary to MQTT topic: $topic"
    fi
}

write_summary_to_log() {
    local summary_text="$1"
    local log_file="/var/log/pulse-setup-summary.log"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')

    {
        echo "────────────────────────────────────────────────────────────────────────"
        echo " PulseOS Setup Summary - $timestamp"
        echo "────────────────────────────────────────────────────────────────────────"
        echo "$summary_text"
        echo ""
    } | sudo tee -a "$log_file" >/dev/null
}

print_feature_summary() {
    local location
    location=$(read_stored_location 2>/dev/null || echo "<not set>")

    kv_block() {
        local header="$1"
        local value="$2"
        local description="${3:-}"
        printf "  %s\n" "$header"
        printf "    %s\n" "$value"
        if [ -n "$description" ]; then
            printf "    %s\n" "$description"
        fi
        echo
    }

    # Set defaults for variables that might not be set
    local pulse_user="${PULSE_USER:-pulse}"
    local pulse_url="${PULSE_URL:-<not set>}"
    local pulse_version="${PULSE_VERSION:-0.0.0}"
    local pulse_revive_interval="${PULSE_REVIVE_INTERVAL:-2}"
    local pulse_watchdog_interval="${PULSE_WATCHDOG_INTERVAL:-60}"
    local pulse_watchdog_limit="${PULSE_WATCHDOG_LIMIT:-5}"
    local pulse_watchdog_url="${PULSE_WATCHDOG_URL:-<not set>}"
    local pulse_backlight_sun="${PULSE_BACKLIGHT_SUN:-true}"
    local pulse_bluetooth_autoconnect="${PULSE_BLUETOOTH_AUTOCONNECT:-true}"
    local pulse_remote_logging="${PULSE_REMOTE_LOGGING:-true}"
    local pulse_remote_log_host="${PULSE_REMOTE_LOG_HOST:-<not set>}"
    local pulse_remote_log_port="${PULSE_REMOTE_LOG_PORT:-<not set>}"
    local mqtt_host="${MQTT_HOST:-<not set>}"
    local mqtt_port="${MQTT_PORT:-1883}"
    local pulse_version_checks_per_day="${PULSE_VERSION_CHECKS_PER_DAY:-12}"
    local pulse_telemetry_interval_seconds="${PULSE_TELEMETRY_INTERVAL_SECONDS:-15}"

    # Build summary output by capturing printf statements
    local summary_output
    summary_output=$(
        echo "────────────────────────────────────────────────────────────────────────"
        echo " PulseOS Configuration Summary"
        echo "────────────────────────────────────────────────────────────────────────"
        echo
        kv_block \
            "Location" \
            "$location" \
            "Physical location identifier (stored in /etc/pulse-location)"
        kv_block \
            "System User (PULSE_USER)" \
            "$pulse_user" \
            "Linux user with auto-login, default: pulse"
        kv_block \
            "Kiosk URL (PULSE_URL)" \
            "$pulse_url" \
            "Web page loaded on boot and Home button target"
        kv_block \
            "Version (PULSE_VERSION)" \
            "$pulse_version" \
            "PulseOS version from VERSION file"
        kv_block \
            "Revive Interval (PULSE_REVIVE_INTERVAL)" \
            "$pulse_revive_interval minutes" \
            "Cron interval to check and restart if needed, default: 2"
        kv_block \
            "Watchdog Interval (PULSE_WATCHDOG_INTERVAL)" \
            "$pulse_watchdog_interval seconds" \
            "Chromium watchdog check interval, default: 60"
        kv_block \
            "Watchdog Limit (PULSE_WATCHDOG_LIMIT)" \
            "$pulse_watchdog_limit failures" \
            "Failures before restarting browser, default: 5"
        kv_block \
            "Watchdog URL (PULSE_WATCHDOG_URL)" \
            "$pulse_watchdog_url" \
            "URL to check for browser health"
        kv_block \
            "Sun Backlight (PULSE_BACKLIGHT_SUN)" \
            "$( [ "$pulse_backlight_sun" = "true" ] && echo "enabled" || echo "disabled" )" \
            "Auto-dimming based on sunrise/sunset, default: true"
        kv_block \
            "Bluetooth Autoconnect (PULSE_BLUETOOTH_AUTOCONNECT)" \
            "$( [ "$pulse_bluetooth_autoconnect" = "true" ] && echo "enabled" || echo "disabled" )" \
            "Auto-connect to previously paired devices, default: true"
        kv_block \
            "Remote Logging (PULSE_REMOTE_LOGGING)" \
            "$( [ "$pulse_remote_logging" = "true" ] && echo "enabled" || echo "disabled" )" \
            "Send syslogs to remote server, default: true"
        if [ "$pulse_remote_logging" = "true" ]; then
            kv_block \
                "Remote Log Host (PULSE_REMOTE_LOG_HOST)" \
                "$pulse_remote_log_host" \
                "Remote syslog server hostname/IP"
            kv_block \
                "Remote Log Port (PULSE_REMOTE_LOG_PORT)" \
                "$pulse_remote_log_port" \
                "Remote syslog server port"
        fi
        kv_block \
            "MQTT Host (MQTT_HOST)" \
            "$mqtt_host" \
            "MQTT broker hostname for Home Assistant integration"
        kv_block \
            "MQTT Port (MQTT_PORT)" \
            "$mqtt_port" \
            "MQTT broker port, default: 1883"
        kv_block \
            "Version Checks (PULSE_VERSION_CHECKS_PER_DAY)" \
            "$pulse_version_checks_per_day checks/day" \
            "Update availability polling (2,4,6,8,12,24), default: 12"
        kv_block \
            "Telemetry Interval (PULSE_TELEMETRY_INTERVAL_SECONDS)" \
            "$pulse_telemetry_interval_seconds seconds" \
            "MQTT telemetry publishing interval (min 5), default: 15"
        echo "────────────────────────────────────────────────────────────────────────"
    )

    # Print to stdout (for normal interactive use)
    echo "$summary_output"

    # Also write to log file and publish to MQTT (for background runs)
    write_summary_to_log "$summary_output"
    publish_summary_to_mqtt "$summary_output"
}

main() {
    if [ "$#" -gt 1 ]; then
        usage
        exit 1
    fi

    local location
    location=$(resolve_location "${1:-}")

    configure_device_identity "$location"
    configure_display_stack
    install_packages
    setup_user_dirs
    link_home_files
    link_system_files
    install_boot_splash
    enable_services
    setup_crontab
    install_bluetooth_audio
    print_feature_summary

    log "PulseOS setup complete!"
}

main "$@"

