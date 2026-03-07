#!/usr/bin/env python3
"""
Generate the Omni DMG background image.
660×400 points @2x = 1320×800 px — dark with violet/blue spotlight glows.
Pure stdlib, numpy fast-path if available.
"""
import sys
import struct
import zlib
import math

W, H = 1320, 800   # @2x retina

# Brand colours (Omni's palette)
BG_R, BG_G, BG_B   = 10, 10, 13      # slightly darker than #0D0D10

# Left spotlight  — violet  (#8B5CF6) — sits under the app icon
LX, LY   = W * 0.273, H * 0.46
L_R, L_G, L_B = 139, 92, 246
L_RADIUS = W * 0.32
L_ALPHA  = 0.22

# Right spotlight — indigo-blue (#3B82F6) — sits under the Applications arrow
RX, RY   = W * 0.727, H * 0.46
R_R, R_G, R_B = 59, 130, 246
R_RADIUS = W * 0.30
R_ALPHA  = 0.17

# Vignette strength (darken edges)
VIG = 0.30


def _write_png(rgb_rows: bytes, path: str):
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(rgb_rows, 1))
    iend = chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend)


def _fast(path: str):
    import numpy as np

    xv = np.linspace(0, W - 1, W, dtype=np.float32)
    yv = np.linspace(0, H - 1, H, dtype=np.float32)
    xg, yg = np.meshgrid(xv, yv)

    def spotlight(cx, cy, radius, alpha):
        d = np.sqrt((xg - cx) ** 2 + (yg - cy) ** 2)
        return np.maximum(0.0, 1.0 - d / radius) ** 2.0 * alpha

    li = spotlight(LX, LY, L_RADIUS, L_ALPHA)
    ri = spotlight(RX, RY, R_RADIUS, R_ALPHA)

    # Vignette
    ex = (xg / W - 0.5) * 2
    ey = (yg / H - 0.5) * 2
    vig = np.clip(1.0 - (ex * ex + ey * ey) * VIG, 0.4, 1.0)

    r = np.clip((BG_R + L_R * li + R_R * ri) * vig, 0, 255).astype(np.uint8)
    g = np.clip((BG_G + L_G * li + R_G * ri) * vig, 0, 255).astype(np.uint8)
    b = np.clip((BG_B + L_B * li + R_B * ri) * vig, 0, 255).astype(np.uint8)

    # Interleave into filter-byte-prefixed rows
    rows = np.zeros((H, 1 + W * 3), dtype=np.uint8)
    rows[:, 0] = 0          # filter byte: None
    rows[:, 1::3] = r
    rows[:, 2::3] = g
    rows[:, 3::3] = b

    _write_png(rows.tobytes(), path)


def _slow(path: str):
    def clamp(v):
        return max(0, min(255, int(v)))

    buf = bytearray()
    for y in range(H):
        buf.append(0)  # filter byte
        for x in range(W):
            ld = math.sqrt((x - LX) ** 2 + (y - LY) ** 2)
            li = max(0.0, 1.0 - ld / L_RADIUS) ** 2.0 * L_ALPHA

            rd = math.sqrt((x - RX) ** 2 + (y - RY) ** 2)
            ri = max(0.0, 1.0 - rd / R_RADIUS) ** 2.0 * R_ALPHA

            ex = (x / W - 0.5) * 2
            ey = (y / H - 0.5) * 2
            vig = max(0.4, min(1.0, 1.0 - (ex * ex + ey * ey) * VIG))

            buf.append(clamp((BG_R + L_R * li + R_R * ri) * vig))
            buf.append(clamp((BG_G + L_G * li + R_G * ri) * vig))
            buf.append(clamp((BG_B + L_B * li + R_B * ri) * vig))

    _write_png(bytes(buf), path)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "background.png"
    try:
        _fast(out)
        print(f"[bg] numpy fast-path → {out}")
    except ImportError:
        print("[bg] numpy not available, using pure Python (may be slow)…")
        _slow(out)
        print(f"[bg] done → {out}")


if __name__ == "__main__":
    main()
