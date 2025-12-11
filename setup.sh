#!/usr/bin/env bash
set -euo pipefail

# setup config
REPO_DIR="/opt/pulse-os"
CONFIG_FILE="$REPO_DIR/pulse.conf"
SYNC_SCRIPT="$REPO_DIR/bin/tools/sync-pulse-conf.py"
BOOT_MOUNT="/boot"
if [ -d /boot/firmware ]; then
    BOOT_MOUNT="/boot/firmware"
fi
BOOT_CONFIG="$BOOT_MOUNT/config.txt"
BOOT_CMDLINE="$BOOT_MOUNT/cmdline.txt"
BOOT_SPLASH="$BOOT_MOUNT/splash.rgb"
FIRMWARE_LOGO="/lib/firmware/boot-splash.tga"
LOCATION_FILE="/etc/pulse-location"

VERSION_FILE="$REPO_DIR/VERSION"
if [ -f "$VERSION_FILE" ]; then
    VERSION="v$(tr -d '\r\n' <"$VERSION_FILE")"
else
    VERSION="unknown"
fi

echo " ______   __  __     __         ______     ______    ";
echo "/\  == \ /\ \/\ \   /\ \       /\  ___\   /\  ___\   ";
echo "\ \  _-/ \ \ \_\ \  \ \ \____  \ \___  \  \ \  __\   ";
echo " \ \_\    \ \_____\  \ \_____\  \/\_____\  \ \_____\ ";
echo "  \/_/     \/_____/   \/_____/   \/_____/   \/_____/ ";
echo "";
echo "$VERSION";
echo "";

# Keep pulse.conf in sync with the latest template before sourcing it.
if [ -x "$SYNC_SCRIPT" ]; then
    echo "[PulseOS] Syncing pulse.conf with template…"
    if ! python3 "$SYNC_SCRIPT"; then
        echo "[PulseOS] Warning: sync-pulse-conf failed; continuing with existing config." >&2
    fi
else
    echo "[PulseOS] Warning: sync-pulse-conf missing at $SYNC_SCRIPT; skipping auto-sync." >&2
fi

if [ -f "$CONFIG_FILE" ]; then
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
else
    echo "[PulseOS] Warning: no pulse.conf found, using defaults."
fi

PULSE_USER="${PULSE_USER:-pulse}"
PULSE_REMOTE_LOGGING="${PULSE_REMOTE_LOGGING:-true}"
# Support both new and legacy variable names for backward compatibility
PULSE_DAY_NIGHT_AUTO="${PULSE_DAY_NIGHT_AUTO:-${PULSE_BACKLIGHT_SUN:-true}}"
PULSE_BLUETOOTH_AUTOCONNECT="${PULSE_BLUETOOTH_AUTOCONNECT:-true}"
PULSE_VOICE_ASSISTANT="${PULSE_VOICE_ASSISTANT:-false}"
PULSE_SNAPCLIENT="${PULSE_SNAPCLIENT:-false}"
PULSE_DISPLAY_TYPE="${PULSE_DISPLAY_TYPE:-dsi}"           # dsi | hdmi
PULSE_HDMI_CONNECTOR="${PULSE_HDMI_CONNECTOR:-HDMI-A-1}"  # HDMI connector name for kernel arg
PULSE_HDMI_MODE="${PULSE_HDMI_MODE:-1280x800M@60}"        # Kernel video mode
PULSE_HDMI_GROUP="${PULSE_HDMI_GROUP:-2}"
PULSE_HDMI_MODE_ID="${PULSE_HDMI_MODE_ID:-28}"            # hdmi_mode value for config.txt (28=1280x800@60)
PULSE_HDMI_ROTATE="${PULSE_HDMI_ROTATE:-0}"               # 0/1/2/3 => 0/90/180/270
PULSE_HDMI_KMSDEV="${PULSE_HDMI_KMSDEV:-/dev/dri/card1}"  # KMS device that owns HDMI on Pi 5/CM5
PULSE_REVIVE_INTERVAL="${PULSE_REVIVE_INTERVAL:-2}"       # minutes between kiosk revive checks

export PULSE_REMOTE_LOG_HOST
export PULSE_REMOTE_LOG_PORT


log() {
    echo "[PulseOS] $*"
}

secure_config_file() {
    if [ ! -f "$CONFIG_FILE" ]; then
        return
    fi

    local owner_stat desired_owner desired_group
    owner_stat=$(stat -c '%U:%G' "$CONFIG_FILE" 2>/dev/null || echo "")

    if id -u "$PULSE_USER" >/dev/null 2>&1; then
        desired_owner="$PULSE_USER"
        desired_group="$PULSE_USER"
    else
        desired_owner=$(printf '%s\n' "$owner_stat" | cut -d':' -f1)
        desired_group=$(printf '%s\n' "$owner_stat" | cut -d':' -f2)
    fi

    if [ -n "$desired_owner" ] && [ -n "$desired_group" ] && [ "$owner_stat" != "$desired_owner:$desired_group" ]; then
        if chown "$desired_owner:$desired_group" "$CONFIG_FILE" 2>/dev/null; then
            :
        else
            sudo chown "$desired_owner:$desired_group" "$CONFIG_FILE"
        fi
        log "Set owner on pulse.conf to $desired_owner:$desired_group."
    fi

    local mode
    mode=$(stat -c '%a' "$CONFIG_FILE" 2>/dev/null || echo "")
    if [ "$mode" != "600" ]; then
        if chmod 600 "$CONFIG_FILE" 2>/dev/null; then
            :
        else
            sudo chmod 600 "$CONFIG_FILE"
        fi
        log "Restricted pulse.conf permissions to 600."
    fi
}

secure_config_file

usage() {
    cat <<EOF
Usage: $0 [--no-restart] [location]

Provide the physical location identifier (e.g. kitchen). After the first
successful run, the script remembers the last location written to
$LOCATION_FILE and you may omit the argument to reuse it.

Flags:
  --no-restart    Skip automatic service restart at the end of setup.
EOF
}

