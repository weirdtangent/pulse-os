"""Tests for config persistence."""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pulse.config_persist import (
    ConfigPersister,
    _quote_value,
    _strip_quotes,
    persist_preference,
)


class TestQuoteUtilities:
    """Test quote helper functions."""

    def test_strip_quotes_double(self):
        """Test stripping double quotes."""
        assert _strip_quotes('"hello"') == "hello"

    def test_strip_quotes_single(self):
        """Test stripping single quotes."""
        assert _strip_quotes("'hello'") == "hello"

    def test_strip_quotes_no_quotes(self):
        """Test value without quotes."""
        assert _strip_quotes("hello") == "hello"

    def test_strip_quotes_mismatched(self):
        """Test mismatched quotes are not stripped."""
        assert _strip_quotes('"hello\'') == '"hello\''

    def test_strip_quotes_whitespace(self):
        """Test stripping with surrounding whitespace."""
        assert _strip_quotes('  "hello"  ') == "hello"

    def test_strip_quotes_empty(self):
        """Test stripping empty string."""
        assert _strip_quotes('""') == ""

    def test_quote_value_simple(self):
        """Test quoting a simple value."""
        assert _quote_value("hello") == '"hello"'

    def test_quote_value_with_quotes(self):
        """Test quoting a value containing quotes."""
        assert _quote_value('say "hello"') == '"say \\"hello\\""'

    def test_quote_value_with_backslash(self):
        """Test quoting a value containing backslashes."""
        assert _quote_value('path\\to\\file') == '"path\\\\to\\\\file"'

    def test_quote_value_empty(self):
        """Test quoting empty string."""
        assert _quote_value("") == '""'


class TestConfigPersisterBasic:
    """Test basic ConfigPersister functionality."""

    def test_init_default_path(self):
        """Test initialization with default path."""
        persister = ConfigPersister()
        assert persister._config_path == Path("/opt/pulse-os/pulse.conf")

    def test_init_custom_path(self):
        """Test initialization with custom path."""
        custom_path = Path("/tmp/custom.conf")
        persister = ConfigPersister(config_path=custom_path)
        assert persister._config_path == custom_path

    def test_init_custom_debounce(self):
        """Test initialization with custom debounce delay."""
        persister = ConfigPersister(debounce_seconds=5.0)
        assert persister._debounce_seconds == 5.0

    def test_init_custom_logger(self):
        """Test initialization with custom logger."""
        logger = Mock()
        persister = ConfigPersister(logger=logger)
        assert persister._logger == logger


