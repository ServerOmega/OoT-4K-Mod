#!/usr/bin/env python3
"""Read-only extractor for Fritchii NUP containers.

Pulls System/HeapSize/*HeapDef* and Pack/ZsDic.pack.zs out of the merged
(base + best update) RomFS of a .nup, the same way Fritchii's AppLoader_NUP
builds its RomFS (update paths shadow base paths). Opens the .nup read-only.

Layout (src/core/file_sys/nup_container.cpp), all little-endian:
  0x00 Superblock (0x80): magic 'NUP\\x01', u16 header_size, u16 version,
       u64 toc_offset, u64 toc_size, sha256[32] of TOC, u64 blob_offset,
       u64 file_size, u32 package_count, u32 flags, uuid[16], reserved[32]
  TOC: header (0x30) 'TOC\\x01', u32 pkg_count, u64 dir_count, u64 file_count,
       u64 pool_offset, u64 pool_size, u64 reserved
       packages[pkg_count] (0x40), dirs[dir_count] (0x18), files[file_count] (0x50),
       children u32[] up to pool_offset, then string pool.
  Child index < dir_count is a dir, otherwise file index = child - dir_count.
  File codec 0 = raw extent, 1 = one zstd frame. No encryption.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

NO_DIR = 0xFFFFFFFF
KIND = {0: "Base", 1: "Update", 2: "Addon"}


class Nup:
    def __init__(self, path: Path):
        self.f = open(path, "rb")  # read-only
        sb = self.f.read(0x80)
        if sb[:4] != b"NUP\x01":
            raise SystemExit("not a NUP file")
        (self.toc_off, self.toc_size) = struct.unpack_from("<QQ", sb, 8)
        toc_sha = sb[0x18:0x38]
        (self.blob_off, self.file_size, pkg_count, flags) = struct.unpack_from("<QQII", sb, 0x38)
        if flags != 0:
            raise SystemExit(f"unexpected superblock flags {flags:#x}")
        self.f.seek(self.toc_off)
        toc = self.f.read(self.toc_size)
        if hashlib.sha256(toc).digest() != toc_sha:
            raise SystemExit("TOC SHA-256 mismatch")
        magic, pc, dc, fc, pool_off, pool_size, _ = struct.unpack_from("<IIQQQQQ", toc, 0)
        if magic != 0x01434F54 or pc != pkg_count:
            raise SystemExit("bad TOC header")
        pool = toc[pool_off : pool_off + pool_size]
        name = lambda o, n: pool[o : o + n].decode("utf-8", "replace")
        pos = 0x30
        self.packages = []
        for _ in range(pc):
            tid, root, exefs, romfs, ver, kind, pflags = struct.unpack_from("<QIIIIBB", toc, pos)
            self.packages.append(dict(tid=tid, root=root, exefs=exefs, romfs=romfs,
                                      version=ver, kind=kind, flags=pflags))
            pos += 0x40
        self.dirs = []
        for _ in range(dc):
            no, nl, first, count = struct.unpack_from("<IIII", toc, pos)
            self.dirs.append((name(no, nl), first, count))
            pos += 0x18
        self.files = []
        for _ in range(fc):
            no, nl, size, eoff, esize = struct.unpack_from("<IIQQQ", toc, pos)
            sha = toc[pos + 0x20 : pos + 0x40]
            codec, cflags = toc[pos + 0x40], toc[pos + 0x41]
            self.files.append(dict(name=name(no, nl), size=size, eoff=eoff, esize=esize,
                                   sha=sha, codec=codec, cflags=cflags))
            pos += 0x50
        n_children = (pool_off - pos) // 4
        self.children = list(struct.unpack_from(f"<{n_children}I", toc, pos))

    def entries(self, d):
        _, first, count = self.dirs[d]
        for c in self.children[first : first + count]:
            if c < len(self.dirs):
                yield ("dir", self.dirs[c][0], c)
            else:
                yield ("file", self.files[c - len(self.dirs)]["name"], c - len(self.dirs))

    def lookup_dir(self, d, parts):
        for p in parts:
            nxt = None
            for kind, nm, idx in self.entries(d):
                if kind == "dir" and nm.casefold() == p.casefold():
                    nxt = idx
                    break
            if nxt is None:
                return None
            d = nxt
        return d

    def read_file(self, idx) -> bytes:
        fe = self.files[idx]
        if fe["size"] == 0:
            return b""
        self.f.seek(fe["eoff"])
        blob = self.f.read(fe["esize"])
        if fe["codec"] == 0:
            data = blob
        elif fe["codec"] == 1:
            import zstandard
            data = zstandard.ZstdDecompressor().decompress(blob, max_output_size=fe["size"])
        else:
            raise SystemExit(f"{fe['name']}: unknown codec {fe['codec']}")
        if len(data) != fe["size"] or hashlib.sha256(data).digest() != fe["sha"]:
            raise SystemExit(f"{fe['name']}: size/SHA-256 mismatch")
        return data


def pick(nup: Nup, pkg_order, rel_dir, want):
    """Merged lookup: later packages in pkg_order shadow earlier ones."""
    found = {}
    for pi in pkg_order:
        pkg = nup.packages[pi]
        if pkg["romfs"] == NO_DIR:
            continue
        d = nup.lookup_dir(pkg["romfs"], rel_dir.split("/"))
        if d is None:
            continue
        real_dir = "/".join(rel_dir.split("/"))
        for kind, nm, idx in nup.entries(d):
            if kind == "file" and want(nm):
                found[nm] = (pi, idx, real_dir)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("nup", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="romfs output root")
    ap.add_argument("--list", action="store_true", help="only list packages/matches")
    a = ap.parse_args()
    nup = Nup(a.nup)
    print(f"TOC @{nup.toc_off:#x} size {nup.toc_size}, blob @{nup.blob_off:#x}, "
          f"{len(nup.dirs)} dirs, {len(nup.files)} files")
    for i, p in enumerate(nup.packages):
        print(f"  package {i}: {KIND.get(p['kind'], p['kind'])} tid {p['tid']:016X} "
              f"v{p['version']} flags {p['flags']:#x} romfs_dir {p['romfs']:#x}")
    base = [i for i, p in enumerate(nup.packages) if p["kind"] == 0][:1]
    upd = sorted((i for i, p in enumerate(nup.packages) if p["kind"] == 1),
                 key=lambda i: nup.packages[i]["version"])[-1:]
    order = base + upd
    hits = {}
    hits.update(pick(nup, order, "System/HeapSize", lambda n: "HeapDef" in n))
    hits.update(pick(nup, order, "Pack", lambda n: n.casefold() == "zsdic.pack.zs"))
    if not hits:
        raise SystemExit("no matching files found")
    for nm, (pi, idx, rel) in sorted(hits.items(), key=lambda kv: (kv[1][2], kv[0])):
        fe = nup.files[idx]
        src = KIND.get(nup.packages[pi]["kind"])
        print(f"  {rel}/{nm}  {fe['size']} bytes  codec {fe['codec']}  from {src} pkg {pi}")
        if a.list:
            continue
        data = nup.read_file(idx)
        dest = a.out / rel / nm
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"    wrote {dest} (sha256 ok)")


if __name__ == "__main__":
    main()
