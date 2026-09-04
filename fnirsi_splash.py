#!/usr/bin/env python3
"""
fnirsi_splash.py - inspect / extract / replace the boot splash in a FNIRSI
1013D / 1014D SPI flash image (W25Q32, 4 MiB).

The flash is a sequence of Allwinner "eGON" blocks:

    0x000000  eGON.BT0   SPL - loads the splash, then the main app
    0x006000  eGON.EXE   1st executable (FEL helper)
    0x013000  eGON.BMP   boot splash                  <-- what we care about
    0x027000  eGON.EXE   2nd executable (scope app)
    0x1FD000  (raw)      per-unit calibration / config - DO NOT TOUCH

eGON block header (32 bytes) + 8 extra bytes for BMP blocks:

    0x00  u32  ARM branch instruction
    0x04  8s   magic, e.g. "eGON.BMP"
    0x0C  u32  checksum
    0x10  u32  block size, including header
    0x14  ...  magic "BMP"
    0x1A  u16  x pixels
    0x1C  u16  y pixels
    0x20  8    duplicate of 0x18..0x1F, stripped by the SPL
    0x28  ...  pixel data: RGB565 little-endian, top-down, no compression
               zero-padded out to the block size

Verified against real dumps: for a 298x98 splash the pixel data is exactly
298*98*2 = 58408 bytes followed by 432 zero bytes.
"""

import argparse
import struct
import sys

# Two chip sizes are known in the wild:
#   1013D (later units)  W25Q32  4 MiB
#   1014D                W25Q16  2 MiB
# The block offsets below are identical on both; only the executable block
# sizes and the total chip size differ.
FLASH_SIZES = (2 * 1024 * 1024, 4 * 1024 * 1024)
BMP_OFFSET = 0x13000
BMP_HDR = 0x28  # 32-byte eGON header + 8 bytes stripped by the SPL

# Per-unit calibration/config. Differs between two otherwise identical dumps,
# so it is unique to your scope. Never overwrite it with someone else's.
CAL_OFFSET = 0x1FD000
CAL_LENGTH = 0x2000

EGON_STAMP = 0x5F0A6C39

BLOCKS = [
    ("SPL",      0x000000),
    ("1st exec", 0x006000),
    ("bitmap",   0x013000),
    ("2nd exec", 0x027000),
]


def egon_checksum(block: bytes) -> int:
    """Standard sunxi eGON checksum: sum of u32 words with the checksum
    field replaced by the stamp value. Verified to match the eGON.BT0
    block of genuine dumps."""
    b = bytearray(block)
    b[0x0C:0x10] = struct.pack("<I", EGON_STAMP)
    if len(b) % 4:
        b += b"\x00" * (4 - len(b) % 4)
    total = 0
    for i in range(0, len(b), 4):
        total = (total + struct.unpack_from("<I", b, i)[0]) & 0xFFFFFFFF
    return total


def read_flash(path: str) -> bytearray:
    with open(path, "rb") as fh:
        data = bytearray(fh.read())
    if len(data) not in FLASH_SIZES:
        sizes = " or ".join(f"{s // 1024 // 1024} MiB" for s in FLASH_SIZES)
        sys.stderr.write(
            f"warning: {path} is {len(data)} bytes; expected {sizes}. "
            f"Continuing, but check this is a full dump.\n"
        )
    if len(data) < CAL_OFFSET + CAL_LENGTH:
        sys.stderr.write(
            f"warning: image is shorter than the calibration region at "
            f"0x{CAL_OFFSET:X}; that safety check cannot run.\n"
        )
    return data


def parse_bmp_header(data: bytes, offset: int = BMP_OFFSET) -> dict:
    hdr = data[offset:offset + BMP_HDR]
    if len(hdr) < BMP_HDR:
        raise ValueError("image too short to contain a bitmap block")
    magic = hdr[4:12]
    if magic != b"eGON.BMP":
        raise ValueError(
            f"no eGON.BMP magic at 0x{offset:X} (found {magic!r}). "
            "This may be a different firmware layout - dump and inspect first."
        )
    size = struct.unpack_from("<I", hdr, 0x10)[0]
    xs, ys = struct.unpack_from("<HH", hdr, 0x1A)
    return {
        "offset": offset,
        "block_size": size,
        "width": xs,
        "height": ys,
        "pixel_offset": offset + BMP_HDR,
        "pixel_bytes": xs * ys * 2,
        "capacity": size - BMP_HDR,
        "stored_checksum": struct.unpack_from("<I", hdr, 0x0C)[0],
    }


