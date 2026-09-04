#!/usr/bin/env python3
"""
Build work/sd-wipe.bin - a one-shot payload that erases the FEL boot stub from
the 1014D's *internal* SD card, so the scope boots from SPI flash again.

Why this exists
---------------
To reach FEL we wrote fel-sdboot.sunxi to the internal card at byte 8192.  The
boot ROM checks the card before SPI flash, so the scope now always lands in FEL
and never runs the app - and the app is the only thing that exposes the card as
USB mass storage.  Chicken and egg.  Rather than boot the scope, we run code on
the SoC that writes zeros over those 8192 bytes directly.

Base image: upstream pecostm32 fnirsi_1014d_startup (work/peco-bootloader.bin),
which already initialises clocks, DRAM, display and the SD card, and links in
sd_card_write().  We replace its dead SD-boot path with our own routine.

Addresses, all verified by disassembly of the base image:
    0x13a4  sd_card_init()        called by main at 0x4cf4
    0x19e4  sd_card_read()        issues CMD17 (0x11) / CMD18 (0x12)
    0x1b84  sd_card_write()       issues CMD24 (0x18) / CMD25 (0x19)  <-- ours
    0x0140  display_set_fg_color(rgb)
    0x05fc  display_fill_rect(x, y, w, h)
    0x81CBBA90  DRAM scratch buffer main passes to sd_card_read

What it writes
--------------
Sector 16, 16 blocks = bytes 8192..16383 - exactly the stub we put there.
The FAT32 partition starts at sector 63 = byte 32256, so the filesystem and
your saved captures are 15872 bytes clear of the end of the write.
Nothing here touches SPI flash, so the splash and the per-unit calibration at
0x1FD000 are not involved at all.

Screen goes GREEN if sd_card_write() returned 0, RED otherwise, then halts.
"""

import struct
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else 'work/peco-bootloader.bin'
DST = sys.argv[2] if len(sys.argv) > 2 else 'work/sd-wipe.bin'

SD_INIT, SD_WRITE = 0x13a4, 0x1b84
SET_COLOR, FILL_RECT = 0x0140, 0x05fc
BUFFER = 0x81CBBA90          # DRAM scratch, well clear of our code in SRAM
SECTOR, BLOCKS = 16, 16      # byte 8192, 8192 bytes
BASE = 0x4CF4                # start of main's dead SD path (runs to 0x4E84)

d = bytearray(open(SRC, 'rb').read())


def egon_checksum(buf):
    """Standard sunxi eGON checksum: sum of u32 words over the declared block
    size, with the checksum field itself replaced by the stamp 0x5F0A6C39."""
    m = bytearray(buf)
    m[0x0C:0x10] = struct.pack('<I', 0x5F0A6C39)
    n = struct.unpack('<I', m[0x10:0x14])[0]
    return sum(struct.unpack('<I', m[i:i + 4])[0] for i in range(0, n, 4)) & 0xFFFFFFFF


assert egon_checksum(d) == struct.unpack('<I', d[0x0C:0x10])[0], \
    "base image checksum does not verify - wrong file?"

A = lambda i: BASE + 4 * i                      # address of instruction i
rel = lambda at, t: ((t - (at + 8)) >> 2) & 0xFFFFFF   # ARM pc-relative, pc = insn + 8

ZLOOP, FAIL, PAINT, HALT, LIT = A(6), A(17), A(18), A(24), A(25)

code = [
    (0xEB000000 | rel(A(0), SD_INIT),  "bl   sd_card_init"),
    (0xE3500000,                       "cmp  r0, #0"),
    (0x1A000000 | rel(A(2), FAIL),     "bne  FAIL"),

    (0xE59F4000 | (LIT - (A(3) + 8)),  "ldr  r4, =0x%08X" % BUFFER),
    (0xE3A05000,                       "mov  r5, #0"),
    (0xE3A06A02,                       "mov  r6, #8192"),
    # ZLOOP:
    (0xE4845004,                       "str  r5, [r4], #4"),
    (0xE2566004,                       "subs r6, r6, #4"),
    (0x1A000000 | rel(A(8), ZLOOP),    "bne  ZLOOP"),

    (0xE3A00010,                       "mov  r0, #%d      ; sector" % SECTOR),
    (0xE3A01010,                       "mov  r1, #%d      ; blocks" % BLOCKS),
    (0xE59F2000 | (LIT - (A(11) + 8)), "ldr  r2, =0x%08X" % BUFFER),
    (0xEB000000 | rel(A(12), SD_WRITE), "bl   sd_card_write"),
    (0xE3500000,                       "cmp  r0, #0"),
    (0x1A000000 | rel(A(14), FAIL),    "bne  FAIL"),

    (0xE3A00CFF,                       "mov  r0, #0x00FF00   ; green"),
    (0xEA000000 | rel(A(16), PAINT),   "b    PAINT"),
    # FAIL:
    (0xE3A008FF,                       "mov  r0, #0xFF0000   ; red"),
    # PAINT:
    (0xEB000000 | rel(A(18), SET_COLOR), "bl   display_set_fg_color"),
    (0xE3A00000,                       "mov  r0, #0"),
    (0xE3A01000,                       "mov  r1, #0"),
    (0xE3A02E32,                       "mov  r2, #800"),
    (0xE3A03E1E,                       "mov  r3, #480"),
    (0xEB000000 | rel(A(23), FILL_RECT), "bl   display_fill_rect"),
    # HALT:
    (0xEA000000 | rel(A(24), HALT),    "b    HALT"),
    (BUFFER,                           ".word 0x%08X" % BUFFER),
]

assert A(len(code) - 1) == LIT, "literal pool is not where the LDRs expect it"
assert A(len(code)) <= 0x4E84, "payload overruns the dead code region"

print("payload, assembled at 0x%04X:\n" % BASE)
for i, (word, text) in enumerate(code):
    print("  0x%04X  %08X   %s" % (A(i), word, text))

# main reaches 0x4CF0 with `bne 0x4E88` (choice != 0).  NOP it so we always
# fall through into our routine, whatever the front panel reported.
d[0x4CF0:0x4CF4] = struct.pack('<I', 0xE1A00000)

for i, (word, _) in enumerate(code):
    d[A(i):A(i) + 4] = struct.pack('<I', word)

# Caches off.  main enables I- and D-cache before we get here; our zeros must be
# in DRAM, not parked in the D-cache, when the SD controller reads the buffer.
for at in (0x4B84, 0x4B88):
    d[at:at + 4] = struct.pack('<I', 0xE1A00000)

d[0x0C:0x10] = struct.pack('<I', egon_checksum(d))
open(DST, 'wb').write(bytes(d))

print("\nwrote %s  (%d bytes, eGON checksum 0x%08X)"
      % (DST, len(d), struct.unpack('<I', d[0x0C:0x10])[0]))
print("target: sector %d, %d blocks = bytes %d..%d"
      % (SECTOR, BLOCKS, SECTOR * 512, (SECTOR + BLOCKS) * 512 - 1))
print("FAT32 partition starts at byte 32256 - untouched")
