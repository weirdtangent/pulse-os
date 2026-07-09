#!/usr/bin/env python3
"""Auto-recover the Snapcast client when it is connected but producing no audio.

Background
----------
``snapclient`` streams through PipeWire/Pulse (``--player pulse``). It resolves
the ``default`` sink once, when its player connects. If that sink later
disappears and is re-created with a new index -- e.g. a USB DAC re-enumerates,
or PipeWire restarts -- the player connection goes stale and snapclient can sit
there "connected" to the server while feeding audio to a sink that no longer
exists. The process never crashes, so ``Restart=always`` in the unit does not
help, and the room goes silent until someone restarts the service by hand.

This watchdog closes that gap. Every POLL_INTERVAL seconds it asks the snapserver
whether *this* client is supposed to be playing (stream ``playing`` + client
connected + not muted). If it is, but there is no local snapclient sink-input
feeding audio, it waits SILENT_GRACE seconds (to rule out a normal
stream-startup gap) and then restarts ``pulse-snapclient.service``.

It is deliberately fail-safe: if the snapserver status cannot be fetched, or the
stream is idle, or the client is muted/disconnected, it does nothing. It only
ever acts on the specific "server is playing but we are silent" condition.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

# ── Tunables (override via environment / EnvironmentFile) ────────────────────
POLL_INTERVAL = float(os.environ.get("AUDIO_WATCHDOG_POLL_INTERVAL", "10"))
# Seconds the "server playing but locally silent" state must persist before we
# act. Must comfortably exceed snapclient's own reconnect/stream-startup time so
# we never fight a client that is about to recover on its own.
SILENT_GRACE = float(os.environ.get("AUDIO_WATCHDOG_SILENT_GRACE", "20"))
# Minimum seconds between restarts, so a client that is slow to come back never
# gets thrashed.
COOLDOWN = float(os.environ.get("AUDIO_WATCHDOG_COOLDOWN", "60"))
# Let the system settle before judging anything (boot, first login, PipeWire up).
START_GRACE = float(os.environ.get("AUDIO_WATCHDOG_START_GRACE", "45"))

SNAP_UNIT = "pulse-snapclient.service"
LOG_TAG = "pulse-audio-watchdog"


def log(msg: str) -> None:
    try:
        subprocess.run(["logger", "-t", LOG_TAG, msg], check=False)
    except Exception:
        # Best-effort syslog only; logging must never take down the watchdog.
        # The stderr/journal write below still happens regardless.
        pass
    print(f"[{LOG_TAG}] {msg}", file=sys.stderr, flush=True)


def snap_host_id() -> str:
    hid = os.environ.get("SNAPCLIENT_HOST_ID", "").strip()
    if hid:
        return hid
    # pulse-snapclient.sh defaults --hostID to the short hostname.
    return socket.gethostname().split(".")[0]


def server_status_url() -> str | None:
    host = os.environ.get("SNAPCAST_HOST", "").strip()
    if not host:
        return None
    port = os.environ.get("SNAPCAST_HTTP_PORT", "1780").strip() or "1780"
    return f"http://{host}:{port}/jsonrpc"


def snapclient_active() -> bool:
    rc = subprocess.run(["systemctl", "is-active", "--quiet", SNAP_UNIT], check=False).returncode
    return rc == 0


def fetch_server_status(url: str) -> dict | None:
    payload = json.dumps({"id": 1, "jsonrpc": "2.0", "method": "Server.GetStatus"}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def server_wants_audio(status: dict, host_id: str) -> bool:
    """True iff this client should currently be producing sound.

    That means: the client is present + connected + not muted, and the stream
    its group is bound to is actively ``playing``.
    """
    try:
        server = status["result"]["server"]
    except (KeyError, TypeError):
        return False

    stream_status = {s.get("id"): s.get("status") for s in server.get("streams", [])}

    for group in server.get("groups", []):
        for client in group.get("clients", []):
            if client.get("host", {}).get("name") != host_id:
                continue
            if not client.get("connected"):
                return False
            if client.get("config", {}).get("volume", {}).get("muted"):
                return False
            return stream_status.get(group.get("stream_id")) == "playing"
    return False


def snapclient_is_feeding() -> bool | None:
    """True if snapclient has a live sink-input, False if not, None if unknown.

    None means we could not query PipeWire -- treat as "don't act".
    """
    try:
        out = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    text = out.stdout
    return ('application.process.binary = "snapclient"' in text) or ('application.name = "Snapcast"' in text)


def restart_snapclient() -> None:
    log(f"restarting {SNAP_UNIT}: server reports this client playing but no local audio")
    subprocess.run(["systemctl", "restart", SNAP_UNIT], check=False)


def main() -> int:
    url = server_status_url()
    if not url:
        log("SNAPCAST_HOST not set; audio watchdog has nothing to watch. Exiting.")
        return 0

    host_id = snap_host_id()
    log(
        f"started (host_id={host_id}, server={url}, poll={POLL_INTERVAL}s, grace={SILENT_GRACE}s, cooldown={COOLDOWN}s)"
    )
    time.sleep(START_GRACE)

    silent_since: float | None = None
    last_restart = 0.0

    while True:
        now = time.monotonic()
        try:
            if not snapclient_active():
                silent_since = None  # not our job while the unit is stopped
            else:
                status = fetch_server_status(url)
                if status is None or not server_wants_audio(status, host_id):
                    # Server unreachable, stream idle, muted, or disconnected:
                    # nothing to recover.
                    silent_since = None
                else:
                    feeding = snapclient_is_feeding()
                    if feeding is None or feeding:
                        # Producing audio (or we can't tell) -> healthy.
                        silent_since = None
                    else:
                        if silent_since is None:
                            silent_since = now
                            log("server reports this client playing but no local snapclient audio; watching…")
                        elif (now - silent_since) >= SILENT_GRACE and (now - last_restart) >= COOLDOWN:
                            restart_snapclient()
                            last_restart = now
                            silent_since = None
        except Exception as exc:  # never let the loop die on a transient error
            log(f"unexpected error in watch loop: {exc}")
            silent_since = None

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
