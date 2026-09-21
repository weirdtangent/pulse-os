"""pulse.conf parsing, secret masking in tool output, and cross-device compare."""

from __future__ import annotations

from tools.config_tools import _parse_pulse_conf

OFFICE_CONF = """
# Pulse OS configuration
PULSE_NAME="Office"
PULSE_HOSTNAME=pulse-office
MQTT_HOST='broker.local'
MQTT_PORT=1883
HOME_ASSISTANT_TOKEN="secret-office-token"
PULSE_OVERLAY_ENABLED=true
EMPTY_VALUE=
"""

KITCHEN_CONF = """
PULSE_NAME="Kitchen"
PULSE_HOSTNAME=pulse-kitchen
MQTT_HOST='broker.local'
MQTT_PORT=8883
HOME_ASSISTANT_TOKEN="secret-kitchen-token"
PULSE_OVERLAY_ENABLED=true
"""


class TestParsePulseConf:
    def test_strips_quotes_and_export_prefix(self):
        parsed = _parse_pulse_conf("export PULSE_NAME=\"Office\"\nMQTT_HOST='broker'\n")

        assert parsed == {"PULSE_NAME": "Office", "MQTT_HOST": "broker"}

    def test_skips_comments_blanks_and_non_assignments(self):
        parsed = _parse_pulse_conf("# comment\n\nnot an assignment\nKEY=value\n")

        assert parsed == {"KEY": "value"}

    def test_keeps_empty_values(self):
        assert _parse_pulse_conf("KEY=\n") == {"KEY": ""}

    def test_value_may_contain_equals(self):
        assert _parse_pulse_conf("URL=http://x/?a=b\n") == {"URL": "http://x/?a=b"}

    def test_mismatched_quotes_are_left_alone(self):
        assert _parse_pulse_conf("KEY=\"value'\n") == {"KEY": "\"value'"}


async def test_get_device_config_masks_secrets(call, ssh):
    ssh.responses["cat"] = OFFICE_CONF

    out = await call("get_device_config", device="pulse-office")

    assert "HOME_ASSISTANT_TOKEN=***" in out
    assert "secret-office-token" not in out
    assert "MQTT_HOST=broker.local" in out


async def test_get_device_config_drops_empty_values(call, ssh):
    ssh.responses["cat"] = OFFICE_CONF

    out = await call("get_device_config", device="pulse-office")

    assert "EMPTY_VALUE" not in out


async def test_get_device_config_filters_by_section(call, ssh):
    ssh.responses["cat"] = OFFICE_CONF

    out = await call("get_device_config", device="pulse-office", section="mqtt")

    assert "MQTT_HOST=broker.local" in out
    assert "PULSE_NAME" not in out


async def test_get_device_config_reports_an_empty_section(call, ssh):
    ssh.responses["cat"] = OFFICE_CONF

    out = await call("get_device_config", device="pulse-office", section="NOPE")

    assert "No config values found matching section 'NOPE'" in out


async def test_get_device_config_reads_the_configured_remote_path(call, ssh):
    ssh.responses["cat"] = OFFICE_CONF

    await call("get_device_config", device="pulse-office")

    # shlex.quote leaves an ordinary path unquoted; see test_ssh.py for the
    # quoting case.
    assert ssh.commands()[-1] == "cat /opt/pulse-os/pulse.conf"


async def test_get_device_config_handles_an_empty_file(call, ssh):
    ssh.responses["cat"] = "# nothing but a comment\n"

    out = await call("get_device_config", device="pulse-office")

    assert "No configuration found on pulse-office" in out


async def test_get_device_config_reports_read_failure(call, ssh):
    ssh.unreachable.add("pulse-office")

    out = await call("get_device_config", device="pulse-office")

    assert out.startswith("Failed to read config from pulse-office:")


async def test_compare_needs_two_devices(call):
    out = await call("compare_configs", devices="pulse-office")

    assert "Need at least 2 devices" in out


def _per_host(host: str) -> str:
    return OFFICE_CONF if host == "pulse-office" else KITCHEN_CONF


async def test_compare_reports_only_real_differences(call, ssh, monkeypatch):
    async def read_file(hostname, path):
        return _per_host(hostname)

    monkeypatch.setattr(ssh, "read_file", read_file)

    out = await call("compare_configs")

    assert "MQTT_PORT" in out  # 1883 vs 8883
    assert "PULSE_OVERLAY_ENABLED" not in out  # identical on both
    assert "PULSE_HOSTNAME" not in out  # expected to differ per device
    assert "PULSE_NAME" not in out  # per-device too


async def test_compare_masks_secret_values(call, ssh, monkeypatch):
    async def read_file(hostname, path):
        return _per_host(hostname)

    monkeypatch.setattr(ssh, "read_file", read_file)

    out = await call("compare_configs")

    assert "HOME_ASSISTANT_TOKEN" in out
    assert "secret-office-token" not in out
    assert "secret-kitchen-token" not in out
    assert "***" in out


async def test_compare_reports_a_device_that_could_not_be_read(call, ssh, monkeypatch):
    async def read_file(hostname, path):
        if hostname == "pulse-kitchen":
            raise OSError("host is down")
        return OFFICE_CONF

    monkeypatch.setattr(ssh, "read_file", read_file)

    out = await call("compare_configs")

    assert "Fetch errors:" in out
    assert "pulse-kitchen" in out


async def test_compare_when_no_device_can_be_read(call, ssh, monkeypatch):
    async def read_file(hostname, path):
        raise OSError("host is down")

    monkeypatch.setattr(ssh, "read_file", read_file)

    out = await call("compare_configs")

    assert out.startswith("Failed to fetch config from all devices:")


async def test_compare_says_so_when_everything_matches(call, ssh, monkeypatch):
    async def read_file(hostname, path):
        return KITCHEN_CONF

    monkeypatch.setattr(ssh, "read_file", read_file)

    out = await call("compare_configs", devices="pulse-office,pulse-kitchen")

    assert "All configurations match" in out
