"""Minimal PNG decoder (8-bit RGB/RGBA/gray, all five filter types) for QA."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path


class Image:
    def __init__(self, width: int, height: int, rgb: bytes):
        self.width, self.height, self.rgb = width, height, rgb

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        i = (y * self.width + x) * 3
        return self.rgb[i], self.rgb[i + 1], self.rgb[i + 2]


def read_png(path: Path) -> Image:
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG")
    pos, idat = 8, b""
    width = height = depth = ctype = interlace = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if depth != 8 or interlace != 0 or ctype not in (0, 2, 6):
        raise ValueError(f"{path}: unsupported PNG (depth={depth}, color={ctype}, interlace={interlace})")
    bpp = {0: 1, 2: 3, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = width * bpp
    out = bytearray(height * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(height):
        f = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif f != 0:
            raise ValueError(f"{path}: bad filter {f}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    if ctype == 2:
        rgb = bytes(out)
    elif ctype == 6:
        rgb = bytes(b for i in range(0, len(out), 4) for b in out[i:i + 3])
    else:
        rgb = bytes(b for g in out for b in (g, g, g))
    return Image(width, height, rgb)
