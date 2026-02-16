"""Tests for Wyoming STT/TTS/wake helper functions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from pulse.assistant.config import MicConfig, WyomingEndpoint
from pulse.assistant.wyoming import (
    play_tts_stream,
    probe_synthesize,
    probe_wake_detection,
    silence_bytes,
    transcribe_audio,
)
from wyoming.asr import Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.wake import Detection, NotDetected

pytestmark = pytest.mark.anyio


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mic():
    """Standard 16kHz mono mic configuration."""
    return MicConfig(command=["arecord"], rate=16000, width=2, channels=1, chunk_ms=30)


@pytest.fixture
def endpoint():
    """Wyoming endpoint without a default model."""
    return WyomingEndpoint(host="localhost", port=10300)


@pytest.fixture
def endpoint_with_model():
    """Wyoming endpoint with a default model configured."""
    return WyomingEndpoint(host="localhost", port=10300, model="whisper-base")


@pytest.fixture
def mock_client():
    """Create a mock AsyncTcpClient with async connect/disconnect/read/write."""
    client = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.write_event = AsyncMock()
    client.read_event = AsyncMock(return_value=None)
    return client


@pytest.fixture
def patch_tcp_client(mock_client):
    """Patch AsyncTcpClient to return mock_client and yield (constructor_mock, client)."""
    with patch("pulse.assistant.wyoming.AsyncTcpClient") as ctor:
        ctor.return_value = mock_client
        yield ctor, mock_client


@pytest.fixture
def mock_sink():
    """Mock AplaySink with async start/write/stop."""
    sink = AsyncMock()
    sink.start = AsyncMock()
    sink.write = AsyncMock()
    sink.stop = AsyncMock()
    return sink


# ============================================================================
# silence_bytes
# ============================================================================


class TestSilenceBytes:
    def test_standard_duration(self, mic):
        result = silence_bytes(30, mic)
        # 16000 * 0.030 = 480 frames, * 2 bytes * 1 channel = 960 bytes
        assert len(result) == 960

    def test_all_zeros(self, mic):
        result = silence_bytes(30, mic)
        assert result == b"\x00" * 960

    def test_different_durations_proportional(self, mic):
        short = silence_bytes(30, mic)
        long = silence_bytes(60, mic)
        assert len(long) == len(short) * 2

    def test_one_second(self, mic):
        result = silence_bytes(1000, mic)
        # 16000 frames * 2 bytes * 1 channel = 32000
        assert len(result) == 32000

    def test_minimum_one_byte(self, mic):
        # With duration_ms=0, frames=0, total_bytes would be 0 -> clamped to 1
        result = silence_bytes(0, mic)
        assert len(result) == 1

    def test_stereo_config(self):
        stereo = MicConfig(command=["arecord"], rate=16000, width=2, channels=2, chunk_ms=30)
        result = silence_bytes(30, stereo)
        # 480 frames * 2 bytes * 2 channels = 1920
        assert len(result) == 1920

    def test_different_sample_width(self):
        wide = MicConfig(command=["arecord"], rate=16000, width=4, channels=1, chunk_ms=30)
        result = silence_bytes(30, wide)
        # 480 frames * 4 bytes * 1 channel = 1920
        assert len(result) == 1920


# ============================================================================
# transcribe_audio
# ============================================================================


class TestTranscribeAudio:
    async def test_successful_transcription(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        transcript_event = Transcript(text="hello world").event()
        client.read_event = AsyncMock(return_value=transcript_event)

        result = await transcribe_audio(b"\x00" * 960, endpoint=endpoint, mic=mic, timeout=5.0)

        assert result == "hello world"

    async def test_returns_none_on_connection_closed(self, endpoint, mic, patch_tcp_client, mock_logger):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(return_value=None)

        result = await transcribe_audio(
            b"\x00" * 960,
            endpoint=endpoint,
            mic=mic,
            timeout=5.0,
            logger=mock_logger,
        )

        assert result is None
        mock_logger.debug.assert_called_once()

    async def test_none_without_logger_does_not_raise(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(return_value=None)

        result = await transcribe_audio(b"\x00" * 960, endpoint=endpoint, mic=mic, timeout=5.0)

        assert result is None

    async def test_model_override(self, endpoint_with_model, mic, patch_tcp_client):
        _, client = patch_tcp_client
        transcript_event = Transcript(text="test").event()
        client.read_event = AsyncMock(return_value=transcript_event)

        await transcribe_audio(
            b"\x00" * 960,
            endpoint=endpoint_with_model,
            mic=mic,
            model="whisper-large",
            timeout=5.0,
        )

        # The first write_event call should be the Transcribe event
        first_write = client.write_event.call_args_list[0]
        event = first_write[0][0]
        assert event.data is not None
        # When model= is passed, it should override endpoint.model
        assert "whisper-large" in str(event.data)

    async def test_endpoint_model_used_when_no_override(self, endpoint_with_model, mic, patch_tcp_client):
        _, client = patch_tcp_client
        transcript_event = Transcript(text="test").event()
        client.read_event = AsyncMock(return_value=transcript_event)

        await transcribe_audio(
            b"\x00" * 960,
            endpoint=endpoint_with_model,
            mic=mic,
            timeout=5.0,
        )

        first_write = client.write_event.call_args_list[0]
        event = first_write[0][0]
        assert "whisper-base" in str(event.data)

    async def test_language_parameter(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        transcript_event = Transcript(text="bonjour").event()
        client.read_event = AsyncMock(return_value=transcript_event)

        await transcribe_audio(
            b"\x00" * 960,
            endpoint=endpoint,
            mic=mic,
            language="fr",
            timeout=5.0,
        )

        first_write = client.write_event.call_args_list[0]
        event = first_write[0][0]
        assert "fr" in str(event.data)

    async def test_multiple_chunks_for_large_audio(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        transcript_event = Transcript(text="long audio").event()
        client.read_event = AsyncMock(return_value=transcript_event)

        # bytes_per_chunk for this mic: int(16000*0.030)*2*1 = 960
        large_audio = b"\x00" * 2880  # 3 chunks of 960 bytes

        await transcribe_audio(large_audio, endpoint=endpoint, mic=mic, timeout=5.0)

        # Expected writes: Transcribe + AudioStart + 3 AudioChunks + AudioStop = 6
        assert client.write_event.call_count == 6

    async def test_disconnect_called_on_success(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        transcript_event = Transcript(text="ok").event()
        client.read_event = AsyncMock(return_value=transcript_event)

        await transcribe_audio(b"\x00" * 960, endpoint=endpoint, mic=mic, timeout=5.0)

        client.disconnect.assert_awaited_once()

    async def test_disconnect_called_on_error(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await transcribe_audio(b"\x00" * 960, endpoint=endpoint, mic=mic, timeout=5.0)

        client.disconnect.assert_awaited_once()

    async def test_client_constructed_with_endpoint(self, endpoint, mic, patch_tcp_client):
        ctor, client = patch_tcp_client
        transcript_event = Transcript(text="test").event()
        client.read_event = AsyncMock(return_value=transcript_event)

        await transcribe_audio(b"\x00" * 960, endpoint=endpoint, mic=mic)

        ctor.assert_called_once_with("localhost", 10300)


# ============================================================================
# play_tts_stream
# ============================================================================


class TestPlayTtsStream:
    async def test_successful_playback(self, endpoint, mock_sink, patch_tcp_client):
        _, client = patch_tcp_client
        audio_bytes = b"\x00" * 960
        client.read_event = AsyncMock(
            side_effect=[
                AudioStart(rate=16000, width=2, channels=1).event(),
                AudioChunk(rate=16000, width=2, channels=1, audio=audio_bytes).event(),
                AudioStop().event(),
            ]
        )

        await play_tts_stream("hello", endpoint=endpoint, sink=mock_sink, timeout=5.0)

        mock_sink.start.assert_awaited_once_with(16000, 2, 1)
        mock_sink.write.assert_awaited_once_with(audio_bytes)
        mock_sink.stop.assert_awaited_once()

    async def test_with_audio_guard(self, endpoint, mock_sink, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(
            side_effect=[
                AudioStart(rate=16000, width=2, channels=1).event(),
                AudioStop().event(),
            ]
        )

        guard_entered = False
        guard_exited = False

        @asynccontextmanager
        async def mock_guard():
            nonlocal guard_entered, guard_exited
            guard_entered = True
            yield
            guard_exited = True

        await play_tts_stream(
            "hello",
            endpoint=endpoint,
            sink=mock_sink,
            audio_guard=mock_guard(),
            timeout=5.0,
        )

        assert guard_entered
        assert guard_exited

    async def test_without_audio_guard(self, endpoint, mock_sink, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(
            side_effect=[
                AudioStart(rate=16000, width=2, channels=1).event(),
                AudioStop().event(),
            ]
        )

        # Should not raise even though audio_guard is None (default)
        await play_tts_stream("hello", endpoint=endpoint, sink=mock_sink, timeout=5.0)

        mock_sink.start.assert_awaited_once()
        mock_sink.stop.assert_awaited_once()

    async def test_sink_stop_called_on_error(self, endpoint, mock_sink, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(
            side_effect=[
                AudioStart(rate=16000, width=2, channels=1).event(),
                RuntimeError("stream failure"),
            ]
        )

        with pytest.raises(RuntimeError, match="stream failure"):
            await play_tts_stream("hello", endpoint=endpoint, sink=mock_sink, timeout=5.0)

        # The finally block should still call stop since started=True
        mock_sink.stop.assert_awaited_once()

    async def test_sink_stop_not_called_when_never_started(self, endpoint, mock_sink, patch_tcp_client):
        _, client = patch_tcp_client
        # Connection closes immediately, no AudioStart ever received
        client.read_event = AsyncMock(return_value=None)

        await play_tts_stream("hello", endpoint=endpoint, sink=mock_sink, timeout=5.0)

        mock_sink.start.assert_not_awaited()
        mock_sink.stop.assert_not_awaited()

    async def test_voice_name_passed_through(self, endpoint, mock_sink, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(side_effect=[AudioStop().event()])

        await play_tts_stream(
            "hello",
            endpoint=endpoint,
            sink=mock_sink,
            voice_name="en_US-amy-medium",
            timeout=5.0,
        )

        # The Synthesize write_event should reference the voice
        first_write = client.write_event.call_args_list[0]
        event = first_write[0][0]
        assert "en_US-amy-medium" in str(event.data)


# ============================================================================
# probe_synthesize
# ============================================================================


class TestProbeSynthesize:
    async def test_returns_started_and_chunk_count(self, endpoint, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(
            side_effect=[
                AudioStart(rate=22050, width=2, channels=1).event(),
                AudioChunk(rate=22050, width=2, channels=1, audio=b"\x00" * 512).event(),
                AudioChunk(rate=22050, width=2, channels=1, audio=b"\x00" * 512).event(),
                AudioChunk(rate=22050, width=2, channels=1, audio=b"\x00" * 512).event(),
                AudioStop().event(),
            ]
        )

        started, chunks = await probe_synthesize(endpoint=endpoint, text="test", timeout=5.0)

        assert started is True
        assert chunks == 3

    async def test_no_audio_start_returns_false(self, endpoint, patch_tcp_client):
        _, client = patch_tcp_client
        # Server closes connection without sending AudioStart
        client.read_event = AsyncMock(return_value=None)

        started, chunks = await probe_synthesize(endpoint=endpoint, text="test", timeout=5.0)

        assert started is False
        assert chunks == 0

    async def test_audio_start_but_no_chunks(self, endpoint, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(
            side_effect=[
                AudioStart(rate=22050, width=2, channels=1).event(),
                AudioStop().event(),
            ]
        )

        started, chunks = await probe_synthesize(endpoint=endpoint, text="test", timeout=5.0)

        assert started is True
        assert chunks == 0


# ============================================================================
# probe_wake_detection
# ============================================================================


class TestProbeWakeDetection:
    async def test_detection_returns_model_name(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(return_value=Detection(name="hey_jarvis").event())

        result = await probe_wake_detection(
            endpoint=endpoint,
            mic=mic,
            models=["hey_jarvis"],
            timeout=5.0,
        )

        assert result == "hey_jarvis"

    async def test_detection_without_name_returns_first_model(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(return_value=Detection(name=None).event())

        result = await probe_wake_detection(
            endpoint=endpoint,
            mic=mic,
            models=["hey_jarvis", "ok_nabu"],
            timeout=5.0,
        )

        assert result == "hey_jarvis"

    async def test_detection_without_name_empty_models(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(return_value=Detection(name=None).event())

        result = await probe_wake_detection(
            endpoint=endpoint,
            mic=mic,
            models=[],
            timeout=5.0,
        )

        assert result is None

    async def test_not_detected_returns_none(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(return_value=NotDetected().event())

        result = await probe_wake_detection(
            endpoint=endpoint,
            mic=mic,
            models=["hey_jarvis"],
            timeout=5.0,
        )

        assert result is None

    async def test_connection_closed_returns_none(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(return_value=None)

        result = await probe_wake_detection(
            endpoint=endpoint,
            mic=mic,
            models=["hey_jarvis"],
            timeout=5.0,
        )

        assert result is None

    async def test_uses_silence_when_no_audio(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(return_value=NotDetected().event())

        await probe_wake_detection(
            endpoint=endpoint,
            mic=mic,
            models=["hey_jarvis"],
            timeout=5.0,
        )

        # The AudioChunk write should contain silence (all zeros)
        # Writes: Detect, AudioStart, AudioChunk, AudioStop = 4
        assert client.write_event.call_count == 4
        chunk_write = client.write_event.call_args_list[2]
        chunk_event = chunk_write[0][0]
        # The audio data in the chunk should be silence (zeros)
        assert b"\x00" in chunk_event.payload

    async def test_custom_audio_passed_through(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(return_value=Detection(name="hey_jarvis").event())

        custom_audio = b"\x01\x02\x03\x04" * 240

        await probe_wake_detection(
            endpoint=endpoint,
            mic=mic,
            models=["hey_jarvis"],
            audio=custom_audio,
            timeout=5.0,
        )

        # The AudioChunk write should contain our custom audio
        chunk_write = client.write_event.call_args_list[2]
        chunk_event = chunk_write[0][0]
        assert custom_audio in chunk_event.payload

    async def test_disconnect_called_on_success(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(return_value=Detection(name="hey_jarvis").event())

        await probe_wake_detection(
            endpoint=endpoint,
            mic=mic,
            models=["hey_jarvis"],
            timeout=5.0,
        )

        client.disconnect.assert_awaited_once()

    async def test_disconnect_called_on_error(self, endpoint, mic, patch_tcp_client):
        _, client = patch_tcp_client
        client.read_event = AsyncMock(side_effect=RuntimeError("network"))

        with pytest.raises(RuntimeError, match="network"):
            await probe_wake_detection(
                endpoint=endpoint,
                mic=mic,
                models=["hey_jarvis"],
                timeout=5.0,
            )

        client.disconnect.assert_awaited_once()
