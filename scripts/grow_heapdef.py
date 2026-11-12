#!/usr/bin/env python3
"""Grow Ocarina of Time HeapDef.byml.zs files for a native 4K picture.

Ports the logic in fritchii src/core/file_sys/oot_4k.cpp (commit a23a737):
DynamicTextureAllocatorMem and its parent Graphics heap each grow by
ceil(DTA * (2.25 - 1) / 1024) * 1024 KiB. For the shipped 312320 KiB DTA
that is +391168 KiB, giving 703488 and 1252352 KiB, as logged in the
working in-emulator 4K run.

The box has no game dump. Run this on the PC against a RomFS dump
(Fritchii: right-click the game -> Dump RomFS -> Dump RomFS -> Full),
without EDEN_NX2_OOT_4K=1, then copy the grown files into the OoT-4K
mod's romfs folder.

Requires the `zstandard` package (`pip install zstandard`) when a HeapDef
uses a zstd dictionary from Pack/ZsDic.pack.zs, which this game's files
typically do.
"""

from __future__ import annotations

import argparse
import math
import shutil
import struct
import sys
from pathlib import Path
from typing import Iterable, Optional

TITLE_ID = "0400486024DEC000"
PIXEL_RATIO = (3840.0 * 2160.0) / (2560.0 * 1440.0)  # 2.25
ZSTD_DICT_MAGIC = 0xEC30A437
MAX_BLOB = 64 * 1024 * 1024
# Logged in the working in-emulator run (a23a737, Docked).
KNOWN_DTA = 312320
KNOWN_DTA_GROWN = 703488
KNOWN_GFX = 861184
KNOWN_GFX_GROWN = 1252352

COMPRESS_LEVEL = 9


class HeapDefError(Exception):
    pass


def extra_kib(shipped: int) -> int:
    """Whole-MiB extra: ceil(shipped * (ratio-1) / 1024) * 1024 KiB."""
    if shipped <= 0:
        return 0
    grow = PIXEL_RATIO - 1.0
    return int(math.ceil(shipped * grow / 1024.0) * 1024.0)


def u16_le(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 2], "little")


def u16_be(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 2], "big")


def u32_le(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 4], "little")


def u32_be(data: bytes, at: int) -> int:
    return int.from_bytes(data[at : at + 4], "big")


# --- SARC (ZsDic.pack) -----------------------------------------------------

def parse_sarc(data: bytes) -> list[tuple[str, bytes]]:
    if len(data) < 0x14 or data[:4] != b"SARC":
        raise HeapDefError("ZsDic.pack is not a SARC")
    big = u16_le(data, 6) == 0xFFFE
    u16_at = u16_be if big else u16_le
    u32_at = u32_be if big else u32_le
    header = u16_at(data, 4)
    data_offset = u32_at(data, 12)
    if header < 0x14 or data_offset > len(data):
        raise HeapDefError("invalid SARC header")
    sfat = header
    if sfat + 8 > len(data) or data[sfat : sfat + 4] != b"SFAT":
        raise HeapDefError("SARC has no SFAT")
    count = u16_at(data, sfat + 6)
    nodes = sfat + u16_at(data, sfat + 4)
    sfnt = nodes + count * 0x10
    if sfnt + 8 > len(data) or data[sfnt : sfnt + 4] != b"SFNT":
        raise HeapDefError("SARC has no SFNT")
    names = sfnt + u16_at(data, sfnt + 4)
    members: list[tuple[str, bytes]] = []
    for i in range(count):
        node = nodes + i * 0x10
        if node + 0x10 > len(data):
            raise HeapDefError("truncated SARC")
        flags = u32_at(data, node + 4)
        start = u32_at(data, node + 8)
        end = u32_at(data, node + 12)
        if end < start or data_offset + end > len(data):
            raise HeapDefError("SARC member out of range")
        name = ""
        if (flags >> 24) != 0:
            at = names + (flags & 0xFFFFFF) * 4
            if at < len(data):
                limit = min(len(data), data_offset)
                zero = data.find(b"\0", at, limit)
                name = data[at : zero if zero >= 0 else limit].decode("utf-8", "replace")
        members.append((name, data[data_offset + start : data_offset + end]))
    return members


