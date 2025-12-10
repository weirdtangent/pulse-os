#!/usr/bin/env python3
"""Test Home Assistant WebSocket Assist API connection."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import ssl
import subprocess
import sys
from pathlib import Path

try:
    import websockets
except ImportError:
    print("ERROR: websockets library not installed. Run: pip install websockets")
    sys.exit(1)

MODULE_ROOT = Path(__file__).resolve().parents[2]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from pulse.assistant.config import AssistantConfig  # noqa: E402


def load_env_from_config(config_path: Path | None) -> dict[str, str]:
    """Source pulse.conf in a subshell and merge the exported variables into a dict."""
    env = os.environ.copy()
    if config_path is None:
        return env

    command = f"set -a; source {shlex.quote(str(config_path))}; env -0"

    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Failed to source {config_path}: {exc.stderr.decode('utf-8', errors='ignore')}") from exc

    stdout = proc.stdout
    for entry in stdout.split(b"\0"):
        if not entry:
            continue
        if b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        env[key.decode("utf-8")] = value.decode("utf-8")

    return env


async def test_websocket_connection():
    """Test WebSocket connection to Home Assistant Assist API."""
    # Load config
    config_file = Path("/opt/pulse-os/pulse.conf")
    if not config_file.exists():
        config_file = MODULE_ROOT / "pulse.conf.sample"
        print(f"Using sample config: {config_file}")

    env = load_env_from_config(config_file)
    config = AssistantConfig.from_env(env)
    ha_config = config.home_assistant

    if not ha_config.base_url:
        print("ERROR: HOME_ASSISTANT_BASE_URL not set")
        return False

    if not ha_config.token:
        print("ERROR: HOME_ASSISTANT_TOKEN not set")
        return False

    if not ha_config.assist_pipeline:
        print("WARNING: HOME_ASSISTANT_ASSIST_PIPELINE not set (optional for testing)")

    base_url = ha_config.base_url.rstrip("/")
    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_uri = f"{ws_url}/api/websocket"

    print(f"Connecting to: {ws_uri}")
    print(f"Pipeline: {ha_config.assist_pipeline or '(not configured)'}")

    # Build SSL context if needed
    ssl_context = None
    if ws_url.startswith("wss://"):
        ssl_context = ssl.create_default_context()
        if not ha_config.verify_ssl:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

    try:
        async with websockets.connect(ws_uri, ssl=ssl_context) as ws:
            print("✓ WebSocket connected")

            # Authenticate
            auth_msg_raw = await ws.recv()
            auth_msg = json.loads(auth_msg_raw)
            print(f"Received: {json.dumps(auth_msg, indent=2)}")

            if auth_msg.get("type") != "auth_required":
                print(f"ERROR: Expected auth_required, got: {auth_msg.get('type')}")
                return False

            await ws.send(json.dumps({"type": "auth", "access_token": ha_config.token}))
            print("✓ Sent authentication")

            auth_result_raw = await ws.recv()
            auth_result = json.loads(auth_result_raw)
            print(f"Received: {json.dumps(auth_result, indent=2)}")

            if auth_result.get("type") != "auth_ok":
                print(f"ERROR: Authentication failed: {auth_result}")
                return False

            print("✓ Authentication successful")

            # Test assist_pipeline/run command (with minimal payload)
            if ha_config.assist_pipeline:
                print(f"\nLooking up pipeline ID for: {ha_config.assist_pipeline}")
                # First, list all pipelines to find the ID
                list_payload = {"id": 1, "type": "assist_pipeline/pipeline/list"}
                await ws.send(json.dumps(list_payload))
                print("✓ Sent pipeline list request")

                # Wait for the list response
                pipeline_id = None
                try:
                    list_response_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    list_response = json.loads(list_response_raw)
                    print(f"Received: {json.dumps(list_response, indent=2)}")

                    if list_response.get("type") == "result" and list_response.get("success"):
                        result_data = list_response.get("result", {})
                        # The result contains a "pipelines" key with the list
                        pipelines = result_data.get("pipelines", []) if isinstance(result_data, dict) else []
                        pipeline_name = ha_config.assist_pipeline
                        for pipeline in pipelines:
                            if isinstance(pipeline, dict) and pipeline.get("name") == pipeline_name:
                                pipeline_id = pipeline.get("id")
                                print(f"✓ Found pipeline ID: {pipeline_id}")
                                break
                        if not pipeline_id:
                            print(f"ERROR: Pipeline '{pipeline_name}' not found in list")
                            pipeline_names = [p.get("name") for p in pipelines if isinstance(p, dict) and p.get("name")]
                            print(f"Available pipelines: {pipeline_names}")
                            return False
                    else:
                        error = list_response.get("error", {})
                        print(f"ERROR: Failed to list pipelines: {error}")
                        return False
                except TimeoutError:
                    print("ERROR: Timeout waiting for pipeline list")
                    return False

                print(f"\nTesting assist_pipeline/run with pipeline ID: {pipeline_id}")
                # WebSocket API requires an 'id' field for commands
                run_payload = {
                    "id": 2,
                    "type": "assist_pipeline/run",
                    "start_stage": "intent",
                    "end_stage": "tts",
                    "input": {
                        "text": "test",
                    },
                    "pipeline": pipeline_id,
                }

                await ws.send(json.dumps(run_payload))
                print("✓ Sent assist_pipeline/run command")

                # Wait for response (with timeout)
                # Note: assist_pipeline/run sends events, not direct responses
                # We'll wait for the run-start event
                try:
                    # Read messages until we get a run-start event or timeout
                    for _ in range(10):  # Try up to 10 messages
                        response_raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                        response = json.loads(response_raw)
                        print(f"Received: {json.dumps(response, indent=2)}")

                        if response.get("type") == "event":
                            event = response.get("event", {})
                            if event.get("type") == "run-start":
                                print("✓ Pipeline run started successfully!")
                                return True
                            elif event.get("type") == "error":
                                error_data = event.get("data", {})
                                print(f"ERROR: Pipeline error: {error_data}")
                                return False
                        elif response.get("type") == "result":
                            # Some commands return results directly
                            if response.get("success"):
                                print("✓ Pipeline command accepted!")
                                return True
                            else:
                                error = response.get("error", {})
                                print(f"ERROR: Command failed: {error}")
                                return False
                except TimeoutError:
                    print("WARNING: No response within timeout (pipeline may still be processing)")
                    return True  # Connection works, just no immediate response
            else:
                print("\nNo pipeline configured - skipping assist_pipeline/run test")
                print("✓ WebSocket connection and authentication verified")
                return True

    except websockets.exceptions.InvalidURI as exc:
        print(f"ERROR: Invalid WebSocket URI: {exc}")
        return False
    except websockets.exceptions.InvalidStatusCode as exc:
        print(f"ERROR: WebSocket connection failed: {exc}")
        return False
    except Exception as exc:
        print(f"ERROR: {exc}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(test_websocket_connection())
    sys.exit(0 if success else 1)
