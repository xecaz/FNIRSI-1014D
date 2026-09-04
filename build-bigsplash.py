#!/usr/bin/env python3
"""
Build work/bigsplash.bin - a full-screen 800x480 boot splash for the 1014D.

The stock bitmap block at 0x13000 holds 77784 bytes of payload and the app
block starts right behind it at 0x27000, so a full-screen image (800*480*2 =
768000 bytes) cannot live there.  Instead we put a new eGON.BMP block in the
unused space above the app and repoint the SPL at it.

SPL changes (2 words, both found by disassembly of the SPL in flash):
    0x548   mov r0, #0x13000  ->  mov r0, #0x100000   bitmap header address
    0x75c   .word 0x13028     ->  .word 0x100028      bitmap pixel address

Memory, from the SPL itself:
    0x80000000  app load address        (app is ~0xBCE00 bytes)
    0x81000000  splash pixel buffer     <- our 768000 bytes land here
    0x81B00000  framebuffer             11534336 bytes above the buffer
so the larger read has ~10.7 MB of headroom before it reaches anything.

The SPL centres with (800-w)/2, (480-h)/2, which for 800x480 gives (0,0).

The old block at 0x13000 is deliberately LEFT IN PLACE: reverting is then a
two-word SPL change rather than restoring two regions of flash.
"""

import struct
import sys

sys.path.insert(0, '.')
from fnirsi_splash import encode_rgb565, egon_checksum          # noqa: E402
from PIL import Image                                            # noqa: E402

# usage: build-bigsplash.py [flash-image] [picture] [output]
SRC     = sys.argv[1] if len(sys.argv) > 1 else 'work/patched.bin'
PICTURE = sys.argv[2] if len(sys.argv) > 2 else 'new.jonash3.png'
DST     = sys.argv[3] if len(sys.argv) > 3 else 'work/bigsplash.bin'

BMP_ADDR = 0x100000               # free: app ends 0xE3E00, calibration at 0x1FD000
BLOCK_SIZE = 0xBC000              # 770048; payload 770008, pixels 768000
WIDTH, HEIGHT = 800, 480

SPL_MOV_OFF = 0x548               # mov r0, #imm  -- bitmap header address
SPL_LIT_OFF = 0x75C               # literal       -- bitmap pixel address
MOV_R0_OLD = 0xE3A00A13           # mov r0, #0x13000
MOV_R0_NEW = 0xE3A00601           # mov r0, #0x100000  (imm8=1, rot=6 -> 1<<20)

data = bytearray(open(SRC, 'rb').read())
assert len(data) == 0x200000, "source is not a 2 MiB image"
assert struct.unpack('<I', data[SPL_MOV_OFF:SPL_MOV_OFF + 4])[0] == MOV_R0_OLD, \
    "SPL does not contain the expected 'mov r0, #0x13000' - refusing to patch"
assert struct.unpack('<I', data[SPL_LIT_OFF:SPL_LIT_OFF + 4])[0] == 0x13028, \
    "SPL literal is not 0x13028 - refusing to patch"

pixels = WIDTH * HEIGHT * 2
assert BMP_ADDR + BLOCK_SIZE <= 0x1FD000, "new block would reach calibration"
assert BMP_ADDR >= 0xE4000, "new block would overlap the app"
assert 40 + pixels <= BLOCK_SIZE, "image does not fit the block"

# ---- the image -------------------------------------------------------------
img = Image.open(PICTURE)
print("source image: %dx%d %s" % (img.size[0], img.size[1], img.mode))
if img.size != (WIDTH, HEIGHT):
    print("  resizing to %dx%d (stretch, keeps all content)" % (WIDTH, HEIGHT))
payload = encode_rgb565(img, WIDTH, HEIGHT, dither=True, stretch=True)
assert len(payload) == pixels

# ---- the block header ------------------------------------------------------
# Copy the stock header and change only size, width and height.  Fields at 0x18
# (0x1000) and 0x1E (0x1B01) are identical on both a 298x98 and a 298x130
# firmware, so they are constants and are carried over untouched.  0x20..0x27
# duplicates 0x18..0x1F and is updated to match.
hdr = bytearray(data[0x13000:0x13028])
struct.pack_into('<I', hdr, 0x10, BLOCK_SIZE)
struct.pack_into('<H', hdr, 0x1A, WIDTH)
struct.pack_into('<H', hdr, 0x1C, HEIGHT)
hdr[0x20:0x28] = hdr[0x18:0x20]

block = bytearray(BLOCK_SIZE)
block[0:0x28] = hdr
block[0x28:0x28 + pixels] = payload          # remainder stays zero, as stock does

data[BMP_ADDR:BMP_ADDR + BLOCK_SIZE] = block

# ---- point the SPL at it ---------------------------------------------------
struct.pack_into('<I', data, SPL_MOV_OFF, MOV_R0_NEW)
struct.pack_into('<I', data, SPL_LIT_OFF, BMP_ADDR + 0x28)
struct.pack_into('<I', data, 0x0C, 0)
struct.pack_into('<I', data, 0x0C, egon_checksum(bytes(data[0:0x3400])))

open(DST, 'wb').write(bytes(data))

# ---- prove what changed ----------------------------------------------------
old = open(SRC, 'rb').read()
diff = [i for i in range(len(old)) if old[i] != data[i]]
runs = []
for i in diff:
    if runs and i == runs[-1][1] + 1:
        runs[-1][1] = i
    else:
        runs.append([i, i])

print("\nwrote %s" % DST)
print("changed regions:")
for a, b in runs:
    print("  0x%06X..0x%06X  (%d bytes)" % (a, b, b - a + 1))
print("\ncalibration 0x1FD000 unchanged:",
      old[0x1FD000:0x1FF000] == bytes(data[0x1FD000:0x1FF000]))
print("app block   0x027000 unchanged:",
      old[0x27000:0xE3E00] == bytes(data[0x27000:0xE3E00]))
print("old bitmap  0x013000 unchanged:",
      old[0x13000:0x26000] == bytes(data[0x13000:0x26000]))
print("SPL checksum now 0x%08X (verifies: %s)"
      % (struct.unpack('<I', data[0x0C:0x10])[0],
         egon_checksum(bytes(data[0:0x3400])) == struct.unpack('<I', data[0x0C:0x10])[0]))