# --- zstd + dictionaries ---------------------------------------------------

def _zstd_module():
    try:
        import zstandard
    except ImportError as exc:
        raise HeapDefError(
            "This HeapDef is zstd-compressed and typically needs a dictionary "
            "from Pack/ZsDic.pack.zs. Install the Python package with:\n"
            "  pip install --user zstandard\n"
            "and run this script again."
        ) from exc
    return zstandard


class ZstdDicts:
    def __init__(self) -> None:
        self._zstd = None
        self.ddicts: dict[int, object] = {}
        self.cdicts: dict[int, object] = {}
        self.raw: dict[int, bytes] = {}

    def _mod(self):
        if self._zstd is None:
            self._zstd = _zstd_module()
        return self._zstd

    def add(self, dictionary: bytes) -> None:
        zstd = self._mod()
        dict_id = zstd.ZstdCompressionDict(dictionary).dict_id()
        if dict_id == 0 or dict_id in self.ddicts:
            return
        cdict = zstd.ZstdCompressionDict(dictionary)
        self.raw[dict_id] = dictionary
        self.ddicts[dict_id] = cdict
        self.cdicts[dict_id] = cdict

    def frame_dict_id(self, frame: bytes) -> int:
        zstd = self._mod()
        try:
            return int(zstd.get_frame_parameters(frame).dict_id)
        except Exception:
            return 0

    def decompress(self, frame: bytes, name: str) -> bytes:
        zstd = self._mod()
        dict_id = self.frame_dict_id(frame)
        dctx = zstd.ZstdDecompressor(
            dict_data=self.ddicts.get(dict_id) if dict_id else None
        )
        if dict_id and dict_id not in self.ddicts:
            raise HeapDefError(
                f"{name} needs zstd dictionary {dict_id} and Pack/ZsDic.pack.zs "
                "did not provide it"
            )
        try:
            return dctx.decompress(frame, max_output_size=MAX_BLOB)
        except Exception as exc:
            raise HeapDefError(f"failed to decompress {name}: {exc}") from exc

    def compress(self, raw: bytes, dict_id: int, name: str) -> bytes:
        zstd = self._mod()
        if dict_id:
            cdict = self.cdicts.get(dict_id)
            if cdict is None:
                raise HeapDefError(f"no compressor dictionary {dict_id} for {name}")
            cctx = zstd.ZstdCompressor(level=COMPRESS_LEVEL, dict_data=cdict)
        else:
            cctx = zstd.ZstdCompressor(level=COMPRESS_LEVEL)
        try:
            return cctx.compress(raw)
        except Exception as exc:
            raise HeapDefError(f"failed to recompress {name}: {exc}") from exc


def load_zsdic(romfs: Path, dicts: ZstdDicts) -> Optional[Path]:
    pack = _find_child(romfs, "Pack") or _find_child(romfs, "pack")
    if pack is None:
        return None
    zsdic = _find_file(pack, "ZsDic.pack.zs") or _find_file(pack, "zsdic.pack.zs")
    if zsdic is None:
        return None
    packed = zsdic.read_bytes()
    # The dictionary pack itself is a plain zstd frame (no dict).
    zstd = _zstd_module()
    try:
        sarc = zstd.ZstdDecompressor().decompress(packed, max_output_size=MAX_BLOB)
    except Exception as exc:
        raise HeapDefError(f"failed to decompress {zsdic}: {exc}") from exc
    for _name, member in parse_sarc(sarc):
        if len(member) >= 4 and u32_le(member, 0) == ZSTD_DICT_MAGIC:
            dicts.add(member)
    return zsdic


# --- BYML (little-endian "YB" only) ----------------------------------------

def _key_name(buf: bytearray, keys: int, index: int) -> Optional[str]:
    if keys + 4 > len(buf):
        return None
    head = u32_le(buf, keys)
    if (head & 0xFF) != 0xC2 or index >= (head >> 8):
        return None
    at = u32_le(buf, keys + 4 + 4 * index)
    start = keys + at
    if start >= len(buf):
        return None
    zero = buf.find(b"\0", start)
    if zero < 0:
        return None
    return bytes(buf[start:zero]).decode("utf-8", "replace")


