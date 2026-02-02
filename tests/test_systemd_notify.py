"""Tests for pulse/systemd_notify.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pulse.systemd_notify import _notify, ready, watchdog


def test_notify_noop_when_no_socket():
    """_notify does nothing when NOTIFY_SOCKET is unset."""
    with patch.dict("os.environ", {}, clear=True):
        # Should not raise
        _notify("READY=1")


@patch("pulse.systemd_notify.socket.socket")
def test_notify_sends_to_socket(mock_socket_class):
    """_notify sends the message to the NOTIFY_SOCKET path."""
    mock_sock = MagicMock()
    mock_socket_class.return_value.__enter__ = MagicMock(return_value=mock_sock)
    mock_socket_class.return_value.__exit__ = MagicMock(return_value=False)

    with patch.dict("os.environ", {"NOTIFY_SOCKET": "/run/systemd/notify"}):
        _notify("WATCHDOG=1")

    mock_sock.sendto.assert_called_once_with(b"WATCHDOG=1", "/run/systemd/notify")


@patch("pulse.systemd_notify.socket.socket")
def test_notify_abstract_socket(mock_socket_class):
    """_notify converts @ prefix to null byte for abstract sockets."""
    mock_sock = MagicMock()
    mock_socket_class.return_value.__enter__ = MagicMock(return_value=mock_sock)
    mock_socket_class.return_value.__exit__ = MagicMock(return_value=False)

    with patch.dict("os.environ", {"NOTIFY_SOCKET": "@/run/systemd/notify"}):
        _notify("READY=1")

    mock_sock.sendto.assert_called_once_with(b"READY=1", "\0/run/systemd/notify")


@patch("pulse.systemd_notify.socket.socket")
def test_notify_handles_os_error(mock_socket_class):
    """_notify logs but does not raise on OSError."""
    mock_sock = MagicMock()
    mock_sock.sendto.side_effect = OSError("Permission denied")
    mock_socket_class.return_value.__enter__ = MagicMock(return_value=mock_sock)
    mock_socket_class.return_value.__exit__ = MagicMock(return_value=False)

    with patch.dict("os.environ", {"NOTIFY_SOCKET": "/run/systemd/notify"}):
        _notify("WATCHDOG=1")  # Should not raise


@patch("pulse.systemd_notify._notify")
def test_ready_sends_correct_message(mock_notify):
    """ready() calls _notify with READY=1."""
    ready()
    mock_notify.assert_called_once_with("READY=1")


@patch("pulse.systemd_notify._notify")
def test_watchdog_sends_correct_message(mock_notify):
    """watchdog() calls _notify with WATCHDOG=1."""
    watchdog()
    mock_notify.assert_called_once_with("WATCHDOG=1")