def cmd_info(args):
    data = read_flash(args.image)
    chip = {2 * 1024 * 1024: "2 MiB - W25Q16 class, as found on the 1014D",
            4 * 1024 * 1024: "4 MiB - W25Q32 class, as found on later 1013D units"}
    print(f"image: {args.image}  ({len(data)} bytes)")
    print(f"chip : {chip.get(len(data), 'unrecognised size')}")
    print()
    print("  block      offset    magic       size        checksum")
    for name, off in BLOCKS:
        blk_magic = bytes(data[off + 4:off + 12])
        size = struct.unpack_from("<I", data, off + 0x10)[0]
        stored = struct.unpack_from("<I", data, off + 0x0C)[0]
        calc = egon_checksum(bytes(data[off:off + size])) if 0 < size <= len(data) - off else 0
        if stored == 0:
            state = "(not used)"
        elif stored == calc:
            state = "valid"
        else:
            state = "stale/not-a-sum"
        print(f"  {name:9s}  0x{off:06X}  {blk_magic.decode('latin1'):11s} "
              f"0x{size:<8X}  0x{stored:08X} {state}")
    print()
    try:
        b = parse_bmp_header(data)
    except ValueError as exc:
        print(f"bitmap: {exc}")
        return 1
    print(f"splash: {b['width']}x{b['height']} RGB565")
    print(f"  pixel data at 0x{b['pixel_offset']:X}, {b['pixel_bytes']} bytes")
    print(f"  block capacity {b['capacity']} bytes "
          f"({b['capacity'] - b['pixel_bytes']} bytes spare)")
    tail = data[b["pixel_offset"] + b["pixel_bytes"]:b["offset"] + b["block_size"]]
    print(f"  padding after pixels: {len(tail)} bytes, "
          f"{'all zero' if set(tail) <= {0} else 'NOT all zero'}")
    cal = data[CAL_OFFSET:CAL_OFFSET + CAL_LENGTH]
    print()
    print(f"calibration region 0x{CAL_OFFSET:X}..0x{CAL_OFFSET + CAL_LENGTH:X}: "
          f"{'blank (0xFF)' if set(cal) <= {0xFF} else 'present - back this up'}")
    return 0


def cmd_extract(args):
    from PIL import Image
    data = read_flash(args.image)
    b = parse_bmp_header(data)
    raw = data[b["pixel_offset"]:b["pixel_offset"] + b["pixel_bytes"]]
    w, h = b["width"], b["height"]
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        row = y * w
        for x in range(w):
            v = struct.unpack_from("<H", raw, (row + x) * 2)[0]
            r, g, bl = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
            # replicate high bits so 0x1F -> 255 exactly
            px[x, y] = ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (bl << 3) | (bl >> 2))
    img.save(args.output)
    print(f"wrote {args.output}  ({w}x{h})")
    if args.scale > 1:
        big = args.output.rsplit(".", 1)
        name = f"{big[0]}_{args.scale}x.{big[1]}"
        img.resize((w * args.scale, h * args.scale), Image.NEAREST).save(name)
        print(f"wrote {name}  ({w * args.scale}x{h * args.scale}, nearest-neighbour)")
    return 0