def _field(buf: bytearray, node: int, name: str, typ: int) -> int:
    """Offset of the value for `name` in a 0xC1 hash, or 0."""
    if node + 4 > len(buf):
        return 0
    head = u32_le(buf, node)
    if (head & 0xFF) != 0xC1:
        return 0
    keys = u32_le(buf, 4)
    for i in range(head >> 8):
        entry_at = node + 4 + 8 * i
        if entry_at + 4 > len(buf):
            return 0
        entry = u32_le(buf, entry_at)
        key = _key_name(buf, keys, entry & 0xFFFFFF)
        if key == name:
            return (entry_at + 4) if (entry >> 24) == typ else 0
    return 0


def _heap_size_field(buf: bytearray, heaps: int, heap: str) -> int:
    at = _field(buf, heaps, heap, 0xC1)
    if not at:
        return 0
    node = u32_le(buf, at)
    return _field(buf, node, "Size", 0xD1)


def grow_heapdef_byml(byml: bytes, name: str) -> tuple[bytes, dict]:
    if len(byml) < 16 or byml[:2] != b"YB":
        raise HeapDefError(f"{name} is not a little-endian BYML")
    ver = u16_le(byml, 2)
    if ver < 2 or ver > 7:
        raise HeapDefError(f"{name} has an unsupported BYML header (version {ver})")
    buf = bytearray(byml)
    root = u32_le(buf, 12)
    heaps_at = _field(buf, root, "Heaps", 0xC1)
    if not heaps_at:
        raise HeapDefError(f"{name} has no Heaps table")
    heaps = u32_le(buf, heaps_at)
    dta_at = _heap_size_field(buf, heaps, "DynamicTextureAllocatorMem")
    gfx_at = _heap_size_field(buf, heaps, "Graphics")
    if not dta_at or not gfx_at:
        raise HeapDefError(
            f"{name} has no DynamicTextureAllocatorMem / Graphics sizes"
        )
    dta = struct.unpack_from("<i", buf, dta_at)[0]
    gfx = struct.unpack_from("<i", buf, gfx_at)[0]
    info = {"name": name, "dta": dta, "gfx": gfx, "skipped": False, "reason": ""}

    if dta == KNOWN_DTA_GROWN and gfx == KNOWN_GFX_GROWN:
        info["skipped"] = True
        info["reason"] = "already grown to the known 4K sizes; leaving as-is"
        info["dta2"] = dta
        info["gfx2"] = gfx
        info["extra"] = 0
        return bytes(buf), info

    extra = extra_kib(dta)
    if extra <= 0:
        raise HeapDefError(f"{name} DynamicTextureAllocatorMem size {dta} is not usable")
    dta2 = dta + extra
    gfx2 = gfx + extra
    struct.pack_into("<i", buf, dta_at, dta2)
    struct.pack_into("<i", buf, gfx_at, gfx2)
    info.update(dta2=dta2, gfx2=gfx2, extra=extra)
    if dta != KNOWN_DTA or gfx != KNOWN_GFX:
        info["reason"] = (
            f"shipped sizes are {dta}/{gfx} KiB, not the known "
            f"{KNOWN_DTA}/{KNOWN_GFX}; grew with the same 2.25x formula"
        )
    return bytes(buf), info


# --- filesystem helpers ----------------------------------------------------

def _norm(name: str) -> str:
    return name.casefold()


def _find_child(directory: Path, name: str) -> Optional[Path]:
    if not directory.is_dir():
        return None
    wanted = _norm(name)
    for child in directory.iterdir():
        if child.is_dir() and _norm(child.name) == wanted:
            return child
    return None


def _find_file(directory: Path, name: str) -> Optional[Path]:
    if not directory.is_dir():
        return None
    wanted = _norm(name)
    for child in directory.iterdir():
        if child.is_file() and _norm(child.name) == wanted:
            return child
    return None


def find_heap_dir(romfs: Path) -> Optional[Path]:
    for sys_name, heap_name in (("System", "HeapSize"), ("system", "heapsize")):
        sys_dir = _find_child(romfs, sys_name)
        if sys_dir is None:
            continue
        heap_dir = _find_child(sys_dir, heap_name)
        if heap_dir is not None:
            return heap_dir
    return None


