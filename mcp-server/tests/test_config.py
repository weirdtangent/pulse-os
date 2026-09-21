"""Config loading, device-list merging, and secret masking."""

from __future__ import annotations

import json

# Imported as a module (not `from config import ...`) so the search-path patch
# below and the calls here refer to the same object — CodeQL flags mixing the
# two import forms for one module.
import config


def _write_conf(tmp_path, payload: dict) -> str:
    path = tmp_path / "pulse-mcp.conf"
    path.write_text(json.dumps(payload))
    return str(path)


def test_env_path_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PULSE_MCP_CONFIG",
        _write_conf(
            tmp_path,
            {
                "ssh": {"user": "someone", "timeout": 42},
                "mqtt": {"host": "broker.local", "port": 8883},
                "devices": ["pulse-office"],
                "devices_file": "",
                "auto_discover": False,
            },
        ),
    )
    cfg = config.load_config()

    assert cfg.ssh.user == "someone"
    assert cfg.ssh.timeout == 42
    assert cfg.mqtt.host == "broker.local"
    assert cfg.mqtt.port == 8883
    assert cfg.devices == ["pulse-office"]
    assert cfg.auto_discover is False


def test_missing_env_path_falls_back_to_defaults(tmp_path, monkeypatch):
    """A bad PULSE_MCP_CONFIG must not silently load some other config file.

    Devices are deliberately not asserted here: with no config file, the
    default `devices_file` still resolves against the repo root, so the list
    depends on whether a pulse-devices.conf is checked out beside the server.
    """
    monkeypatch.setenv("PULSE_MCP_CONFIG", str(tmp_path / "nope.conf"))
    cfg = config.load_config()

    assert cfg.ssh.user == "pulse"
    assert cfg.ssh.key_path == "~/.ssh/id_ed25519"
    assert cfg.ssh.timeout == 10
    assert cfg.mqtt.host == ""
    assert cfg.auto_discover is True


def test_unknown_keys_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PULSE_MCP_CONFIG",
        _write_conf(tmp_path, {"ssh": {"user": "pulse", "nonsense": "x"}, "devices_file": ""}),
    )
    cfg = config.load_config()

    assert not hasattr(cfg.ssh, "nonsense")


def test_devices_file_merges_without_duplicates(tmp_path, monkeypatch):
    devices_file = tmp_path / "pulse-devices.conf"
    devices_file.write_text(
        "\n".join(
            [
                "# the kitchen one is also listed in the json",
                "pulse-kitchen",
                "",
                "   pulse-bedroom   ",
                "# pulse-commented-out",
            ]
        )
    )
    monkeypatch.setenv(
        "PULSE_MCP_CONFIG",
        _write_conf(tmp_path, {"devices": ["pulse-office", "pulse-kitchen"], "devices_file": "pulse-devices.conf"}),
    )
    cfg = config.load_config()

    # json order preserved, file entries appended, no duplicate kitchen,
    # comments and blank lines dropped, whitespace stripped.
    assert cfg.devices == ["pulse-office", "pulse-kitchen", "pulse-bedroom"]


def test_devices_file_resolves_next_to_the_config(tmp_path, monkeypatch):
    """A relative devices_file is relative to the config, not the cwd."""
    (tmp_path / "devices.txt").write_text("pulse-great-room\n")
    monkeypatch.setenv("PULSE_MCP_CONFIG", _write_conf(tmp_path, {"devices_file": "devices.txt"}))
    monkeypatch.chdir(tmp_path.parent)

    assert config.load_config().devices == ["pulse-great-room"]


def test_missing_devices_file_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PULSE_MCP_CONFIG",
        _write_conf(tmp_path, {"devices": ["pulse-office"], "devices_file": "absent.conf"}),
    )

    assert config.load_config().devices == ["pulse-office"]


def test_search_paths_are_used_when_no_env_var(tmp_path, monkeypatch):
    found = tmp_path / "pulse-mcp.conf"
    found.write_text(json.dumps({"devices": ["pulse-office"], "devices_file": ""}))
    monkeypatch.delenv("PULSE_MCP_CONFIG", raising=False)
    monkeypatch.setattr(config, "_CONFIG_SEARCH_PATHS", [found])

    assert config.load_config().devices == ["pulse-office"]


class TestMaskSecrets:
    def test_masks_by_name_regardless_of_case(self):
        masked = config.mask_secrets(
            {
                "HOME_ASSISTANT_TOKEN": "abc123",
                "mqtt_password": "hunter2",
                "OPENAI_API_KEY": "sk-xxx",
                "some_secret": "s",
                "PULSE_APIKEY": "k",
            }
        )

        assert set(masked.values()) == {"***"}

    def test_leaves_ordinary_values_alone(self):
        masked = config.mask_secrets({"MQTT_HOST": "broker.local", "PULSE_NAME": "Kitchen"})

        assert masked == {"MQTT_HOST": "broker.local", "PULSE_NAME": "Kitchen"}

    def test_empty_secret_is_not_masked(self):
        """An unset token should read as unset, not as though it had a value."""
        assert config.mask_secrets({"HOME_ASSISTANT_TOKEN": ""}) == {"HOME_ASSISTANT_TOKEN": ""}
