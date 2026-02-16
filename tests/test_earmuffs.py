"""Tests for EarmuffsManager."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from pulse.assistant.earmuffs import EarmuffsManager


@pytest.fixture
def mock_mqtt():
    mqtt = Mock()
    mqtt.subscribe = Mock()
    return mqtt


@pytest.fixture
def mock_publisher():
    publisher = Mock()
    publisher._publish_earmuffs_state = Mock()
    return publisher


@pytest.fixture
def manager(mock_mqtt, mock_publisher):
    m = EarmuffsManager(
        mqtt=mock_mqtt,
        publisher=mock_publisher,
        base_topic="pulse/test",
    )
    m.set_wake_context_dirty_callback(Mock())
    return m


class TestInitialState:
    """Tests for initial state."""

    def test_disabled_by_default(self, manager):
        assert manager.enabled is False

    def test_state_not_restored_by_default(self, manager):
        assert manager.state_restored is False

    def test_manual_override_false_by_default(self, manager):
        assert manager.manual_override is False


class TestSubscribe:
    """Tests for MQTT subscription."""

    def test_subscribes_to_set_and_state_topics(self, manager, mock_mqtt):
        manager.subscribe()
        assert mock_mqtt.subscribe.call_count == 2
        topics = [c[0][0] for c in mock_mqtt.subscribe.call_args_list]
        assert "pulse/test/earmuffs/set" in topics
        assert "pulse/test/earmuffs/state" in topics

    def test_subscribe_handles_runtime_error(self, manager, mock_mqtt):
        mock_mqtt.subscribe.side_effect = RuntimeError("not connected")
        manager.subscribe()  # Should not raise


class TestSetEnabled:
    """Tests for set_enabled()."""

    def test_enable(self, manager, mock_publisher):
        manager.set_enabled(True)
        assert manager.enabled is True
        mock_publisher._publish_earmuffs_state.assert_called_once_with(True)

    def test_disable(self, manager, mock_publisher):
        manager.set_enabled(True)
        mock_publisher._publish_earmuffs_state.reset_mock()
        manager.set_enabled(False)
        assert manager.enabled is False
        mock_publisher._publish_earmuffs_state.assert_called_once_with(False)

    def test_no_change_no_publish(self, manager, mock_publisher):
        manager.set_enabled(False)  # Already false
        mock_publisher._publish_earmuffs_state.assert_not_called()

    def test_enable_marks_wake_context_dirty(self, manager):
        manager.set_enabled(True)
        manager._on_wake_context_dirty.assert_called_once()

    def test_disable_does_not_mark_wake_dirty(self, manager):
        manager.set_enabled(True)
        manager._on_wake_context_dirty.reset_mock()
        manager.set_enabled(False)
        manager._on_wake_context_dirty.assert_not_called()

    def test_manual_flag_sets_override(self, manager):
        manager.set_enabled(True, manual=True)
        assert manager.manual_override is True

    def test_non_manual_no_override(self, manager):
        manager.set_enabled(True)
        assert manager.manual_override is False


class TestGetEnabled:
    """Tests for get_enabled() callable interface."""

    def test_callable_interface(self, manager):
        assert manager.get_enabled() is False
        manager.set_enabled(True)
        assert manager.get_enabled() is True


class TestHandleCommand:
    """Tests for _handle_command() MQTT handler."""

    @pytest.mark.parametrize("payload", ["on", "ON", "true", "1", "yes", "enable", "enabled"])
    def test_enable_values(self, manager, payload):
        manager._handle_command(payload)
        assert manager.enabled is True

    @pytest.mark.parametrize("payload", ["off", "OFF", "false", "0", "no", "disable"])
    def test_disable_values(self, manager, payload):
        manager.set_enabled(True)
        manager._handle_command(payload)
        assert manager.enabled is False

    def test_toggle_from_off(self, manager):
        manager._handle_command("toggle")
        assert manager.enabled is True

    def test_toggle_from_on(self, manager):
        manager.set_enabled(True)
        manager._handle_command("toggle")
        assert manager.enabled is False

    def test_command_sets_manual_override(self, manager):
        manager._handle_command("on")
        assert manager.manual_override is True


class TestHandleStateRestore:
    """Tests for _handle_state_restore() retained message handler."""

    def test_restores_enabled_state(self, manager):
        manager._handle_state_restore("on")
        assert manager.enabled is True
        assert manager.state_restored is True

    def test_restores_disabled_state(self, manager):
        manager._handle_state_restore("off")
        assert manager.enabled is False
        assert manager.state_restored is True

    def test_ignores_subsequent_calls(self, manager):
        manager._handle_state_restore("on")
        assert manager.enabled is True
        manager._handle_state_restore("off")  # Should be ignored
        assert manager.enabled is True

    def test_enabled_restore_sets_manual_override(self, manager):
        manager._handle_state_restore("on")
        assert manager.manual_override is True

    def test_disabled_restore_clears_manual_override(self, manager):
        manager._handle_state_restore("off")
        assert manager.manual_override is False

    def test_enabled_restore_marks_wake_dirty(self, manager):
        manager._handle_state_restore("on")
        manager._on_wake_context_dirty.assert_called_once()

    def test_disabled_restore_no_wake_dirty(self, manager):
        manager._handle_state_restore("off")
        manager._on_wake_context_dirty.assert_not_called()

    def test_no_publish_on_restore(self, manager, mock_publisher):
        manager._handle_state_restore("on")
        mock_publisher._publish_earmuffs_state.assert_not_called()