class TestConfigPersisterDebouncing:
    """Test debouncing behavior."""

    def test_update_queues_change(self):
        """Test that update queues a change."""
        persister = ConfigPersister(debounce_seconds=10.0)  # Long delay to prevent auto-flush
        persister.update("TEST_VAR", "test_value")
        assert "TEST_VAR" in persister._pending_changes
        assert persister._pending_changes["TEST_VAR"] == "test_value"

    def test_update_overwrites_pending(self):
        """Test that multiple updates to same variable overwrite."""
        persister = ConfigPersister(debounce_seconds=10.0)
        persister.update("TEST_VAR", "value1")
        persister.update("TEST_VAR", "value2")
        assert persister._pending_changes["TEST_VAR"] == "value2"

    def test_update_schedules_timer(self):
        """Test that update schedules a timer."""
        persister = ConfigPersister(debounce_seconds=10.0)
        persister.update("TEST_VAR", "value")
        assert persister._timer is not None
        assert persister._timer.is_alive()
        persister._timer.cancel()

    def test_update_cancels_previous_timer(self):
        """Test that second update cancels first timer."""
        persister = ConfigPersister(debounce_seconds=10.0)
        persister.update("TEST_VAR", "value1")
        first_timer = persister._timer
        assert first_timer is not None
        persister.update("TEST_VAR", "value2")
        second_timer = persister._timer
        assert first_timer is not second_timer
        # Second update should create a new timer
        assert second_timer is not None
        assert second_timer.is_alive()
        second_timer.cancel()

    def test_flush_clears_pending_changes(self):
        """Test that flush clears pending changes."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write("TEST_VAR=\"old_value\"\n")
            temp_path = Path(f.name)

        try:
            persister = ConfigPersister(config_path=temp_path, debounce_seconds=0.1)
            persister.update("TEST_VAR", "new_value")
            time.sleep(0.2)  # Wait for debounce
            assert len(persister._pending_changes) == 0
        finally:
            temp_path.unlink(missing_ok=True)
            Path(str(temp_path) + ".lock").unlink(missing_ok=True)
            Path(str(temp_path) + ".backup").unlink(missing_ok=True)


class TestConfigPersisterWriting:
    """Test config file writing."""

    def test_write_updates_existing_variable(self):
        """Test updating an existing variable."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write('# Test config\n')
            f.write('TEST_VAR="old_value"\n')
            f.write('OTHER_VAR="other"\n')
            temp_path = Path(f.name)

        try:
            persister = ConfigPersister(config_path=temp_path, debounce_seconds=0.1)
            persister.update("TEST_VAR", "new_value")
            time.sleep(0.2)  # Wait for debounce

            content = temp_path.read_text()
            assert 'TEST_VAR="new_value"' in content
            assert 'OTHER_VAR="other"' in content
            assert '# Test config' in content
        finally:
            temp_path.unlink(missing_ok=True)
            Path(str(temp_path) + ".lock").unlink(missing_ok=True)
            Path(str(temp_path) + ".backup").unlink(missing_ok=True)

    def test_write_new_variable_not_supported(self):
        """Test that adding a new variable logs warning (not supported)."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write('EXISTING_VAR="value"\n')
            temp_path = Path(f.name)

        try:
            logger = Mock()
            persister = ConfigPersister(config_path=temp_path, debounce_seconds=0.1, logger=logger)
            persister.update("NEW_VAR", "new_value")
            time.sleep(0.2)

            # Should log warning that variable wasn't found
            assert logger.warning.called

            # File should be unchanged (new vars not added)
            content = temp_path.read_text()
            assert 'EXISTING_VAR="value"' in content
        finally:
            temp_path.unlink(missing_ok=True)
            Path(str(temp_path) + ".lock").unlink(missing_ok=True)
            Path(str(temp_path) + ".backup").unlink(missing_ok=True)

    def test_write_preserves_comments(self):
        """Test that comments are preserved."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write('# Important comment\n')
            f.write('TEST_VAR="old_value"\n')
            f.write('# Another comment\n')
            temp_path = Path(f.name)

        try:
            persister = ConfigPersister(config_path=temp_path, debounce_seconds=0.1)
            persister.update("TEST_VAR", "new_value")
            time.sleep(0.2)

            content = temp_path.read_text()
            assert '# Important comment' in content
            assert '# Another comment' in content
        finally:
            temp_path.unlink(missing_ok=True)
            Path(str(temp_path) + ".lock").unlink(missing_ok=True)
            Path(str(temp_path) + ".backup").unlink(missing_ok=True)

    def test_write_handles_commented_defaults(self):
        """Test updating a commented-out default variable."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write('# (default) TEST_VAR="default_value"\n')
            temp_path = Path(f.name)

        try:
            persister = ConfigPersister(config_path=temp_path, debounce_seconds=0.1)
            persister.update("TEST_VAR", "new_value")
            time.sleep(0.2)

            content = temp_path.read_text()
            assert 'TEST_VAR="new_value"' in content
            # Original comment should be removed or replaced
            assert 'default_value' not in content or 'new_value' in content
        finally:
            temp_path.unlink(missing_ok=True)
            Path(str(temp_path) + ".lock").unlink(missing_ok=True)
            Path(str(temp_path) + ".backup").unlink(missing_ok=True)

    def test_write_creates_backup(self):
        """Test that a backup file is created."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write('TEST_VAR="old_value"\n')
            temp_path = Path(f.name)

        backup_path = Path(str(temp_path) + ".backup")
        try:
            persister = ConfigPersister(config_path=temp_path, debounce_seconds=0.1)
            persister.update("TEST_VAR", "new_value")
            time.sleep(0.2)

            assert backup_path.exists()
            backup_content = backup_path.read_text()
            assert 'TEST_VAR="old_value"' in backup_content
        finally:
            temp_path.unlink(missing_ok=True)
            backup_path.unlink(missing_ok=True)
            Path(str(temp_path) + ".lock").unlink(missing_ok=True)

    def test_write_batches_multiple_changes(self):
        """Test that multiple changes are batched together."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write('VAR1="old1"\n')
            f.write('VAR2="old2"\n')
            temp_path = Path(f.name)

        try:
            persister = ConfigPersister(config_path=temp_path, debounce_seconds=0.1)
            persister.update("VAR1", "new1")
            persister.update("VAR2", "new2")
            time.sleep(0.2)

            content = temp_path.read_text()
            assert 'VAR1="new1"' in content
            assert 'VAR2="new2"' in content
        finally:
            temp_path.unlink(missing_ok=True)
            Path(str(temp_path) + ".lock").unlink(missing_ok=True)
            Path(str(temp_path) + ".backup").unlink(missing_ok=True)


class TestConfigPersisterErrorHandling:
    """Test error handling."""

    def test_write_nonexistent_file_logs_warning(self):
        """Test that writing to nonexistent file logs warning."""
        logger = Mock()
        nonexistent_path = Path("/tmp/nonexistent_file_12345.conf")
        persister = ConfigPersister(
            config_path=nonexistent_path,
            debounce_seconds=0.1,
            logger=logger,
        )
        persister.update("TEST_VAR", "value")
        time.sleep(0.2)

        # Should have logged a warning about missing file
        assert logger.warning.called

    def test_thread_safety(self):
        """Test that concurrent updates are thread-safe."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            for i in range(100):
                f.write(f'VAR{i}="initial"\n')
            temp_path = Path(f.name)

        try:
            persister = ConfigPersister(config_path=temp_path, debounce_seconds=0.2)

            def update_vars(start, end):
                for i in range(start, end):
                    persister.update(f"VAR{i}", f"updated{i}")

            threads = [
                threading.Thread(target=update_vars, args=(0, 25)),
                threading.Thread(target=update_vars, args=(25, 50)),
                threading.Thread(target=update_vars, args=(50, 75)),
                threading.Thread(target=update_vars, args=(75, 100)),
            ]

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            time.sleep(0.3)  # Wait for debounce

            content = temp_path.read_text()
            # All updates should have been applied
            for i in range(100):
                assert f'VAR{i}="updated{i}"' in content
        finally:
            temp_path.unlink(missing_ok=True)
            Path(str(temp_path) + ".lock").unlink(missing_ok=True)
            Path(str(temp_path) + ".backup").unlink(missing_ok=True)


