# FNIRSI 1014D — boot splash reverse engineering

Goal: replace the boot logo on the owner's FNIRSI 1014D oscilloscope without
destroying it. Some sellers rebadge this scope as **SiRyder**; searches should
include that name and the closely related **1013D**, which shares most of its
design.

Public repo at `github.com/xecaz/FNIRSI-1014D`. No build step, no dependencies
beyond Pillow + NumPy.

**The repo is public.** Nothing personal, and no flash dumps, go in it: dumps
carry per-unit calibration, and the owner's splash artwork is their own. Both
are covered by `.gitignore` — check it before adding files.

## Hardware

| Part | Detail |
|---|---|
| SoC | Allwinner **F1C100s**, ARM926EJ-S — has USB recovery (FEL) |
| FPGA | Anlogic EF2L45LG144 — capture path, not involved in the splash |
| MCU | GD32E230 — front panel |
| Boot flash | **2 MiB** SPI NOR, W25Q16 class |
| Display | RGB565 framebuffer |

**The 1014D's flash is 2 MiB. Later 1013D units use 4 MiB.** This bit us once:
the first version of the tooling assumed 4 MiB from 1013D dumps. Read length is
`0x200000`, not `0x400000`.

## Flash layout

Four Allwinner eGON blocks near the start, calibration at the end.

| Offset | Block | 1014D size | 1013D size |
|---|---|---|---|
| `0x000000` | `eGON.BT0` — SPL, draws the splash | `0x3400` | `0x3400` |
| `0x006000` | `eGON.EXE` — FEL helper | `0xBA00` | `0x7A00` |
| `0x013000` | **`eGON.BMP` — the splash** | `0xE600` | `0xE600` |
| `0x027000` | `eGON.EXE` — scope app | `0xBC400` | `0x193A00` |
| `0x1FD000` | per-unit calibration | ~`0x1F4` | ~`0x1F4` |

The splash block is **identical across both models** — same offset, same size,
same 298 × 98 geometry. Only the executable blocks and total chip size differ.

### eGON block header

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

Splash pixels are **RGB565 little-endian, top-down, uncompressed**, no stride
padding, zero-filled to the end of the block. At 298 × 98 that is 58,408 bytes
of pixels then 432 bytes of zeroes.

### Checksums

`eGON.BT0` uses the standard sunxi algorithm — sum of `u32` words with the
checksum field replaced by the stamp `0x5F0A6C39` — and verifies correctly on
genuine dumps. Do not assume the others do:

- Both `.EXE` blocks store `0x00000000`.
- The `.BMP` field differs per image (`0x776F9CC9` on a 1013D, `0x03098A00` on
  the 1014D) and matches no sum over any range tried.
- A known-working patched image upstream carries a **stale** BT0 checksum, so
  the boot path does not appear to enforce it on SPI flash.

`fnirsi_splash.py` therefore leaves the BMP field untouched by default.

## Safety rules

1. **`0x1FD000` is per-unit calibration and cannot be replaced.** Two genuine
   dumps are byte-identical everywhere except two 4 KiB pages there. Nobody
   else's dump is a valid restore image for this scope.
2. **Never write before a verified backup exists.** Two independent reads,
   compared with `cmp`. A backup you cannot trust is worse than none, because
   you will rely on it.
3. **Never write to a device node without confirming it.** `/dev/sdX` in any
   documentation here is a placeholder.
4. Prefer building a patched copy (`-o patched.bin`) over editing in place.

## Files

```
fnirsi_splash.py    info / extract / replace on a flash dump. Parses geometry
                    from the block header rather than hardcoding it.
backup-scope.sh     Read-only. Double-reads the chip, compares, hashes, splits
                    out calibration, runs info + extract. Run this first.
bench-guide.html    The bench procedure, published as an artifact.
work/               Scratch: extracted splashes, patched images, comparisons.
upstream/           Vendored copies of pecostm32's repos (see below).
ossiloscope.jpg     Owner's artwork, git-ignored. Already exactly 298 × 98.
```

Typical use:

```bash
python3 fnirsi_splash.py info    dump.bin
python3 fnirsi_splash.py extract dump.bin -o stock.png -s 2
python3 fnirsi_splash.py replace dump.bin logo.png -o patched.bin
```

`replace` refuses to emit a file whose calibration region differs from the
source, resizes and letterboxes onto black, and Floyd-Steinberg dithers by
default — which matters, since flat truncation bands visibly on skin tones and
gradients at 5/6/5.

## Upstream

Everything rests on pecostm32's reverse engineering. Vendored under `upstream/`:

- `FNIRSI-1013D-1014D-Hack` — dumps, schematics, FPGA notes, sunxi tools.
  The **genuine 1014D flash image** is at
  `Test code/1014D_Emulator/FNIRSI_1014D_full_flash_backup.bin`.
  Original 1013D dumps are under `Binaries/Original files/`.
  `Binaries/Hacked files/` is upstream's own name for its patched images.
- `FNIRSI_1014D_Firmware`, `FNIRSI_1013D_Firmware` — replacement firmware.

Fetch with `curl` against `codeload.github.com`; `git clone` hangs in this
sandbox.

## Verified vs assumed

Verified locally against real dumps:

- The layout and splash format above, on **both** a 1013D and a genuine 1014D
  image. Extract-then-reinsert reproduces each source file **byte for byte** —
  this round trip is the evidence the format is right.
- Calibration is the only per-unit region.
- The BT0 checksum algorithm.

Not verified — nothing here has been run against hardware:

- Whether the owner's specific unit matches the upstream 1014D dump. Running
  `info` on their own dump is the gate before any write.
- The A/B display panel variants. Official firmware ships as `-A-` and `-B-`
  builds with a documented symptom of the image shifting left on the wrong one;
  whether that reaches the splash block is unknown.
- The `sunxi-fel spiflash-read` invocations are standard tool usage, not lifted
  from upstream docs. The `spiflash-write` and FEL SD-boot commands are
  upstream's.
- Latest official firmware appears to be `v3.0`, 2021-10-06, shipped as
  `FSI-1014.bin` on a USB stick. Not obtained — FNIRSI's download page builds
  links in JavaScript, and third-party mirrors were deliberately avoided.

## Working notes

- Say what is measured and what is inferred. This project has already had one
  wrong assumption (4 MiB) carried into a published document; the fix was
  checking against a real dump rather than reasoning harder.
- The owner's splash artwork is personal and is deliberately kept out of this
  repo (see `.gitignore`). Whatever goes in the splash shows on every power-on,
  so it is more public than a wallpaper — worth a mention, not a lecture.
- `var()` does not work in SVG presentation attributes. In `bench-guide.html`
  every SVG fill and stroke comes from a CSS class for this reason.
