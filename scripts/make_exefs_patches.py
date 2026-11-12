#!/usr/bin/env python3
"""Build the OoT native-4K IPS32 and pchtxt from the builtin patch records.

Offsets in the builtin records (src/core/file_sys/patch_manager.cpp, commit
a23a737) are into the NSO text, after the 0x100-byte NSO0 header. Fritchii
applies an exefs IPS to the whole NSO file, so IPS32 offsets are
module_offset + 0x100. IPSwitch pchtxt uses the module offsets plus
`@flag offset_shift 0x100`, which is the convention in Fritchii's own tests.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

TITLE_ID = "0400486024DEC000"
BUILD_ID = "E0F3CCFAF50065AE4EA01EB5AEB13B1A45DA3348"
NSO_HEADER = 0x100

# (module_offset, original_word, new_word, comment)
# Words are ARM little-endian instruction encodings, written here as the
# usual big-endian hex (the same form IPSwitch / pchtxt uses).
RECORDS = (
    (0x1202EE0, 0xD2814008, 0xD281E008, "picture 2560 -> 3840 (site 1)"),
    (0x1202EE4, 0xF2C0B408, 0xF2C10E08, "picture 1440 -> 2160 (site 1)"),
    (0x31F39F4, 0xD2814008, 0xD281E008, "picture 2560 -> 3840 (site 2)"),
    (0x31F39F8, 0xF2C0B408, 0xF2C10E08, "picture 1440 -> 2160 (site 2)"),
    (0x31F3A00, 0xD280F008, 0xD2816808, "scene 1920 -> 2880"),
    (0x31F3A04, 0xF2C08708, 0xF2C0CA88, "scene 1080 -> 1620"),
    (0x80641C, 0x9A888128, 0xAA1F03E8, "csel x8, x9, x8, hi -> mov x8, xzr"),
    (0x806420, 0x52A7F009, 0x52A7F809, "scene scale 1.0f -> 1.5f"),
)


def word_le(word: int) -> bytes:
    return struct.pack("<I", word)


def build_ips32() -> bytes:
    out = bytearray(b"IPS32")
    for module_offset, _original, new, _comment in RECORDS:
        ips_offset = module_offset + NSO_HEADER
        data = word_le(new)
        out += struct.pack(">I", ips_offset)
        out += struct.pack(">H", len(data))
        out += data
    out += b"EEOF"
    return bytes(out)


def build_pchtxt() -> str:
    lines = [
        f"@nsobid-{BUILD_ID}",
        "# Ocarina of Time native 4K (picture size + scene scale)",
        f"# Title {TITLE_ID}, build id {BUILD_ID}",
        "# Offsets are into main's text (after the NSO header).",
        "# @flag offset_shift 0x100 matches Fritchii/yuzu IPSwitch: the patch",
        "# is applied to the whole NSO file, so each offset is moved by the",
        "# 0x100-byte NSO0 header. Hex words are ARM encodings in the usual",
        "# most-significant-byte-first form; Fritchii stores them little-endian.",
        "#",
        "# Max Resolution (main+0x805BD0 tbz -> b) is left out: Fritchii already",
        "# ships it as a built-in. Keep that add-on on. This patch does not",
        "# include the HeapDef growth (romfs) or the +400 MiB application pool.",
        "@flag offset_shift 0x100",
        "",
        "@enabled",
    ]
    for module_offset, original, new, comment in RECORDS:
        lines.append(
            f"{module_offset:08X} {new:08X}  # {comment}; was {original:08X}"
        )
    lines.append("@stop")
    lines.append("")
    return "\n".join(lines)


def parse_ips32(data: bytes) -> list[tuple[int, bytes]]:
    if not data.startswith(b"IPS32") or not data.endswith(b"EEOF"):
        raise ValueError("not a complete IPS32 (IPS32 ... EEOF)")
    records = []
    offset = 5
    end = len(data) - 4
    while offset < end:
        ips_offset = struct.unpack_from(">I", data, offset)[0]
        size = struct.unpack_from(">H", data, offset + 4)[0]
        offset += 6
        if size == 0:
            raise ValueError("unexpected RLE record")
        records.append((ips_offset, data[offset : offset + size]))
        offset += size
    if offset != end:
        raise ValueError("IPS32 records do not end on EEOF")
    return records


def verify_ips32(data: bytes) -> None:
    got = parse_ips32(data)
    expected = [
        (module_offset + NSO_HEADER, word_le(new))
        for module_offset, _original, new, _comment in RECORDS
    ]
    if got != expected:
        raise SystemExit(f"IPS32 mismatch:\n  got {got!r}\n  expected {expected!r}")
    # Highest offset is past 24-bit IPS range, so IPS32 is required.
    if any(off > 0xFFFFFF for off, _ in expected) is False:
        raise SystemExit("internal error: expected at least one IPS32-only offset")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="package root (default: parent of scripts/)",
    )
    args = parser.parse_args()
    root = args.out_dir
    exefs = root / "load" / TITLE_ID / "OoT-4K" / "exefs"
    patches = root / "patches"
    exefs.mkdir(parents=True, exist_ok=True)
    patches.mkdir(parents=True, exist_ok=True)

    ips = build_ips32()
    verify_ips32(ips)
    ips_path = exefs / f"{BUILD_ID}.ips"
    ips_path.write_bytes(ips)
    pchtxt = build_pchtxt()
    pchtxt_path = patches / "oot_4k.pchtxt"
    pchtxt_path.write_text(pchtxt, encoding="utf-8")
    print(f"wrote {ips_path} ({len(ips)} bytes, {len(RECORDS)} records)")
    print(f"wrote {pchtxt_path}")


if __name__ == "__main__":
    main()