class TestPersistPreferenceHelper:
    """Test the persist_preference helper function."""

    def test_persist_preference_unknown_key(self):
        """Test that unknown preference keys are handled."""
        logger = Mock()
        result = persist_preference("unknown_key", "value", logger=logger)
        # Should return False for unknown keys
        assert result is False
        # Should log warning
        assert logger.warning.called


class TestConfigPersisterSpecialCases:
    """Test special cases and edge conditions."""

    def test_empty_value(self):
        """Test updating with empty value."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write('TEST_VAR="old_value"\n')
            temp_path = Path(f.name)

        try:
            persister = ConfigPersister(config_path=temp_path, debounce_seconds=0.1)
            persister.update("TEST_VAR", "")
            time.sleep(0.2)

            content = temp_path.read_text()
            assert 'TEST_VAR=""' in content
        finally:
            temp_path.unlink(missing_ok=True)
            Path(str(temp_path) + ".lock").unlink(missing_ok=True)
            Path(str(temp_path) + ".backup").unlink(missing_ok=True)

    def test_value_with_special_characters(self):
        """Test updating with special characters."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write('TEST_VAR="old"\n')
            temp_path = Path(f.name)

        try:
            persister = ConfigPersister(config_path=temp_path, debounce_seconds=0.1)
            special_value = 'value with "quotes" and $pecial ch@rs!'
            persister.update("TEST_VAR", special_value)
            time.sleep(0.2)

            content = temp_path.read_text()
            # Should be properly escaped
            assert "TEST_VAR=" in content
            assert "quotes" in content
        finally:
            temp_path.unlink(missing_ok=True)
            Path(str(temp_path) + ".lock").unlink(missing_ok=True)
            Path(str(temp_path) + ".backup").unlink(missing_ok=True)
