#!/usr/bin/env python3
"""Regenerate optional brand PNG/ICO files with librsvg's rsvg-convert.

This is a development asset utility, not a Tokenograph runtime dependency.
The checked-in SVG/PNG files are usable without running this script.
"""
import shutil
import struct
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPORTS = {
    "tokenograph": (32, 64, 256, 512),
    "tokenograph-mono": (512,),
    "tokenograph-white": (512,),
    "tokenograph-app": (256, 512, 1024),
    "favicon": (16, 32, 48),
}


def main():
    renderer = shutil.which("rsvg-convert")
    if renderer is None:
        raise SystemExit("PNG regeneration needs rsvg-convert (librsvg); existing assets need no tools.")
    for name, sizes in EXPORTS.items():
        for size in sizes:
            out = HERE / f"{name}-{size}.png"
            subprocess.run([renderer, "--width", str(size), "--height", str(size),
                            "--output", str(out), str(HERE / f"{name}.svg")], check=True)
            data = out.read_bytes()
            if data[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", data[16:24]) != (size, size):
                raise SystemExit(f"Invalid rendered PNG: {out}")
            print(out.name)

    # ICO permits PNG payloads. The directory preserves every native favicon size.
    images = [(size, (HERE / f"favicon-{size}.png").read_bytes()) for size in EXPORTS["favicon"]]
    offset = 6 + 16 * len(images)
    directory = []
    for size, image in images:
        directory.append(struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(image), offset))
        offset += len(image)
    ico = struct.pack("<HHH", 0, 1, len(images)) + b"".join(directory) + b"".join(image for _, image in images)
    (HERE / "favicon.ico").write_bytes(ico)
    print("favicon.ico")


if __name__ == "__main__":
    main()
