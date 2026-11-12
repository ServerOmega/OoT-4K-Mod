This folder is empty on purpose. The game's HeapDef.byml.zs files are
copyrighted and are not in this package.

On the PC, dump the game's RomFS (without EDEN_NX2_OOT_4K=1) and run:

  python3 scripts/grow_heapdef.py --romfs ~/.local/share/fritchii/dump/0400486024DEC000/romfs --output ~/.local/share/fritchii/load/0400486024DEC000/OoT-4K/romfs

That writes the grown *HeapDef.byml.zs files here (or into --output).
See the package README for the full steps, including what to do if /home
is short on space.