def encode_rgb565(img, w, h, dither=True, stretch=False):
    """Convert a PIL image to RGB565 little-endian bytes, resizing to w*h.

    By default the image is scaled to fit inside w*h with its aspect ratio
    preserved and centred on black. With stretch=True it is scaled to fill
    w*h exactly, which distorts the aspect ratio but leaves no black bars -
    useful when the source is close to the target shape but not identical."""
    from PIL import Image
    src = img.convert("RGB")
    if src.size != (w, h):
        if stretch:
            src = src.resize((w, h), Image.LANCZOS)
        else:
            sw, sh = src.size
            scale = min(w / sw, h / sh)
            new = (max(1, round(sw * scale)), max(1, round(sh * scale)))
            src = src.resize(new, Image.LANCZOS)
            canvas = Image.new("RGB", (w, h), (0, 0, 0))
            canvas.paste(src, ((w - new[0]) // 2, (h - new[1]) // 2))
            src = canvas

    out = bytearray(w * h * 2)
    if dither:
        # Floyd-Steinberg on the quantisation error, which matters a lot on
        # gradients at 5/6/5 bits.
        import numpy as np
        arr = np.asarray(src, dtype=np.float32).copy()
        for y in range(h):
            for x in range(w):
                old = arr[y, x].copy()
                r = min(31, max(0, int(old[0] * 31 / 255 + 0.5)))
                g = min(63, max(0, int(old[1] * 63 / 255 + 0.5)))
                b = min(31, max(0, int(old[2] * 31 / 255 + 0.5)))
                new_px = np.array([r * 255 / 31, g * 255 / 63, b * 255 / 31], dtype=np.float32)
                err = old - new_px
                struct.pack_into("<H", out, (y * w + x) * 2, (r << 11) | (g << 5) | b)
                if x + 1 < w:
                    arr[y, x + 1] += err * 7 / 16
                if y + 1 < h:
                    if x > 0:
                        arr[y + 1, x - 1] += err * 3 / 16
                    arr[y + 1, x] += err * 5 / 16
                    if x + 1 < w:
                        arr[y + 1, x + 1] += err * 1 / 16
    else:
        px = src.load()
        for y in range(h):
            for x in range(w):
                r, g, b = px[x, y]
                struct.pack_into("<H", out, (y * w + x) * 2,
                                 ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
    return bytes(out)


def cmd_replace(args):
    from PIL import Image
    data = read_flash(args.image)
    b = parse_bmp_header(data)
    w, h = b["width"], b["height"]

    img = Image.open(args.picture)
    if args.exact and img.size != (w, h):
        sys.stderr.write(
            f"error: --exact given but {args.picture} is {img.size[0]}x{img.size[1]}, "
            f"need exactly {w}x{h}\n")
        return 1

    payload = encode_rgb565(img, w, h, dither=not args.no_dither,
                            stretch=args.stretch)
    assert len(payload) == b["pixel_bytes"]

    start = b["pixel_offset"]
    end = b["offset"] + b["block_size"]
    # rewrite pixels, then re-zero the padding so nothing stale is left behind
    data[start:start + len(payload)] = payload
    data[start + len(payload):end] = b"\x00" * (end - start - len(payload))

    if args.fix_checksum:
        blk = bytes(data[b["offset"]:end])
        new = egon_checksum(blk)
        struct.pack_into("<I", data, b["offset"] + 0x0C, new)
        print(f"checksum field updated to 0x{new:08X}")
    else:
        print(f"checksum field left at 0x{b['stored_checksum']:08X} (unchanged)")

    # Safety: never let a splash edit disturb the calibration region.
    src = read_flash(args.image)
    if data[CAL_OFFSET:CAL_OFFSET + CAL_LENGTH] != src[CAL_OFFSET:CAL_OFFSET + CAL_LENGTH]:
        sys.stderr.write("error: calibration region changed - refusing to write\n")
        return 1

    changed = [hex(o) for o in range(0, len(data), 0x1000)
               if data[o:o + 0x1000] != src[o:o + 0x1000]]
    with open(args.output, "wb") as fh:
        fh.write(data)
    print(f"wrote {args.output}")
    print(f"changed 4K pages: {len(changed)} -> {changed}")
    print(f"calibration region at 0x{CAL_OFFSET:X}: untouched")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="show block layout and splash geometry")
    p.add_argument("image")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("extract", help="dump the splash to a PNG")
    p.add_argument("image")
    p.add_argument("-o", "--output", default="splash.png")
    p.add_argument("-s", "--scale", type=int, default=1,
                   help="also write an Nx nearest-neighbour version")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("replace", help="write a new splash into a copy of the image")
    p.add_argument("image")
    p.add_argument("picture", help="PNG/JPG to install; resized to fit, centred on black")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--exact", action="store_true",
                   help="require the picture to already be the exact pixel size")
    p.add_argument("--stretch", action="store_true",
                   help="scale to fill the slot exactly, distorting the aspect "
                        "ratio, instead of letterboxing onto black")
    p.add_argument("--no-dither", action="store_true",
                   help="plain truncation instead of Floyd-Steinberg")
    p.add_argument("--fix-checksum", action="store_true",
                   help="recompute the eGON checksum field (stock images do not "
                        "have a valid one here, so this is off by default)")
    p.set_defaults(func=cmd_replace)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
