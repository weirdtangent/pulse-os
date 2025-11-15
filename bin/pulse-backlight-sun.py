#!/usr/bin/env python3
import os, time, math
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

from astral import LocationInfo
from astral.sun import sun, dawn, dusk

CONF = "/etc/pulse-backlight.conf"

def read_conf(path):
    cfg = {
        "LAT": None, "LON": None,
        "DAY": "85", "NIGHT": "25",
        "TWILIGHT": "OFFICIAL",
        "BACKLIGHT": "/sys/class/backlight/11-0045",
    }
    with open(path) as f:
        for line in f:
            line=line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k,v = line.split("=",1)
            cfg[k.strip().upper()] = v.strip()
    lat = float(cfg["LAT"]); lon = float(cfg["LON"])
    day = max(0, min(100, int(cfg["DAY"])))
    night = max(0, min(100, int(cfg["NIGHT"])))
    tw = cfg["TWILIGHT"].upper()
    if tw not in ("OFFICIAL","CIVIL","NAUTICAL","ASTRONOMICAL"): tw = "OFFICIAL"
    bl = cfg["BACKLIGHT"]
    return lat, lon, day, night, tw, bl

def detect_tz():
    # Prefer configured zone via /etc/timezone
    tzname = None
    for p in (os.environ.get("TZ"), "/etc/timezone"):
        try:
            if p and os.path.isfile(p):
                with open(p) as f: tzname = f.read().strip()
                break
            elif p and p and p not in ("/etc/timezone",):
                tzname = p
                break
        except Exception:
            pass
    # Try ZoneInfo if available
    if tzname and ZoneInfo:
        try:
            return ZoneInfo(tzname)
        except Exception:
            pass
    # Try system local tz
    try:
        return datetime.now().astimezone().tzinfo or timezone.utc
    except Exception:
        return timezone.utc

def set_backlight(dev, pct):
    maxp = int(open(os.path.join(dev, "max_brightness")).read().strip())
    val  = max(0, min(maxp, math.floor(maxp * pct / 100)))
    with open(os.path.join(dev, "brightness"), "w") as f:
        f.write(str(val))

def next_events(lat, lon, tz, twilight_mode, now=None):
    if now is None: now = datetime.now(tz)
    loc = LocationInfo(latitude=lat, longitude=lon)
    date = now.date()
    def boundaries(d):
        if twilight_mode == "OFFICIAL":
            s = sun(loc.observer, date=d, tzinfo=tz)
            return s["sunrise"], s["sunset"]
        dep = {"CIVIL":6, "NAUTICAL":12, "ASTRONOMICAL":18}[twilight_mode]
        return dawn(loc.observer, date=d, tzinfo=tz, depression=dep), \
               dusk(loc.observer, date=d, tzinfo=tz, depression=dep)
    try:
        sr, ss = boundaries(date)
    except Exception:
        # In extreme latitudes, pick safe times and retry soon
        n1 = now.replace(hour=8, minute=0, second=0, microsecond=0)
        n2 = now.replace(hour=18, minute=0, second=0, microsecond=0)
        return (n1 <= now < n2), (n2 if now < n2 else (n1 + timedelta(days=1)))
    # Determine current state and next change
    if sr <= now < ss:
        return True, ss
    if now < sr:
        return False, sr
    # After sunset -> next sunrise tomorrow
    sr2, _ = boundaries(date + timedelta(days=1))
    return False, sr2

def main():
    lat, lon, day, night, twilight, bl = read_conf(CONF)
    tz = detect_tz()
    last_state = None
    while True:
        now = datetime.now(tz)
        is_day, change = next_events(lat, lon, tz, twilight, now)
        target = day if is_day else night
        try:
            if last_state != is_day:
                set_backlight(bl, target)
                last_state = is_day
        except Exception:
            # Backlight not ready? try again soon
            pass
        # Sleep until just after the next change (clamped)
        sleep_s = max(30, min(24*3600, (change - now).total_seconds() + 2))
        time.sleep(sleep_s)

if __name__ == "__main__":
    main()
