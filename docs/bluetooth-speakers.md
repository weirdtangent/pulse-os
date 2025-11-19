# Pairing a Bluetooth speaker

Most PulseOS installs use a Bluetooth speaker for audio output. Pairing is a one-time step per device (redo it if you reimage or replace the microSD card). The commands below assume you are logged in as the `pulse` user on the device.

1. **Put the speaker in pairing mode** so it is discoverable (check the speaker manual for the exact button combo).
2. **Start the Bluetooth CLI**:
   ```bash
   sudo -u pulse bluetoothctl
   ```
3. **Enable power and scanning** inside the prompt:
   ```
   power on
   agent on
   default-agent
   scan on
   ```
   Watch the output for a line such as `Device XX:XX:XX:XX:XX:XX My Speaker`.
4. **Pair, trust, and connect** using the MAC address from the scan step:
   ```
   pair XX:XX:XX:XX:XX:XX
   trust XX:XX:XX:XX:XX:XX
   connect XX:XX:XX:XX:XX:XX
   ```
   Replace `XX:XX:...` with your speaker’s address. Once connected, run `quit`.
5. **Optional: pin the speaker in `pulse.conf`** so the autoconnect script always targets it:
   ```bash
   PULSE_BT_MAC="XX:XX:XX:XX:XX:XX"
   ```
6. **Apply the changes**:
   ```bash
   cd /opt/pulse-os
   ./setup.sh <location-name>
   ```
   (Or tap the MQTT “Update” button if you use Home Assistant.)
7. **Test manually** if desired:
   ```bash
   /home/pulse/bin/bt-autoconnect.sh
   ```

### Tips

- If the speaker falls back to the “Zero” name (common on some BT pods), look for that label when scanning.
- Repeat the pairing flow whenever you reimage or swap SD cards, because trust information lives on the device.

