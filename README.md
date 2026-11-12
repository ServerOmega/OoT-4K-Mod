# OoT 4K Mod (Fritchii)

A native 4K mod for **The Legend of Zelda: Ocarina of Time** (Switch 2,
title `0400486024DEC000`, v131072, main build id
`E0F3CCFAF50065AE4EA01EB5AEB13B1A45DA3348`) for the Fritchii / Lichii
emulators, installed as a normal mod folder.

In Docked mode the game renders its 3D scene at **3840x2160** instead of its
usual 2560x1440 maximum. It was tested on Linux with Fritchii v5.8.4 and an
RTX 5070 Ti: about 54–60 fps in Hyrule Field, with no crash and no memory
problems over a few minutes of play.

The game's own files are copyrighted and **not included**. You make the
memory file from your own copy of the game (steps below).

## What it changes

The code patch (`exefs/*.ips`, readable copy in `patches/oot_4k.pchtxt`)
makes these changes:

| Site | Original | New |
| --- | --- | --- |
| main+0x1202EE0 / +0x1202EE4 | 2560x1440 | 3840x2160 |
| main+0x31F39F4 / +0x31F39F8 | 2560x1440 | 3840x2160 |
| main+0x31F3A00 / +0x31F3A04 | 1920x1080 | 2880x1620 |
| main+0x80641C | `csel x8, x9, x8, hi` | `mov x8, xzr` |
| main+0x806420 | 1.0f | 1.5f |

The memory file (`romfs/System/HeapSize/Product.Nin_Ounce_NVN2.HeapDef.byml.zs`)
gives the game's texture memory room for the larger buffers:
DynamicTextureAllocatorMem goes from 312320 to 703488 KiB, and Graphics from
861184 to 1252352 KiB.

## Install

1. Copy `load/0400486024DEC000/OoT-4K` into Fritchii's load folder. On Linux
   that is `~/.local/share/fritchii/load/0400486024DEC000/OoT-4K/` by default.
   You can check it under Emulation → Configure → System → Filesystem.
2. Make the memory file from your own game (below) and put it in
   `OoT-4K/romfs/System/HeapSize/`.
3. Keep the **Max Resolution** built-in add-on on (Fritchii ships it).
4. Play in **Docked** mode. Handheld stays at 1920x1080.
5. Leave DLSS and FSR off for a pure 4K picture. You will see jagged edges,
   which SMAA or FXAA can soften.

Python 3 and `pip install zstandard` are needed for the scripts.

### Making the memory file from a `.nup`

Fritchii's Dump RomFS doesn't work with `.nup` games, so use the included
extractor. It only reads the `.nup` and checks every file's SHA-256:

```
python3 scripts/nup_extract_heapdef.py --out /path/with/space/romfs "/path/to/OoT.nup"
python3 scripts/grow_heapdef.py --romfs /path/with/space/romfs \
  --output ~/.local/share/fritchii/load/0400486024DEC000/OoT-4K/romfs
```

### Making the memory file from an NSP/XCI install

Right-click the game → Dump RomFS → Dump RomFS → **Full**, with the dump folder
on a drive with space. Then run:

```
python3 scripts/grow_heapdef.py --romfs <dump root>/0400486024DEC000/romfs \
  --output ~/.local/share/fritchii/load/0400486024DEC000/OoT-4K/romfs
```

Expected output:
`DynamicTextureAllocatorMem 312320 -> 703488 KiB, Graphics 861184 -> 1252352 KiB`.
If the file already has those sizes, the script leaves it alone.

## Checking it worked

In `~/.local/share/fritchii/log/eden_log.txt` you should see:

```
- Applying IPS patch from mod "OoT-4K"
RomFS: LayeredFS patches applied successfully
```

## Limits

- This mod can't add DLAA or 4K DLSS. Those need emulator changes, which are
  on the `linux-build/v5.8.4-dlssg` branch of
  [ServerOmega/Fritchii](https://github.com/ServerOmega/Fritchii).
- Builds of that branch with `EDEN_NX2_OOT_4K=1` apply the same changes
  themselves. **Turn this mod off** when you use that setting, or the memory
  change gets applied twice.
- Only tested on Fritchii, game version v131072. Lichii builds without the
  Max Resolution add-on may scale the scene back down.

## Credits

The earlier 4K work on the Hookshot recompiler and the Lichii Max Resolution
patch showed where to look.

🤖 Co-authored with Grok Bot
