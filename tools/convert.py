#!/usr/bin/env python3
from pathlib import Path
from PIL import Image

SOURCE = Path("assets/graystorm-pulse_splash.png")  # adjust filename
TARGET = Path("assets/boot-splash.rgb")             # or wherever you need it

with Image.open(SOURCE) as img:
    img = img.convert("RGB").resize((1280, 720), Image.Resampling.LANCZOS)
    # each pixel → 2 bytes: 5 bits red, 6 green, 5 blue
    data = bytearray()
    for r, g, b in img.getdata():
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        data.extend(divmod(rgb565, 256)[::-1])
    TARGET.write_bytes(data)
    print(f"Wrote {TARGET} ({len(data)} bytes)")
