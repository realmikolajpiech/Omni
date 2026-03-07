#!/usr/bin/env python3
"""
Generate the Omni DMG background image.
660×420 pt @2x = 1320×840 px
Premium dark design: deep violet glow + dot grid + smooth bezier arrow.
Requires: Pillow, numpy (both available in the build environment).
"""
import sys
import math

# ── Canvas ────────────────────────────────────────────────────────────────────
W, H = 1320, 840   # @2x retina (660×420 pt window)

# ── Icon centres in @2x pixels (mirrors create_dmg.sh layout) ────────────────
# APP_X=178, APP_Y=192 in pt  →  @2x = (356, 384)
# APPS_X=482, APPS_Y=192 in pt →  @2x = (964, 384)
APP_CX,  APP_CY  = 356, 384
APPS_CX, APPS_CY = 964, 384

# ── Palette ───────────────────────────────────────────────────────────────────
BG          = (8,   8,  14)    # deep near-black, very slight blue tint
VIOLET      = (139, 92, 246)   # #8B5CF6 — Omni brand violet
INDIGO      = (99,  102, 241)  # #6366F1 — supporting indigo


def _build_base(xg, yg):
    """Numpy gradient: dark BG + two radial glows + vignette → RGBA ndarray."""
    import numpy as np

    def radial(cx, cy, r, exp=1.8):
        d = np.sqrt((xg - cx) ** 2 + (yg - cy) ** 2)
        return np.clip(1.0 - d / r, 0, 1) ** exp

    li = radial(APP_CX,  APP_CY,  520)   # wide violet glow
    ri = radial(APPS_CX, APPS_CY, 480)   # wide indigo glow

    # Strong violet on left, strong indigo on right
    V_A, I_A = 0.58, 0.48
    # Light vignette — keeps edges dark without crushing the glow areas
    ex = (xg / W - 0.5) * 2
    ey = (yg / H - 0.5) * 2
    vig = np.clip(1.0 - (ex ** 2 + ey ** 2) * 0.25, 0.50, 1.0)

    r = np.clip((BG[0] + VIOLET[0] * li * V_A + INDIGO[0] * ri * I_A) * vig, 0, 255)
    g = np.clip((BG[1] + VIOLET[1] * li * V_A + INDIGO[1] * ri * I_A) * vig, 0, 255)
    b = np.clip((BG[2] + VIOLET[2] * li * V_A + INDIGO[2] * ri * I_A) * vig, 0, 255)

    rgb = np.stack([r, g, b], axis=2).astype(np.uint8)
    alpha = np.full((H, W, 1), 255, dtype=np.uint8)
    return np.concatenate([rgb, alpha], axis=2)


def _dot_grid(draw):
    """Very subtle white dot grid — premium tech-tool aesthetic."""
    SPACING = 40
    R       = 1
    COLOR   = (255, 255, 255, 14)
    for gy in range(SPACING // 2, H, SPACING):
        for gx in range(SPACING // 2, W, SPACING):
            draw.ellipse([gx - R, gy - R, gx + R, gy + R], fill=COLOR)


def _bezier(p0, p1, p2, n=300):
    """Quadratic bezier — returns list of (x, y) float tuples."""
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        pts.append((x, y))
    return pts


def _draw_arrow(layer, draw):
    """
    Smooth bezier arrow from the app icon to Applications.
    Three-layer paint: wide violet glow → mid glow → crisp white core → head.
    """
    # Icon half-width at 128pt @2x = 256px.  Start/end just outside icon edge.
    GAP = 108
    p0 = (APP_CX  + GAP, APP_CY + 4)    # starts right of app icon
    p1 = (W // 2,        APP_CY - 95)   # control point — gentle arc upward
    p2 = (APPS_CX - GAP, APP_CY + 4)   # ends left of Applications icon

    pts = _bezier(p0, p1, p2)
    segs = list(zip(pts, pts[1:]))

    # Outer violet aura (wide + very soft)
    for a, b in segs:
        draw.line([a, b], fill=(180, 140, 255, 22), width=28)
    # Inner glow
    for a, b in segs:
        draw.line([a, b], fill=(220, 190, 255, 45), width=12)
    # Core white line
    for a, b in segs:
        draw.line([a, b], fill=(255, 255, 255, 210), width=5)

    # ── Arrowhead ────────────────────────────────────────────────────────────
    # Tangent at t=1 for quadratic bezier = 2*(p2 - p1)
    tx = 2 * (p2[0] - p1[0])
    ty = 2 * (p2[1] - p1[1])
    mag = math.sqrt(tx ** 2 + ty ** 2)
    dx, dy = tx / mag, ty / mag   # forward direction
    px, py = -dy, dx              # perpendicular

    HEAD_LEN, HEAD_W = 38, 20
    tip = p2
    bx = tip[0] - dx * HEAD_LEN
    by = tip[1] - dy * HEAD_LEN
    draw.polygon(
        [tip, (bx + px * HEAD_W, by + py * HEAD_W),
              (bx - px * HEAD_W, by - py * HEAD_W)],
        fill=(255, 255, 255, 225),
    )


def generate(path: str):
    import numpy as np
    from PIL import Image, ImageDraw

    xv = np.arange(W, dtype=np.float32)
    yv = np.arange(H, dtype=np.float32)
    xg, yg = np.meshgrid(xv, yv)

    # Base gradient
    base_arr = _build_base(xg, yg)
    img = Image.fromarray(base_arr, "RGBA")

    # Dot grid layer
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _dot_grid(ImageDraw.Draw(grid))
    img = Image.alpha_composite(img, grid)

    # Arrow layer
    arrow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _draw_arrow(arrow, ImageDraw.Draw(arrow))
    img = Image.alpha_composite(img, arrow)

    img.convert("RGB").save(path, "PNG")
    print(f"[bg] {W}×{H} → {path}")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "background.png"
    generate(out)


if __name__ == "__main__":
    main()