def iter_heapdef_files(heap_dir: Path) -> Iterable[Path]:
    for path in sorted(heap_dir.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        name = path.name
        if name.endswith("HeapDef.byml.zs") or name.endswith("HeapDef.byml.zstd"):
            yield path


def default_mod_romfs(package_root: Path) -> Path:
    return package_root / "load" / TITLE_ID / "OoT-4K" / "romfs"


def process_file(
    path: Path, dicts: ZstdDicts, out_dir: Path, relative_parent: str
) -> dict:
    name = path.name
    packed = path.read_bytes()
    if not packed:
        raise HeapDefError(f"{name} is empty")
    dict_id = dicts.frame_dict_id(packed)
    raw = dicts.decompress(packed, name)
    grown, info = grow_heapdef_byml(raw, name)
    if info["skipped"]:
        recompressed = packed  # keep the original compressed file
    else:
        recompressed = dicts.compress(grown, dict_id, name)
    dest_parent = out_dir / relative_parent
    dest_parent.mkdir(parents=True, exist_ok=True)
    dest = dest_parent / name
    dest.write_bytes(recompressed)
    info["dest"] = str(dest)
    info["dict_id"] = dict_id
    info["src"] = str(path)
    return info


def run(romfs: Path, output_romfs: Path) -> list[dict]:
    if not romfs.is_dir():
        raise HeapDefError(f"RomFS path does not exist or is not a directory: {romfs}")
    heap_dir = find_heap_dir(romfs)
    if heap_dir is None:
        raise HeapDefError(
            f"no System/HeapSize directory under {romfs}. Dump the game's RomFS "
            "(Fritchii: right-click the game -> Dump RomFS -> Full) without "
            "EDEN_NX2_OOT_4K=1, then point --romfs at the dump's romfs folder."
        )

    dicts = ZstdDicts()
    zsdic = load_zsdic(romfs, dicts)
    if zsdic:
        print(f"loaded dictionaries from {zsdic} ({len(dicts.ddicts)} dicts)")
    else:
        print(
            "warning: Pack/ZsDic.pack.zs not found; decompression will fail "
            "if a HeapDef needs a dictionary",
            file=sys.stderr,
        )

    # Preserve the dump's casing (System/HeapSize vs system/heapsize).
    rel = "/".join(heap_dir.relative_to(romfs).parts)
    results = []
    found = list(iter_heapdef_files(heap_dir))
    if not found:
        raise HeapDefError(f"no *HeapDef.byml.zs in {heap_dir}")
    for path in found:
        info = process_file(path, dicts, output_romfs, rel)
        results.append(info)
        extra_mib = info["extra"] / 1024.0
        status = "skipped" if info["skipped"] else "grew"
        print(
            f"{status} {info['name']}: DynamicTextureAllocatorMem "
            f"{info['dta']} -> {info['dta2']} KiB, Graphics "
            f"{info['gfx']} -> {info['gfx2']} KiB "
            f"(extra {info['extra']} KiB, {extra_mib:.0f} MiB, dict {info['dict_id']})"
        )
        if info["reason"]:
            print(f"  note: {info['reason']}")
        print(f"  wrote {info['dest']}")
    return results


# --- synthetic BYML for --self-test ----------------------------------------

def build_synthetic_byml(dta: int = KNOWN_DTA, gfx: int = KNOWN_GFX) -> bytes:
    """A minimal little-endian BYML with Heaps / DTA / Graphics / Size."""
    keys = [b"Heaps", b"DynamicTextureAllocatorMem", b"Graphics", b"Size"]
    # Layout:
    # 0x00 header (16 bytes)
    # 0x10 key table
    # then nodes: root, heaps, dta, gfx
    key_table = bytearray()
    key_table += struct.pack("<I", (len(keys) << 8) | 0xC2)
    # Placeholder offsets, then strings packed after the offset table.
    offset_table = bytearray(4 * len(keys))
    strings = bytearray()
    base = 4 + 4 * len(keys)
    for i, key in enumerate(keys):
        struct.pack_into("<I", offset_table, 4 * i, base + len(strings))
        strings += key + b"\0"
    key_table += offset_table + strings
    while len(key_table) % 4:
        key_table += b"\0"

    def hash_node(entries: list[tuple[int, int, int]]) -> bytes:
        # entries: (key_index, type, value_u32)
        out = bytearray(struct.pack("<I", (len(entries) << 8) | 0xC1))
        for key_index, typ, value in entries:
            out += struct.pack("<I", (typ << 24) | key_index)
            out += struct.pack("<I", value)
        return bytes(out)

    header_len = 16
    keys_off = header_len
    # Place nodes after the key table, 4-byte aligned.
    cursor = keys_off + len(key_table)

    # We need known offsets before writing nodes that point at each other,
    # so compute sizes first. Each hash is 4 + 8 * n bytes.
    dta_node = hash_node([(3, 0xD1, dta & 0xFFFFFFFF)])
    gfx_node = hash_node([(3, 0xD1, gfx & 0xFFFFFFFF)])
    # heaps points at dta and gfx node offsets; root points at heaps.
    # Reserve heaps and root sizes.
    heaps_size = 4 + 8 * 2
    root_size = 4 + 8 * 1
    root_off = cursor
    heaps_off = root_off + root_size
    dta_off = heaps_off + heaps_size
    gfx_off = dta_off + len(dta_node)

    root = hash_node([(0, 0xC1, root_off and heaps_off)])
    # rebuild with actual offsets
    root = hash_node([(0, 0xC1, heaps_off)])
    heaps = hash_node([(1, 0xC1, dta_off), (2, 0xC1, gfx_off)])
    assert len(root) == root_size and len(heaps) == heaps_size

    header = bytearray(16)
    header[0:2] = b"YB"
    struct.pack_into("<H", header, 2, 4)
    struct.pack_into("<I", header, 4, keys_off)
    struct.pack_into("<I", header, 8, 0)
    struct.pack_into("<I", header, 12, root_off)
    return bytes(header + key_table + root + heaps + dta_node + gfx_node)


def self_test() -> None:
    extra = extra_kib(KNOWN_DTA)
    assert extra == 391168, extra
    assert KNOWN_DTA + extra == KNOWN_DTA_GROWN
    assert KNOWN_GFX + extra == KNOWN_GFX_GROWN

    raw = build_synthetic_byml()
    grown, info = grow_heapdef_byml(raw, "synthetic.HeapDef.byml")
    assert not info["skipped"]
    assert info["dta"] == KNOWN_DTA and info["gfx"] == KNOWN_GFX
    assert info["dta2"] == KNOWN_DTA_GROWN and info["gfx2"] == KNOWN_GFX_GROWN
    # Growing twice is a no-op once the known sizes are in place.
    again, info2 = grow_heapdef_byml(grown, "synthetic.HeapDef.byml")
    assert info2["skipped"] and again == grown

    # IPS offsets used by the companion generator stay in IPS32 range.
    from make_exefs_patches import RECORDS, NSO_HEADER, build_ips32, verify_ips32, word_le

    ips = build_ips32()
    verify_ips32(ips)
    assert ips.startswith(b"IPS32") and ips.endswith(b"EEOF")
    assert any(off + NSO_HEADER > 0xFFFFFF for off, *_ in RECORDS)
    print("self-test passed: ExtraKib, BYML growth (idempotent), IPS32 records")
    print(
        f"  {KNOWN_DTA} -> {KNOWN_DTA_GROWN} DTA, {KNOWN_GFX} -> {KNOWN_GFX_GROWN} Graphics"
    )


def main() -> None:
    package_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--romfs",
        type=Path,
        help="Extracted RomFS root (default dump path: "
        f"~/.local/share/fritchii/dump/{TITLE_ID}/romfs)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_mod_romfs(package_root),
        help="Mod romfs directory to write System/HeapSize/* into "
        f"(default: {default_mod_romfs(package_root)})",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run built-in tests that do not need a game dump, then exit",
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.romfs is None:
        parser.error("--romfs is required (or pass --self-test)")
    run(args.romfs.resolve(), args.output.resolve())
    print()
    print(
        "HeapDef files are in the mod romfs. The application-pool +400 MiB "
        "reservation is emulator-side and is not in this mod; test with the "
        "12 GB layout (Fritchii already forces 12 GB for Switch 2 games)."
    )


if __name__ == "__main__":
    try:
        main()
    except HeapDefError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