normalize_bool() {
    local raw="${1:-}"
    case "${raw,,}" in
        1|true|yes|on) echo "true" ;;
        *) echo "false" ;;
    esac
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

    local sanitized
    if ! sanitized=$(sanitize_location "$contents"); then
        return 1
    fi

    if [ "$sanitized" != "$contents" ]; then
        log "Stored location in $LOCATION_FILE appears invalid; rerun setup with a location argument." >&2
        return 1
    fi

    printf '%s\n' "$sanitized"
}

sanitize_location() {
    local raw="$1"
    local lower sanitized

    lower=$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')
    lower=${lower//$'\r'/ }
    lower=${lower//$'\n'/ }
    sanitized=$(printf '%s' "$lower" | sed -E 's/[^a-z0-9]+/-/g; s/-+/-/g; s/^-+//; s/-+$//')

    if [ ${#sanitized} -gt 63 ]; then
        sanitized=${sanitized:0:63}
        sanitized=${sanitized%-}
    fi

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

remove_cmdline_arg_matching() {
    local pattern="$1"
    local file="$BOOT_CMDLINE"
    if [ ! -f "$file" ]; then
        return
    fi
    local current
    current=$(sudo cat "$file")
    # shellcheck disable=SC2001
    local updated
    updated=$(echo "$current" | sed -E "s/[[:space:]]${pattern}//g")
    if [ "$updated" != "$current" ]; then
        printf '%s\n' "$updated" | sudo tee "$file" >/dev/null
        log "Removed kernel args matching /${pattern}/ from $(basename "$file")"
    fi
}

remove_boot_config_matching() {
    local pattern="$1"
    local file="$BOOT_CONFIG"
    if [ ! -f "$file" ]; then
        return
    fi
    if sudo grep -qE "$pattern" "$file"; then
        sudo sed -i "/$pattern/d" "$file"
        log "Removed lines matching /$pattern/ from $(basename "$file")"
    fi
}

ensure_user_systemd_session() {
    local user="${PULSE_USER:-pulse}"
    if ! id "$user" >/dev/null 2>&1; then
        log "User $user not found; skipping user service setup."
        return
    fi

    local uid
    uid=$(id -u "$user")

    if command -v loginctl >/dev/null 2>&1; then
        sudo loginctl enable-linger "$user" >/dev/null 2>&1 || true
    fi

    if ! sudo systemctl is-active --quiet "user@${uid}.service"; then
        log "Starting systemd user instance for $user..."
        sudo systemctl start "user@${uid}.service" >/dev/null 2>&1 || true
    fi

    local runtime="/run/user/${uid}"
    local bus="${runtime}/bus"
    if [ ! -S "$bus" ]; then
        log "Waiting for user bus at ${bus}..."
        for _ in {1..10}; do
            [ -S "$bus" ] && break
            sleep 0.2
        done
    fi

    if [ -S "$bus" ]; then
        log "Enabling PipeWire user services for $user..."
        sudo -u "$user" \
            XDG_RUNTIME_DIR="$runtime" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=${bus}" \
            systemctl --user enable --now pipewire.service pipewire-pulse.service wireplumber.service \
            >/dev/null 2>&1 || log "Warning: failed to enable user PipeWire services for $user."
    else
        log "Warning: user bus unavailable for $user; PipeWire user services not configured."
    fi
}

run_user_systemctl() {
    local user="${PULSE_USER:-pulse}"
    if ! id "$user" >/dev/null 2>&1; then
        return 1
    fi
    local uid
    uid=$(id -u "$user") || return 1
    local runtime="/run/user/${uid}"
    local bus="${runtime}/bus"
    sudo -u "$user" \
        XDG_RUNTIME_DIR="$runtime" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=${bus}" \
        systemctl --user "$@"
}

ensure_pulse_asoundrc() {
    local user="${PULSE_USER:-pulse}"
    local home="/home/${user}"
    local target="${home}/.asoundrc"

    if [ ! -d "$home" ]; then
        log "Home directory $home missing; skipping .asoundrc creation."
        return
    fi

    if [ -f "$target" ]; then
        return
    fi

    log "Creating default ALSA → PulseAudio bridge at ${target}..."
    sudo -u "$user" tee "$target" >/dev/null <<'EOF'
pcm.!default {
    type pulse
}

ctl.!default {
    type pulse
}
EOF
}

ensure_wireplumber_bt_keepalive() {
    local user="${PULSE_USER:-pulse}"
    local home="/home/${user}"
    local conf_dir="${home}/.config/wireplumber/wireplumber.conf.d"
    local conf_file="${conf_dir}/50-pulse-bt-nosuspend.conf"

    if [ ! -d "$home" ]; then
        log "Home directory $home missing; skipping WirePlumber Bluetooth override."
        return
    fi

    if [ ! -f "$conf_file" ]; then
        sudo -u "$user" mkdir -p "$conf_dir"
        sudo -u "$user" tee "$conf_file" >/dev/null <<'EOF'
# Keep Bluetooth outputs awake so short prompts aren't clipped
rule = {
  matches = [
    { node.name = "~bluez_output.*" }
  ]
  actions = {
    update-props = {
      session.suspend-timeout-seconds = 0
    }
  }
}
EOF
        log "Configured WirePlumber Bluetooth keepalive at $conf_file"
    fi

    local uid
    uid=$(id -u "$user")
    local runtime="/run/user/${uid}"
    local bus="${runtime}/bus"
    if [ -S "$bus" ]; then
        sudo -u "$user" \
            XDG_RUNTIME_DIR="$runtime" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=${bus}" \
            systemctl --user try-restart wireplumber.service pipewire.service pipewire-pulse.service \
            >/dev/null 2>&1 || true
    fi
}

ensure_persistent_journal() {
    log "Configuring persistent system journal…"
    sudo mkdir -p /var/log/journal
    sudo systemd-tmpfiles --create --prefix /var/log/journal
    local mid
    if mid=$(cat /etc/machine-id 2>/dev/null); then
        sudo mkdir -p "/var/log/journal/${mid}"
        sudo chown root:systemd-journal /var/log/journal "/var/log/journal/${mid}"
        sudo chmod 2755 /var/log/journal "/var/log/journal/${mid}"
    fi
    sudo tee /etc/tmpfiles.d/pulse-journal.conf >/dev/null <<'EOF'
d /var/log/journal 2755 root systemd-journal -
d /var/log/journal/%m 2755 root systemd-journal -
EOF
    sudo systemd-tmpfiles --create /etc/tmpfiles.d/pulse-journal.conf
    if sudo grep -q '^Storage=' /etc/systemd/journald.conf; then
        sudo sed -i 's/^Storage=.*/Storage=persistent/' /etc/systemd/journald.conf
    else
        echo "Storage=persistent" | sudo tee -a /etc/systemd/journald.conf >/dev/null
    fi
    sudo systemctl enable --now systemd-journal-flush.service >/dev/null 2>&1 || true
    sudo systemctl restart systemd-journald >/dev/null 2>&1 || true
}

configure_display_stack() {
    if [ "${PULSE_DISPLAY_TYPE,,}" = "hdmi" ]; then
        log "Configuring HDMI display parameters…"
        # Remove any DSI-specific settings that break HDMI
        remove_boot_config_matching "^dtoverlay=vc4-kms-dsi-ili9881-7inch"
        remove_cmdline_arg_matching "video=DSI-2:[^ ]*"

        ensure_boot_config_line "dtparam=i2c_arm=on"
        ensure_boot_config_kv "display_auto_detect" "0"
        ensure_boot_config_kv "hdmi_force_hotplug" "1"
        ensure_boot_config_kv "hdmi_group" "${PULSE_HDMI_GROUP}"
        ensure_boot_config_kv "hdmi_mode" "${PULSE_HDMI_MODE_ID}"
        ensure_boot_config_kv "display_hdmi_rotate" "${PULSE_HDMI_ROTATE}"
        ensure_cmdline_arg "video=${PULSE_HDMI_CONNECTOR}:${PULSE_HDMI_MODE}"

        log "Writing X config for HDMI (kmsdev=${PULSE_HDMI_KMSDEV})…"
        sudo tee /etc/X11/xorg.conf >/dev/null <<EOF
Section "ServerLayout"
    Identifier "Layout0"
    Screen 0 "Screen0" 0 0
EndSection

Section "Device"
    Identifier "VC4"
    Driver "modesetting"
    Option "kmsdev" "${PULSE_HDMI_KMSDEV}"
    Option "PrimaryGPU" "true"
EndSection

Section "Monitor"
    Identifier "HDMI-1"
    Option "PreferredMode" "${PULSE_HDMI_MODE%M@*}"
EndSection

Section "Screen"
    Identifier "Screen0"
    Device "VC4"
    Monitor "HDMI-1"
EndSection
EOF
        log "Removing fbdev X driver to prevent fallback to framebuffer…"
        sudo apt-get purge -y xserver-xorg-video-fbdev xserver-xorg-video-all >/dev/null 2>&1 || true
    else
        log "Configuring Touch Display boot parameters…"
        # Clean up any forced HDMI config
        remove_cmdline_arg_matching "video=${PULSE_HDMI_CONNECTOR//\//\\/}:[^ ]*"
        sudo rm -f /etc/X11/xorg.conf
        ensure_boot_config_line "dtparam=i2c_arm=on"
        ensure_boot_config_kv "display_auto_detect" "0"
        ensure_boot_config_line "dtoverlay=vc4-kms-dsi-ili9881-7inch,rotation=90,dsi1,swapxy,invx"
        ensure_cmdline_arg "video=DSI-2:720x1280M@60"
    fi
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
    log "Refreshing APT package lists..."
    if ! sudo apt update; then
        log "Warning: apt update failed; continuing with cached package lists." >&2
    fi
    local missing_packages=()
    while IFS= read -r pkg; do
        [[ -z "$pkg" || "$pkg" == \#* ]] && continue
        if ! dpkg -s "$pkg" >/dev/null 2>&1; then
            missing_packages+=("$pkg")
        fi
    done < "$REPO_DIR/config/apt/manual-packages.txt"

    if (( ${#missing_packages[@]} > 0 )); then
        log "Installing missing packages: ${missing_packages[*]}"
        sudo apt install -y "${missing_packages[@]}"
    else
        log "All manual packages already installed."
    fi
    sudo apt autoremove -y
}

install_voice_assistant_python_deps() {
    if [ "${PULSE_VOICE_ASSISTANT:-false}" != "true" ]; then
        log "Voice assistant disabled; skipping Python dependency install."
        return
    fi

    if ! python3 -m pip --version >/dev/null 2>&1; then
        log "python3-pip not detected; installing so we can fetch Wyoming client…"
        sudo apt install -y python3-pip
    fi

    log "Ensuring Python packages for the voice assistant are installed for the pulse user…"
    if ! sudo -H -u "$PULSE_USER" python3 -m pip install \
        --user --upgrade --disable-pip-version-check --break-system-packages \
        wyoming httpx openlocationcode websockets; then
        log "Warning: failed to install Python packages via pip (voice assistant may not start)."
    fi
}

ensure_snapclient_package() {
    if dpkg -s snapclient >/dev/null 2>&1; then
        return
    fi
    log "Installing snapclient…"
    sudo apt install -y snapclient
}

disable_stock_snapclient() {
    if systemctl list-unit-files | grep -q "^snapclient.service"; then
        log "Disabling stock snapclient.service to avoid conflicts…"
        sudo systemctl disable --now snapclient.service 2>/dev/null || true
    fi
}

configure_snapclient() {
    local enabled="${PULSE_SNAPCLIENT:-false}"
    local config_file="/etc/default/pulse-snapclient"

    # Always disable the distro snapclient unit to prevent conflicts with ours
    disable_stock_snapclient

    if [ "$enabled" != "true" ]; then
        log "Snapcast client disabled; removing config."
        sudo rm -f "$config_file"
        return
    fi

    if [ -z "${PULSE_SNAPCAST_HOST:-}" ]; then
        log "Snapcast client enabled but PULSE_SNAPCAST_HOST is empty; skipping configuration."
        return
    fi

    ensure_snapclient_package

    sudo tee "$config_file" >/dev/null <<EOF
SNAPCAST_HOST="${PULSE_SNAPCAST_HOST}"
SNAPCAST_PORT="${PULSE_SNAPCAST_PORT:-1704}"
SNAPCAST_CONTROL_PORT="${PULSE_SNAPCAST_CONTROL_PORT:-1705}"
SNAPCLIENT_SOUNDCARD="${PULSE_SNAPCLIENT_SOUNDCARD:-pulse}"
SNAPCLIENT_LATENCY_MS="${PULSE_SNAPCLIENT_LATENCY_MS:-}"
SNAPCLIENT_EXTRA_ARGS="${PULSE_SNAPCLIENT_EXTRA_ARGS:---player pulse}"
SNAPCLIENT_HOST_ID="${PULSE_SNAPCLIENT_HOST_ID:-}"
EOF
    log "Wrote Snapcast client defaults to $config_file"

    # Snapclient streams through PipeWire/Pulse (--player pulse). Make sure the
    # per-user audio stack is running even when Bluetooth autoconnect is disabled.
    ensure_user_systemd_session
}

setup_user_dirs() {
    log "Ensuring user config dirs…"
    ensure_dir "/home/$PULSE_USER/.config"
    ensure_dir "/home/$PULSE_USER/.config/nvim"
    ensure_dir "/home/$PULSE_USER/.config/systemd/user"
    ensure_dir "/home/$PULSE_USER/bin"

    sudo chown -R "$PULSE_USER:$PULSE_USER" "/home/$PULSE_USER/.config" "/home/$PULSE_USER/bin"
}

generate_sound_files() {
    log "Ensuring sound files exist…"
    local sounds_dir="$REPO_DIR/assets/sounds"
    local notification_script="$REPO_DIR/bin/tools/generate-notification-tone.py"
    local alarm_script="$REPO_DIR/bin/tools/generate-alarm-tone.py"
    local reminder_script="$REPO_DIR/bin/tools/generate-reminder-tone.py"

    mkdir -p "$sounds_dir"

    if [ ! -f "$sounds_dir/notification.wav" ] && [ -x "$notification_script" ]; then
        log "Generating notification.wav…"
        PYTHONPATH="$REPO_DIR" python3 "$notification_script" -o "$sounds_dir/notification.wav" || log "Warning: failed to generate notification.wav"
    fi

    if [ ! -f "$sounds_dir/alarm.wav" ] && [ -x "$alarm_script" ]; then
        log "Generating alarm.wav…"
        PYTHONPATH="$REPO_DIR" python3 "$alarm_script" -o "$sounds_dir/alarm.wav" || log "Warning: failed to generate alarm.wav"
    fi

    if [ ! -f "$sounds_dir/reminder.wav" ] && [ -x "$reminder_script" ]; then
        log "Generating reminder.wav…"
        PYTHONPATH="$REPO_DIR" python3 "$reminder_script" -o "$sounds_dir/reminder.wav" || log "Warning: failed to generate reminder.wav"
    fi
}

link_home_files() {
    log "Linking home files…"

    ensure_symlink "$REPO_DIR/bin/kiosk-wrap.sh" "/home/$PULSE_USER/bin/kiosk-wrap.sh"
    ensure_symlink "$REPO_DIR/bin/revive-pulse.sh" "/home/$PULSE_USER/bin/revive-pulse.sh"
    ensure_symlink "$REPO_DIR/bin/pulse-backlight-sun.py" "/home/$PULSE_USER/bin/pulse-backlight-sun.py"
    ensure_symlink "$REPO_DIR/bin/tools/sync-pulse-conf.py" "/home/$PULSE_USER/bin/sync-pulse-conf.py"
    ensure_symlink "$REPO_DIR/bin/safe-reboot.sh" "/home/$PULSE_USER/bin/safe-reboot.sh"
    ensure_symlink "$REPO_DIR/bin/bt-mute.sh" "/home/$PULSE_USER/bin/bt-mute.sh"
    ensure_symlink "$REPO_DIR/bin/bt-autoconnect.sh" "/home/$PULSE_USER/bin/bt-autoconnect.sh"

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

    sudo ln -sf "$REPO_DIR/config/system/pulse-assistant.service" \
        /etc/systemd/system/pulse-assistant.service

    sudo ln -sf "$REPO_DIR/config/system/pulse-bt-mute.service" \
        /etc/systemd/system/pulse-bt-mute.service

    sudo ln -sf "$REPO_DIR/config/system/pulse-snapclient.service" \
        /etc/systemd/system/pulse-snapclient.service

    sudo ln -sf "$REPO_DIR/config/system/pulse-backlight.conf" \
        /etc/pulse-backlight.conf

    log "Linking systemd/user files…"

    sudo mkdir -p /etc/systemd/user

    sudo ln -sf "$REPO_DIR/config/system-user/bt-autoconnect.service" \
        /etc/systemd/user/bt-autoconnect.service

    sudo ln -sf "$REPO_DIR/config/system-user/bt-autoconnect.timer" \
        /etc/systemd/user/bt-autoconnect.timer

    sudo ln -sf "$REPO_DIR/config/system-user/pulse-assistant-display.service" \
        /etc/systemd/user/pulse-assistant-display.service

    sudo mkdir -p /etc/systemd/system/plymouth-quit-wait.service.d
    ensure_symlink "$REPO_DIR/config/system/plymouth-quit-wait.service.d/override.conf" \
        /etc/systemd/system/plymouth-quit-wait.service.d/override.conf

    sudo mkdir -p /etc/systemd/system/plymouth-quit.service.d
    ensure_symlink "$REPO_DIR/config/system/plymouth-quit.service.d/override.conf" \
        /etc/systemd/system/plymouth-quit.service.d/override.conf
}

install_boot_splash() {
    local firmware_src="$REPO_DIR/assets/splash/boot-splash.rgb"
    local firmware_logo_src="$REPO_DIR/assets/splash/boot-splash.tga"

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
        if [ -f "$REPO_DIR/assets/splash/graystorm-pulse_splash.png" ]; then
            if ! sudo cmp -s "$REPO_DIR/assets/splash/graystorm-pulse_splash.png" "$theme_dst_dir/splash.png" 2>/dev/null; then
                sudo install -Dm0644 "$REPO_DIR/assets/splash/graystorm-pulse_splash.png" "$theme_dst_dir/splash.png"
                theme_updated=1
            fi
        else
            log "Warning: splash PNG missing (assets/splash/graystorm-pulse_splash.png)"
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
    if [ "${PULSE_DAILY_REBOOT_ENABLED:-false}" = "true" ]; then
        log "Enabling daily reboot timer…"
        sudo systemctl enable --now pulse-daily-reboot.timer
    else
        log "Daily reboot disabled; stopping timer…"
        sudo systemctl disable --now pulse-daily-reboot.timer 2>/dev/null || true
    fi

    if [ "$PULSE_DAY_NIGHT_AUTO" = "true" ]; then
        log "Enabling day/night auto-adjustment (screen brightness)..."
        sudo systemctl enable --now pulse-backlight-sun.service
    else
        log "Disabling day/night auto-adjustment..."
        sudo systemctl disable --now pulse-backlight-sun.service 2>/dev/null || true
    fi

    sudo systemctl enable --now pulse-kiosk-mqtt.service

    if [ "$PULSE_VOICE_ASSISTANT" = "true" ]; then
        log "Enabling voice assistant services..."
        sudo systemctl enable --now pulse-assistant.service
        sudo systemctl --global enable pulse-assistant-display.service
    else
        log "Voice assistant disabled; stopping services..."
        sudo systemctl disable --now pulse-assistant.service 2>/dev/null || true
        sudo systemctl --global disable pulse-assistant-display.service 2>/dev/null || true
    fi

    if [ "$PULSE_SNAPCLIENT" = "true" ] && [ -n "${PULSE_SNAPCAST_HOST:-}" ]; then
        log "Enabling Snapcast client..."
        sudo systemctl enable --now pulse-snapclient.service
    else
        log "Snapcast client disabled; stopping service..."
        sudo systemctl disable --now pulse-snapclient.service 2>/dev/null || true
    fi

    log "Enabling user services (user-global)…"
    # These create symlinks in /etc/systemd/user/
    # The pulse user's per-user systemd instance will load them automatically.
    if [ "$PULSE_BLUETOOTH_AUTOCONNECT" = "true" ]; then
        log "Enabling Bluetooth auto-connect..."
        ensure_user_systemd_session
        sudo systemctl --global enable bt-autoconnect.service
        sudo systemctl --global enable bt-autoconnect.timer
        run_user_systemctl daemon-reload >/dev/null 2>&1 || true
        run_user_systemctl link "$REPO_DIR/config/system-user/bt-autoconnect.service" >/dev/null 2>&1 || true
        run_user_systemctl link "$REPO_DIR/config/system-user/bt-autoconnect.timer" >/dev/null 2>&1 || true
        if ! run_user_systemctl enable --now bt-autoconnect.service bt-autoconnect.timer >/dev/null 2>&1; then
            log "Warning: failed to enable bt-autoconnect units for $PULSE_USER; they will activate on next login."
        fi
        log "Enabling Bluetooth mute on shutdown..."
        sudo systemctl enable pulse-bt-mute.service
    else
        log "Disabling Bluetooth auto-connect..."
        sudo systemctl --global disable bt-autoconnect.service 2>/dev/null || true
        sudo systemctl --global disable bt-autoconnect.timer 2>/dev/null || true
        run_user_systemctl disable bt-autoconnect.service bt-autoconnect.timer >/dev/null 2>&1 || true
        sudo systemctl disable pulse-bt-mute.service 2>/dev/null || true
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
    if [ "$PULSE_BLUETOOTH_AUTOCONNECT" = "true" ]; then
        log "Enabling PipeWire audio stack..."
        sudo systemctl --global enable pipewire.service
        sudo systemctl --global enable pipewire-pulse.service
        sudo systemctl --global enable wireplumber.service
        ensure_user_systemd_session
        ensure_pulse_asoundrc
        ensure_wireplumber_bt_keepalive
        ensure_persistent_journal
    else
        log "PipeWire left untouched (Bluetooth autoconnect disabled)"
    fi
}

publish_summary_to_mqtt() {
    local summary_text="$1"
    local mqtt_host="${MQTT_HOST:-}"
    local mqtt_port="${MQTT_PORT:-1883}"
    local mqtt_user="${MQTT_USER:-${MQTT_USERNAME:-}}"
    local mqtt_pass="${MQTT_PASS:-${MQTT_PASSWORD:-}}"
    local mqtt_tls_enabled
    mqtt_tls_enabled=$(normalize_bool "${MQTT_TLS_ENABLED:-false}")
    local mqtt_cert="${MQTT_CERT:-}"
    local mqtt_key="${MQTT_KEY:-}"
    local mqtt_ca_cert="${MQTT_CA_CERT:-}"

    if [ -z "$mqtt_host" ] || [ "$mqtt_host" = "<not set>" ]; then
        return 0  # MQTT not configured, skip silently
    fi

    local hostname
    hostname=$(hostname 2>/dev/null || echo "pulse-unknown")
    local topic="pulse/${hostname}/setup/summary"

    local -a mosquitto_args=(-s -h "$mqtt_host" -p "$mqtt_port" -t "$topic" -r)
    if [ -n "$mqtt_user" ]; then
        mosquitto_args+=(-u "$mqtt_user")
    fi
    if [ -n "$mqtt_pass" ]; then
        mosquitto_args+=(-P "$mqtt_pass")
    fi
    if [ "$mqtt_tls_enabled" = "true" ]; then
        if [ -n "$mqtt_ca_cert" ]; then
            mosquitto_args+=(--cafile "$mqtt_ca_cert")
        else
            mosquitto_args+=(--capath "/etc/ssl/certs")
        fi
        if [ -n "$mqtt_cert" ]; then
            mosquitto_args+=(--cert "$mqtt_cert")
        fi
        if [ -n "$mqtt_key" ]; then
            mosquitto_args+=(--key "$mqtt_key")
        fi
    fi

    # mosquitto-clients is installed via manual-packages.txt
    # Use -s so mosquitto_pub reads the full summary from stdin as a single payload
    if echo "$summary_text" | mosquitto_pub "${mosquitto_args[@]}" 2>/dev/null; then
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
    local pulse_day_night_auto="${PULSE_DAY_NIGHT_AUTO:-${PULSE_BACKLIGHT_SUN:-true}}"
    local pulse_bluetooth_autoconnect="${PULSE_BLUETOOTH_AUTOCONNECT:-true}"
    local pulse_remote_logging="${PULSE_REMOTE_LOGGING:-true}"
    local pulse_remote_log_host="${PULSE_REMOTE_LOG_HOST:-<not set>}"
    local pulse_remote_log_port="${PULSE_REMOTE_LOG_PORT:-<not set>}"
    local mqtt_host="${MQTT_HOST:-<not set>}"
    local mqtt_port="${MQTT_PORT:-1883}"
    local mqtt_user_raw="${MQTT_USER:-${MQTT_USERNAME:-}}"
    local mqtt_pass_raw="${MQTT_PASS:-${MQTT_PASSWORD:-}}"
    local mqtt_user_display="${mqtt_user_raw:-<not set>}"
    local mqtt_pass_display="<not set>"
    if [ -n "$mqtt_pass_raw" ]; then
        mqtt_pass_display="<set>"
    fi
    local mqtt_tls_enabled
    mqtt_tls_enabled=$(normalize_bool "${MQTT_TLS_ENABLED:-false}")
    local mqtt_cert_display="${MQTT_CERT:-<not set>}"
    local mqtt_key_display="${MQTT_KEY:-<not set>}"
    local mqtt_ca_cert_display="${MQTT_CA_CERT:-<not set>}"
    local pulse_version_checks_per_day="${PULSE_VERSION_CHECKS_PER_DAY:-12}"
    local pulse_telemetry_interval_seconds="${PULSE_TELEMETRY_INTERVAL_SECONDS:-15}"
    local pulse_voice_assistant="${PULSE_VOICE_ASSISTANT:-false}"
    local pulse_snapclient="${PULSE_SNAPCLIENT:-false}"
    local wyoming_whisper_host="${WYOMING_WHISPER_HOST:-<not set>}"
    local wyoming_whisper_port="${WYOMING_WHISPER_PORT:-10300}"
    local wyoming_piper_host="${WYOMING_PIPER_HOST:-<not set>}"
    local wyoming_piper_port="${WYOMING_PIPER_PORT:-10200}"
    local wyoming_openwakeword_host="${WYOMING_OPENWAKEWORD_HOST:-<not set>}"
    local wyoming_openwakeword_port="${WYOMING_OPENWAKEWORD_PORT:-10400}"
    local pulse_snapcast_host="${PULSE_SNAPCAST_HOST:-}"
    local pulse_snapcast_port="${PULSE_SNAPCAST_PORT:-1704}"
    local pulse_assistant_provider="${PULSE_ASSISTANT_PROVIDER:-openai}"
    local pulse_assistant_wake_words_pulse="${PULSE_ASSISTANT_WAKE_WORDS_PULSE:-hey_jarvis}"
    local pulse_assistant_wake_words_ha="${PULSE_ASSISTANT_WAKE_WORDS_HA:-}"
    local openai_model="${OPENAI_MODEL:-gpt-4o-mini}"
    local gemini_model="${GEMINI_MODEL:-gemini-1.5-flash-latest}"
    local llm_model="$openai_model"
    if [ "$pulse_assistant_provider" = "gemini" ]; then
        llm_model="$gemini_model"
    fi
    # Build summary output by capturing printf statements
    local summary_output
    summary_output=$(
        echo "──────────────────────────────"
        echo " PulseOS Configuration Summary"
        echo "──────────────────────────────"
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
            "Day/Night Auto (PULSE_DAY_NIGHT_AUTO)" \
            "$( [ "$pulse_day_night_auto" = "true" ] && echo "enabled" || echo "disabled" )" \
            "Auto-adjust screen brightness based on sunrise/sunset, default: true"
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
            "MQTT User (MQTT_USER)" \
            "$mqtt_user_display" \
            "Optional MQTT username (falls back to MQTT_USERNAME)"
        kv_block \
            "MQTT Password (MQTT_PASS)" \
            "$mqtt_pass_display" \
            "Optional MQTT password; shown as <set> when provided"
        kv_block \
            "MQTT TLS (MQTT_TLS_ENABLED)" \
            "$( [ "$mqtt_tls_enabled" = "true" ] && echo "enabled" || echo "disabled" )" \
            "Enable TLS for MQTT connections, default: false"
        kv_block \
            "MQTT Client Cert (MQTT_CERT)" \
            "$mqtt_cert_display" \
            "Optional TLS client certificate path"
        kv_block \
            "MQTT Client Key (MQTT_KEY)" \
            "$mqtt_key_display" \
            "Optional TLS client key path"
        kv_block \
            "MQTT CA Cert (MQTT_CA_CERT)" \
            "$mqtt_ca_cert_display" \
            "Optional CA certificate path for broker validation"
        kv_block \
            "Version Checks (PULSE_VERSION_CHECKS_PER_DAY)" \
            "$pulse_version_checks_per_day checks/day" \
            "Update availability polling (2,4,6,8,12,24), default: 12"
        kv_block \
            "Telemetry Interval (PULSE_TELEMETRY_INTERVAL_SECONDS)" \
            "$pulse_telemetry_interval_seconds seconds" \
            "MQTT telemetry publishing interval (min 5), default: 15"
        kv_block \
            "Voice Assistant (PULSE_VOICE_ASSISTANT)" \
            "$( [ "$pulse_voice_assistant" = "true" ] && echo "enabled" || echo "disabled" )" \
            "Enable voice assistant features (wake word, STT, TTS), default: false"
        if [ "$pulse_voice_assistant" = "true" ]; then
            kv_block \
                "Wyoming Whisper (STT)" \
                "$wyoming_whisper_host:$wyoming_whisper_port" \
                "Speech-to-text server (wyoming-whisper)"
            kv_block \
                "Wyoming Piper (TTS)" \
                "$wyoming_piper_host:$wyoming_piper_port" \
                "Text-to-speech server (wyoming-piper)"
            kv_block \
                "Wyoming OpenWakeWord" \
                "$wyoming_openwakeword_host:$wyoming_openwakeword_port" \
                "Wake word detection server (wyoming-openwakeword)"
            kv_block \
                "Pulse Wake Words (PULSE_ASSISTANT_WAKE_WORDS_PULSE)" \
                "$pulse_assistant_wake_words_pulse" \
                "Comma-separated list of wake word models handled by the Pulse pipeline"
            kv_block \
                "HA Wake Words (PULSE_ASSISTANT_WAKE_WORDS_HA)" \
                "${pulse_assistant_wake_words_ha:-<none>}" \
                "Models that should route audio through Home Assistant Assist"
            kv_block \
                "LLM Provider" \
                "$pulse_assistant_provider (model: $llm_model)" \
                "Large language model used for responses"
        fi
        local snapcast_status
        if [ "$pulse_snapclient" = "true" ]; then
            if [ -n "$pulse_snapcast_host" ]; then
                snapcast_status="enabled (${pulse_snapcast_host}:${pulse_snapcast_port})"
            else
                snapcast_status="enabled (host not set)"
            fi
        else
            snapcast_status="disabled"
        fi
        kv_block \
            "Snapcast Client (PULSE_SNAPCLIENT)" \
            "$snapcast_status" \
            "Runs snapclient so Music Assistant can stream to Pulse speakers"
        echo "──────────────────────────────"
    )

    # Suppress stdout summary to keep setup logs concise
    write_summary_to_log "$summary_output"
    publish_summary_to_mqtt "$summary_output"
}

wait_for_overlay_server() {
    local overlay_port="${PULSE_OVERLAY_PORT:-8800}"
    local max_attempts=10
    local attempt=0

    while [ $attempt -lt $max_attempts ]; do
        if command -v curl >/dev/null 2>&1; then
            if curl -sf --max-time 1 "http://localhost:${overlay_port}/overlay" >/dev/null 2>&1; then
                return 0
            fi
        elif command -v nc >/dev/null 2>&1; then
            if nc -z localhost "$overlay_port" 2>/dev/null; then
                return 0
            fi
        fi
        attempt=$((attempt + 1))
        sleep 0.5
    done
    return 1
}

reload_overlay_via_devtools() {
    local devtools_url="${CHROMIUM_DEVTOOLS_URL:-http://localhost:9222/json}"
    local overlay_port="${PULSE_OVERLAY_PORT:-8800}"

    if ! command -v curl >/dev/null 2>&1; then
        return 1
    fi

    # Get list of tabs from DevTools
    local tabs_json
    tabs_json=$(curl -sf --max-time 2 "${devtools_url}" 2>/dev/null)
    if [ -z "$tabs_json" ]; then
        return 1
    fi

    # Extract the first tab's webSocketDebuggerUrl (or use the first tab's ID)
    local tab_id
    tab_id=$(echo "$tabs_json" | python3 -c "import sys, json; tabs = json.load(sys.stdin); print(tabs[0]['id'] if tabs else '')" 2>/dev/null)
    if [ -z "$tab_id" ]; then
        return 1
    fi

    # Use Page.reload with bypassCache to force reload of overlay iframe
    # We'll inject JavaScript to reload any iframe pointing to the overlay
    local overlay_url="http://localhost:${overlay_port}/overlay"
    local js_code="
    (function() {
        var iframes = document.querySelectorAll('iframe[src*=\"${overlay_port}/overlay\"], iframe[src*=\"/overlay\"]');
        iframes.forEach(function(iframe) {
            iframe.src = iframe.src.split('?')[0] + '?t=' + Date.now();
        });
        // Also try to find overlay by checking all iframes
        var allIframes = document.querySelectorAll('iframe');
        allIframes.forEach(function(iframe) {
            try {
                if (iframe.contentWindow && iframe.contentWindow.location) {
                    var href = iframe.contentWindow.location.href;
                    if (href && href.includes('${overlay_port}/overlay')) {
                        iframe.src = href.split('?')[0] + '?t=' + Date.now();
                    }
                }
            } catch(e) {}
        });
    })();
    "

    # Execute JavaScript via DevTools Runtime.evaluate
    local ws_url
    ws_url=$(echo "$tabs_json" | python3 -c "import sys, json; tabs = json.load(sys.stdin); print(tabs[0]['webSocketDebuggerUrl'] if tabs and 'webSocketDebuggerUrl' in tabs[0] else '')" 2>/dev/null)

    # Execute JavaScript via DevTools to reload overlay iframes
    # Note: This requires the tab to be accessible via DevTools
    if command -v python3 >/dev/null 2>&1; then
        python3 <<PYTHON_SCRIPT 2>/dev/null || true
import json
import urllib.request
import urllib.error

try:
    overlay_port = "$overlay_port"

    # Get the first tab's webSocketDebuggerUrl
    tabs_resp = urllib.request.urlopen("http://localhost:9222/json", timeout=2)
    tabs = json.loads(tabs_resp.read().decode('utf-8'))

    if not tabs or len(tabs) == 0:
        exit(1)

    tab = tabs[0]
    ws_url = tab.get('webSocketDebuggerUrl', '')

    if not ws_url:
        # Fallback: try to use Runtime.evaluate via HTTP (if supported)
        # This is a simplified approach - we'll just try to trigger a page reload
        # by fetching the overlay with a cache-busting parameter
        overlay_url = f"http://localhost:{overlay_port}/overlay?refresh={int(__import__('time').time() * 1000)}"
        try:
            urllib.request.urlopen(overlay_url, timeout=1)
        except:
            pass
        exit(0)

    # If we have websocket URL, we could use it, but that requires websocket library
    # For now, just exit - the MQTT refresh or direct fetch should work
    exit(0)
except Exception:
    exit(1)
PYTHON_SCRIPT
    fi

    return 0
}

publish_overlay_refresh_mqtt() {
    local hostname="${PULSE_HOSTNAME:-$(hostname -s 2>/dev/null || echo 'pulse')}"
    local reason="${1:-setup}"

    # Publish MQTT refresh message to prompt HA to reload overlay
    if [ -n "${MQTT_HOST:-}" ] && command -v mosquitto_pub >/dev/null 2>&1; then
        local mqtt_host="${MQTT_HOST}"
        local mqtt_port="${MQTT_PORT:-1883}"
        local mqtt_user="${MQTT_USER:-}"
        local mqtt_pass="${MQTT_PASS:-${MQTT_PASSWORD:-}}"
        local topic="pulse/${hostname}/overlay/refresh"
        local ts
        ts=$(date +%s)
        local version
        version=$((ts % 1000000))  # Use timestamp-based version
        local payload
        payload=$(python3 -c "import json, sys; print(json.dumps({'version': $version, 'reason': sys.argv[1], 'ts': $ts}))" "$reason" 2>/dev/null)

        if [ -z "$payload" ]; then
            # Fallback if python fails
            payload="{\"version\":${version},\"reason\":\"${reason}\",\"ts\":${ts}}"
        fi

        local mqtt_args=()
        [ -n "$mqtt_user" ] && mqtt_args+=(-u "$mqtt_user")
        [ -n "$mqtt_pass" ] && mqtt_args+=(-P "$mqtt_pass")

        mosquitto_pub -h "$mqtt_host" -p "$mqtt_port" "${mqtt_args[@]}" \
            -t "$topic" -m "$payload" >/dev/null 2>&1 || true
    fi
}

trigger_overlay_refresh() {
    local overlay_port="${PULSE_OVERLAY_PORT:-8800}"
    local hostname="${PULSE_HOSTNAME:-$(hostname -s 2>/dev/null || echo 'pulse')}"

    # Method 1: Publish MQTT refresh message (most reliable - prompts HA to reload)
    publish_overlay_refresh_mqtt "setup"

    # Method 2: Fetch overlay with cache-busting parameter (forces browser to check for update)
    if command -v curl >/dev/null 2>&1; then
        curl -sf --max-time 2 "http://localhost:${overlay_port}/overlay?refresh=$(date +%s)" >/dev/null 2>&1 || true
    fi

    # Method 3: Try to reload via DevTools
    reload_overlay_via_devtools || true

    # Small delay to allow refresh to propagate
    sleep 0.3
}

show_update_overlay() {
    local message="${1:-Updating PulseOS...}"
    local overlay_enabled="${PULSE_OVERLAY_ENABLED:-true}"
    local overlay_port="${PULSE_OVERLAY_PORT:-8800}"

    if [ "$overlay_enabled" != "true" ]; then
        return 0
    fi

    # Wait for overlay server to be ready (with timeout)
    if ! wait_for_overlay_server; then
        # Server not ready, but continue anyway
        return 0
    fi

    local url="http://localhost:${overlay_port}/overlay/info-card"
    local payload

    # Use python to properly encode JSON if available, otherwise use simple approach
    if command -v python3 >/dev/null 2>&1; then
        payload=$(python3 -c "import json, sys; print(json.dumps({'type': 'update', 'title': 'Updating Pulse', 'text': sys.argv[1]}))" "$message" 2>/dev/null)
    else
        # Fallback: simple JSON construction (assumes message doesn't contain quotes)
        payload="{\"type\":\"update\",\"title\":\"Updating Pulse\",\"text\":\"$message\"}"
    fi

    if [ -n "$payload" ] && command -v curl >/dev/null 2>&1; then
        if curl -sf -X POST "$url" \
            -H "Content-Type: application/json" \
            -d "$payload" \
            >/dev/null 2>&1; then
            # Give the server a moment to process the update
            sleep 0.5
            # Trigger multiple refresh attempts to ensure browser picks it up
            trigger_overlay_refresh
            # Try again after a short delay (browser might need time to process)
            sleep 0.5
            trigger_overlay_refresh
        fi
    fi
}

hide_update_overlay() {
    local overlay_enabled="${PULSE_OVERLAY_ENABLED:-true}"
    local overlay_port="${PULSE_OVERLAY_PORT:-8800}"

    if [ "$overlay_enabled" != "true" ]; then
        return 0
    fi

    # Wait briefly for overlay server (shorter timeout since we're hiding)
    wait_for_overlay_server || return 0

    local url="http://localhost:${overlay_port}/overlay/info-card"
    local payload='{"action": "clear"}'

    if command -v curl >/dev/null 2>&1; then
        if curl -sf -X POST "$url" \
            -H "Content-Type: application/json" \
            -d "$payload" \
            >/dev/null 2>&1; then
            # Trigger a refresh so the browser picks up the change
            sleep 0.2
            trigger_overlay_refresh
        fi
    fi
}

restart_pulse_services() {
    local restart_script="$REPO_DIR/bin/tools/restart-services.sh"
    if [ ! -x "$restart_script" ]; then
        log "Restart script not found at $restart_script; skipping automatic restart."
        return
    fi

    log "Restarting Pulse services via restart-services.sh…"
    if "$restart_script"; then
        log "Pulse services restarted."
    else
        log "Warning: service restart script failed; check the output above."
    fi
}

main() {
    # Publish MQTT overlay refresh at the very start to prompt HA to reload overlay
    # This ensures the browser is ready to show the popup when we update it
    publish_overlay_refresh_mqtt "setup_start"
    sleep 0.5  # Give HA time to process the refresh message

    # Show update overlay at start
    show_update_overlay "Running setup.sh..."

    # Ensure overlay is hidden on exit
    trap 'hide_update_overlay' EXIT

    local location_arg=""
    local auto_restart="true"

    while [ "$#" -gt 0 ]; do
        case "$1" in
            --no-restart)
                auto_restart="false"
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            --*)
                echo "Unknown option: $1" >&2
                usage
                exit 1
                ;;
            *)
                if [ -n "$location_arg" ]; then
                    echo "Only one location argument is allowed." >&2
                    usage
                    exit 1
                fi
                location_arg="$1"
                shift
                ;;
        esac
    done

    local location
    location=$(resolve_location "$location_arg")
    if [ -z "$location" ]; then
        log "Error: location is required on first run (e.g. ./setup.sh kitchen)."
        usage
        exit 1
    fi

    configure_device_identity "$location"
    configure_display_stack
    install_packages
    install_voice_assistant_python_deps
    setup_user_dirs
    generate_sound_files
    link_home_files
    link_system_files
    configure_snapclient
    install_boot_splash
    enable_services
    setup_crontab
    install_bluetooth_audio
    print_feature_summary
    if [ "$auto_restart" = "true" ]; then
        show_update_overlay "Restarting services..."
        restart_pulse_services
        show_update_overlay "Setup complete!"
        sleep 1
    else
        log "Skipping service restart (--no-restart)."
        show_update_overlay "Setup complete!"
        sleep 1
    fi

    log "PulseOS setup complete!"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi

