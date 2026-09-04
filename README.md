# FNIRSI 1014D — custom boot splash

Tooling to replace the boot logo on a FNIRSI 1014D oscilloscope, without
destroying the one thing on the scope that can't be replaced.

The splash turns out to be an uncompressed **298 × 98 RGB565** bitmap sitting at
a fixed offset in SPI flash. No compression, no palette, no encryption, and no
enforced checksum. Swapping it is genuinely straightforward — the risk in this
project isn't the image format, it's everything else on the chip.

> **Status: not yet run against hardware.** The format work is verified against
> real flash dumps (see [Verification](#verification)), but no byte has been
> written to a physical scope with this tooling. Treat the write path as
> untested and read [Before you start](#before-you-start) first.

This scope is also sold rebadged — **SiRyder** is one label — and shares most of
its design with the **FNIRSI 1013D**.

## Before you start

**Your scope's calibration lives at flash offset `0x1FD000` and cannot be
replaced.** It's written at the factory against your specific analog front end.
There's no download for it, no way to regenerate it, and somebody else's dump is
not a spare copy — two genuine dumps are byte-identical across the entire chip
*except* in that region.

So the whole procedure reduces to one rule: **take a verified full-chip backup
before the first write.** Do that, and the worst case stops being a brick and
becomes an inconvenience. Skip it and there's no way back.

`backup-scope.sh` exists to make that step hard to get wrong. It's read-only.

## Hardware

| Part | Detail |
|---|---|
| SoC | Allwinner **F1C100s**, ARM926EJ-S — has USB recovery (FEL), so no soldering |
| FPGA | Anlogic EF2L45LG144 — capture path, not involved in the splash |
| MCU | GD32E230 — front panel |
| Boot flash | **2 MiB** SPI NOR, W25Q16 class |
| Display | RGB565 framebuffer |

Because the F1C100s speaks FEL over USB, the flash can be read and written
without opening the scope or clipping onto the chip.

**Watch the chip size.** The 1014D is 2 MiB; later 1013D units are 4 MiB. Read
length is `0x200000`, not `0x400000`. This project got that wrong initially by
assuming the 1013D layout applied.

## Flash layout

Four Allwinner eGON blocks near the start, calibration at the end, and roughly
1.1 MiB of unused space in between.

| Offset | Block | 1014D | 1013D |
|---|---|---|---|
| `0x000000` | `eGON.BT0` — SPL, draws the splash | `0x3400` | `0x3400` |
| `0x006000` | `eGON.EXE` — FEL helper | `0xBA00` | `0x7A00` |
| `0x013000` | **`eGON.BMP` — the splash** | `0xE600` | `0xE600` |
| `0x027000` | `eGON.EXE` — scope app | `0xBC400` | `0x193A00` |
| `0x1FD000` | per-unit calibration | — | — |

The splash block is **identical on both models** — same offset, same size, same
298 × 98 geometry. Only the executable blocks and total chip size differ.

### Block format

```
0x00  u32   ARM branch instruction
0x04  8s    magic: "eGON.BT0" / "eGON.EXE" / "eGON.BMP"
0x0C  u32   checksum
0x10  u32   block size, including header
0x1A  u16   x pixels    (BMP only)
0x1C  u16   y pixels    (BMP only)
0x20  8     duplicate of 0x18..0x1F, stripped by the SPL
0x28  ...   pixel data
```

Pixels are **RGB565 little-endian, top-down**, no stride padding, zero-filled to
the end of the block. At 298 × 98 that's 58,408 bytes of pixels followed by 432
zero bytes. One pixel is `(R << 11) | (G << 5) | B`, stored low byte first —
green straddles the byte boundary, which is the part that's easy to get wrong.

### Checksums

`eGON.BT0` uses the standard sunxi algorithm — sum of `u32` words with the
checksum field replaced by the stamp `0x5F0A6C39` — and verifies correctly on
genuine dumps. The others don't behave the same way:

- Both `.EXE` blocks store `0x00000000`.
- The `.BMP` field differs per image (`0x776F9CC9` on a 1013D, `0x03098A00` on
  the 1014D) and matches no sum over any range tried.
- A known-working patched image published upstream carries a **stale** BT0
  checksum, which suggests the boot path doesn't enforce it on SPI flash.

`fnirsi_splash.py` leaves the BMP field untouched by default. `--fix-checksum`
recomputes it if you'd rather.

## Usage

Requires Python 3 with Pillow and NumPy, and a `sunxi-fel` **built with SPI
flash support** — the stock package build often lacks it.

```bash
# what's on this dump?
python3 fnirsi_splash.py info backup-1.bin

# pull the current splash out as a PNG (-s 2 also writes a 2x version)
python3 fnirsi_splash.py extract backup-1.bin -o stock.png -s 2

# build a patched image - never edits in place
python3 fnirsi_splash.py replace backup-1.bin mylogo.png -o patched.bin
```

`replace` resizes and letterboxes onto black, and Floyd-Steinberg dithers by
default. That matters more than it sounds: flat truncation bands visibly on skin
tones and gradients at 5/6/5. On a photographic test image, dithering measured
RMSE 2.18 / PSNR 41.4 dB against 3.51 / 37.2 dB for plain truncation.

Use `--exact` to require the source image already be the right pixel size rather
than letting it be rescaled.

Two safety behaviours are built in: `replace` **refuses to emit a file whose
calibration region differs from the source**, and it reports exactly which 4 KiB
pages changed — a correct splash swap touches only the 15 pages of the bitmap
block.

### Taking a backup

With the scope in FEL mode:

```bash
./backup-scope.sh
```

Read-only. It checks the scope is enumerating as `1f3a:efe8`, reads the chip
**twice** and `cmp`s the two reads, aborts if they disagree, hashes the result,
splits the calibration region into its own file, then reports the layout and
extracts your stock splash so you can confirm it looks right.

A backup you can't trust is worse than no backup, because you'll rely on it.
That's why it reads twice.

### Full procedure

`bench-guide.html` is the step-by-step version — a to-scale flash map, the FEL
commands, recovery paths, and a checklist that ticks off as you work. It's a
self-contained page; clone the repo and open it in a browser.

## Verification

What's actually been checked, against real dumps rather than inferred:

- **The format round-trips losslessly.** Extracting the splash to PNG and
  re-encoding it back reproduces the source file **byte for byte**, on both a
  1013D (4 MiB) and a genuine 1014D (2 MiB) image. That's the evidence the
  format above is right rather than merely plausible.
- **Calibration is the only per-unit region.** Two genuine dumps differ in
  exactly two 4 KiB pages, both at `0x1FD000`.
- **The BT0 checksum algorithm** reproduces the stored value exactly.

What hasn't:

- **Nothing has been written to a physical scope.** The `spiflash-read`
  invocations are standard `sunxi-fel` usage; the `spiflash-write` and FEL
  SD-boot commands come from upstream's documentation.
- **The A/B display variants.** Official firmware ships as `-A-` and `-B-`
  builds for different panels, with a documented symptom of the image shifting
  left on the wrong one. Whether that reaches the splash block is unknown.
- **Whether your unit matches.** Run `info` on your own dump before writing.
  The tool parses geometry from the block header rather than hardcoding it, so
  it should adapt — but confirm rather than assume.

## Credits

This rests almost entirely on [pecostm32](https://github.com/pecostm32)'s
reverse engineering, which is where the flash dumps, schematics, FPGA analysis
and sunxi tooling all come from:

- [FNIRSI-1013D-1014D-Hack](https://github.com/pecostm32/FNIRSI-1013D-1014D-Hack)
  — dumps, schematics, FPGA notes. The genuine 1014D flash image used to verify
  this work is at `Test code/1014D_Emulator/FNIRSI_1014D_full_flash_backup.bin`.
- [FNIRSI_1014D_Firmware](https://github.com/pecostm32/FNIRSI_1014D_Firmware)
  and [FNIRSI_1013D_Firmware](https://github.com/pecostm32/FNIRSI_1013D_Firmware)
  — open replacement firmware. Worth a look if you'd rather run something other
  than the stock application; note that changing *its* logo means rebuilding
  that project, not patching a bitmap.

Hardware identification from the
[CircuitDigest teardown](https://circuitdigest.com/articles/fnirsi-1014d-oscilloscope-teardown)
and [CNX Software](https://www.cnx-software.com/2022/11/16/fnirsi-1013d-teardown-and-mini-review-a-portable-oscilloscope-based-on-allwinner-cpu-anlogic-fgpa/).

## Disclaimer

Modifying your scope's firmware is at your own risk, and will not endear you to
the warranty. No affiliation with FNIRSI.
