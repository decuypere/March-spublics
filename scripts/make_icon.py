#!/usr/bin/env python3
"""Genere l'icone de l'application macOS, sans dependance externe.

Dessine un pictogramme d'architecture (arche sur socle) en PNG 1024x1024.
Le script de construction le convertit ensuite en .icns via sips/iconutil.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

SIZE = 1024
BG = (22, 50, 79)        # bleu ardoise
INK = (245, 247, 250)    # blanc casse
ACCENT = (110, 168, 232)  # bleu clair


def rounded_rect(x: float, y: float, w: float, h: float, r: float) -> callable:
    """Rectangle a coins arrondis: le point doit etre a moins de r du
    rectangle interieur (distance nulle des qu'on est dedans)."""
    r = min(r, w / 2, h / 2)
    inner_x0, inner_x1 = x + r, x + w - r
    inner_y0, inner_y1 = y + r, y + h - r

    def inside(px: float, py: float) -> bool:
        nearest_x = min(max(px, inner_x0), inner_x1)
        nearest_y = min(max(py, inner_y0), inner_y1)
        return (px - nearest_x) ** 2 + (py - nearest_y) ** 2 <= r * r
    return inside


def build_pixels() -> bytearray:
    s = SIZE
    card = rounded_rect(0, 0, s, s, s * 0.22)

    # Socle, colonnes et arche du pictogramme
    base = rounded_rect(s * 0.20, s * 0.70, s * 0.60, s * 0.075, s * 0.02)
    left = rounded_rect(s * 0.255, s * 0.42, s * 0.085, s * 0.28, s * 0.015)
    right = rounded_rect(s * 0.66, s * 0.42, s * 0.085, s * 0.28, s * 0.015)
    roof_apex = (s * 0.50, s * 0.20)
    roof_left = (s * 0.20, s * 0.40)
    roof_right = (s * 0.80, s * 0.40)

    def in_triangle(px: float, py: float) -> bool:
        (ax, ay), (bx, by), (cx, cy) = roof_apex, roof_left, roof_right
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if d == 0:
            return False
        a = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
        b = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
        return a >= 0 and b >= 0 and (a + b) <= 1

    cx, cy, rr = s * 0.50, s * 0.585, s * 0.115

    rows = bytearray()
    for y in range(s):
        rows.append(0)  # filtre PNG "None"
        py = y + 0.5
        for x in range(s):
            px = x + 0.5
            if not card(px, py):
                rows.extend((0, 0, 0, 0))
                continue
            color = BG
            if in_triangle(px, py):
                color = INK
            elif base(px, py) or left(px, py) or right(px, py):
                color = INK
            elif (px - cx) ** 2 + (py - cy) ** 2 <= rr * rr and py <= s * 0.70:
                color = ACCENT
            rows.extend((*color, 255))
    return rows


def write_png(path: Path, raw: bytearray) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_png(target, build_pixels())
    print(f"Icone ecrite: {target} ({target.stat().st_size // 1024} Ko)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
