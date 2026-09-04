# FNIRSI 1014D — boot splash reverse engineering

Goal: replace the boot logo on the owner's FNIRSI 1014D oscilloscope without
destroying it. Some sellers rebadge this scope as **SiRyder**; searches should
include that name and the closely related **1013D**, which shares most of its
design.

**Status: done, 2026-09-04.** The owner's scope runs a full-screen 800 × 480
splash from a relocated block at `0x100000` with a two-word SPL patch, verified
by full-chip read-back, calibration untouched. The FEL stub is cleared and it
boots on its own. This file is now a record of what was measured on that
hardware, not a plan — read the **Verified vs assumed** section before treating
anything here as still open.

Public repo at `github.com/xecaz/FNIRSI-1014D`. No build step, no dependencies
beyond Pillow + NumPy.

**The repo is public.** No flash dumps go in it — they carry per-unit
calibration and are specific to one physical scope. `.gitignore` covers `*.bin`,
`backup-*/`, `work/` and `upstream/`; check it before adding files.

Artwork is the owner's call, made once and already made: `new.jonash3.png` (the
installed 800 × 480 splash, eyes pixelated) **is committed and public**, at the
owner's explicit instruction after being told git history is permanent.
`ossiloscope.jpg` — the earlier, unpixelated 298 × 98 artwork — stays ignored,
and nothing derived from it belongs in the repo or in a published page.

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

| Offset | Block | 1014D v3.0 (measured) | upstream 1014D | 1013D |
|---|---|---|---|---|
| `0x000000` | `eGON.BT0` — SPL, draws the splash | `0x3400` | `0x3400` | `0x3400` |
| `0x006000` | `eGON.EXE` — FEL helper | `0xBA00` | `0xBA00` | `0x7A00` |
| `0x013000` | **`eGON.BMP` — the splash** | `0x13000` | `0xE600` | `0xE600` |
| `0x027000` | `eGON.EXE` — scope app | `0xBCE00` | `0xBC400` | `0x193A00` |
| `0x1FD000` | calibration **and configuration** | ~`0x1F4` | ~`0x1F4` | ~`0x1F4` |

**The splash block is _not_ identical across firmwares.** An earlier version of
this file claimed it was — same offset, same size, same 298 × 98 — and hardware
disproved it. A genuine 1014D running **firmware v3.0** has a `0x13000`-byte
block holding a **298 × 130** image; the extra 32 rows are the "Firmware
version: V3.0" line. Upstream's 1014D dump is an older build: 298 × 98 in
`0xE600`. The two dumps differ in **1,566,094 of 2,097,152 bytes**.

Only the offset `0x013000` and the format are stable. Read geometry from the
block header, never from this table — which is what `fnirsi_splash.py` does, and
why it read a v3.0 scope correctly with no changes.

Free space runs from the end of the app (`0xE3E00` on v3.0) to `0x1FD000` —
1,151,488 bytes, enough for a full-screen 800 × 480 splash. See
**Full-screen splash** below.

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
   It also holds **live configuration the firmware rewrites in normal use** —
   measured: `0x1FD014` went `0x04` → `0x03` across one ordinary boot, with no
   write from us. So two dumps of *your own* scope will differ here too. This
   does not weaken the rule; it means a re-read is not proof of a stray write.
2. **Never write before a verified backup exists.** Two independent reads,
   compared with `cmp`. A backup you cannot trust is worse than none, because
   you will rely on it.
3. **Never write to a device node without confirming it.** `/dev/sdX` in any
   documentation here is a placeholder.
4. Prefer building a patched copy (`-o patched.bin`) over editing in place.

## Getting into FEL — measured on hardware

**The 1014D has no external SD slot.** The bench guide's original step 1 ("write
the stub to a microSD, insert it") was inherited from 1013D material and is
wrong for this scope. The card is *internal*, and the boot ROM checks it before
SPI flash, so it is still the way in — you reach it over USB.

1. Boot the scope, open its **USB connection screen**. It enumerates as
   `0483:5720` (ST's example IDs, which upstream's `mass_storage_class.c`
   reproduces verbatim) with SCSI vendor `ADS1014D`. That disk *is* the SoC's
   MMC0, served by `sd_card_read`/`sd_card_write`.
2. `dd if=fel-sdboot.sunxi of=/dev/sdX bs=1024 seek=8` — byte 8192, i.e. sector
   16. The FAT32 partition starts at **sector 63 = byte 32256**, so the 8 KiB
   stub sits in zeroed unallocated space with 15,872 bytes to spare.
3. Power-cycle. The scope comes up as `1f3a:efe8` with a blank screen. That is
   FEL, not a fault.

### Getting back out — the trap

The stub is self-perpetuating: FEL never runs the app, and the app is the only
thing that exposes the card. **You cannot undo step 2 the way you did it.**

Booting the app from FEL does not work, and several plausible routes were tried
and failed on real hardware:

- `sunxi-fel spl <FNIRSI SPL>` — draws the splash, then returns to FEL.
  `sunxi-fel spl` patches an SPL to hand control back when it finishes.
- `write 0x0 <spl> exe 0x0` — `ERROR -1`. FEL's exception vectors live in SRAM
  at `0x0`; writing there kills FEL mid-transfer. Anything linked at `0x0` must
  go through `spl`.
- `write 0x7FFFFFE0 <app> exe 0x80000000` in a fresh FEL — DRAM is not
  initialised (`readl 0x80000000` returns zeros), so this jumps into nothing.
- peco's bootloader patched to boot from SPI, with and without caches enabled —
  loads the v3.0 app and jumps, black screen every time.
- peco's bootloader patched to its "FEL mode" branch (`0xFFFF0020`) — DRAM comes
  up but FEL does not re-enter cleanly; screen fills with uninitialised
  framebuffer noise and bulk transfers time out.

**What works:** `work/sd-wipe.bin`, built by `work/build-sd-wipe.py`. It takes
upstream's `fnirsi_1014d_startup` bootloader — which already brings up clocks,
DRAM, display and the SD card, and links in `sd_card_write()` — and replaces its
dead SD-boot path with ~100 bytes that zero sector 16 for 16 blocks. Screen goes
green on success, red on failure. Don't boot the scope to fix the card; run code
that writes to the card.

### sunxi-fel behaviour worth knowing

- **A FEL session is single-use.** After any `spiflash-write` or `spl`, the next
  bulk transfer fails with `usb_bulk_send() ERROR -7`. Power-cycle between
  every operation. Most of one evening was lost to reading this as failure.
- After running code that never returns, `ERROR -7` is the *expected* result —
  judge by the scope's screen, not the terminal.
- `lsusb` caches. A running app that doesn't drive USB leaves the old FEL entry
  listed, so `lsusb` cannot distinguish "app running" from "still in FEL".

## Full-screen splash

Done and working: 800 × 480, the full panel. The stock block cannot hold it
(77,784 bytes of payload, and the app starts right behind at `0x27000`), so the
bitmap is relocated and the SPL repointed.

The SPL's memory map, from disassembling it:

```
0x50c   mov r5, 0x80000000       app load address
0x510   mov sb, 0x81000000       splash pixel buffer  (11,534,336 B of headroom)
0x514   add r7, r5, 0x1b00000    framebuffer at 0x81B00000
0x548   mov r0, #0x13000         bitmap header address   <- instruction immediate
0x75c   .word 0x13028            bitmap pixel address    <- literal
```

It reads size from header `0x10`, width from `0x1A`, height from `0x1C`, and
centres with `(800-w)/2, (480-h)/2` — so 800 × 480 lands at `(0,0)`. Header
fields `0x18` (`0x1000`) and `0x1E` (`0x1B01`) are identical on 298 × 98 and
298 × 130 firmware, so they are constants; copy them.

`work/build-bigsplash.py` writes a new `eGON.BMP` at `0x100000` (`0xBC000`
bytes, ending `0x1BC000`, clear of calibration) and patches those two SPL words.
Seven bytes change in the SPL: the two operands and the BT0 checksum.

- **Flash the bitmap first, the SPL last.** Until the SPL changes it still
  points at `0x13000`, so an interruption leaves a bootable scope.
- **Leave the old block at `0x13000` in place — this is not optional.** The SPL
  is not the only consumer of the splash. The **scope app draws it again on
  power-down**, and its reference still points at `0x13000`. Observed on
  hardware: after relocating the boot splash to `0x100000`, power-off still
  showed the old 298 × 130 image. Overwrite `0x13000` and you corrupt the
  shutdown screen. It also keeps a revert down to two SPL words.
- Consequence: a v3.0 scope patched this way shows **the large splash at boot
  and the small one at shutdown**. Matching them would mean finding and patching
  the app's own reference inside the `0xBCE00` block at `0x27000` — not
  attempted, and there is no source for v3.0.
- Rewriting the SPL is the only step here that can stop the scope booting from
  SPI. Recovery is the FEL route above plus `spiflash-write 0 <backup SPL>`.

## Files

```
fnirsi_splash.py    info / extract / replace on a flash dump. Parses geometry
                    from the block header rather than hardcoding it.
backup-scope.sh     Read-only. Double-reads the chip, compares, hashes, splits
                    out calibration, runs info + extract. Run this first.
                    Needs sudo for the FEL `version` call as well as the reads.
build-sd-wipe.py    Builds the payload that erases the FEL stub from the
                    internal card — the way out of the trap in the section
                    above. Takes [bootloader] [output]. Read it before you run
                    it; it assembles ~100 bytes of ARM into someone else's
                    binary.
build-bigsplash.py  Builds the full-screen 800 × 480 bitmap block plus the
                    two-word SPL patch. Takes [flash-image] [picture] [output].
                    Refuses to patch an SPL that does not contain the expected
                    instruction and literal.
bench-guide.html    The bench procedure, published as an artifact at
                    claude.ai/code/artifact/b8cea1db-78ab-450f-9b65-539814713cc1
                    Rewritten from hardware; update it there, not by publishing
                    a new one.
index.html          The narrative writeup, "8 KiB from a brick". Self-contained
                    — the owner's site CSS inlined, both splash renders as data
                    URIs, no external requests. Keep it that way.
work/               Scratch: dumps, patched images, comparisons. Git-ignored.
upstream/           Vendored copies of pecostm32's repos (see below).
ossiloscope.jpg     Earlier artwork, git-ignored. 298 × 98 — a v3.0 scope's slot
                    is 298 × 130, so it needs --stretch or a letterbox.
```

Typical use:

```bash
python3 fnirsi_splash.py info    dump.bin
python3 fnirsi_splash.py extract dump.bin -o stock.png -s 2
python3 fnirsi_splash.py replace dump.bin logo.png -o patched.bin
```

`replace` refuses to emit a file whose calibration region differs from the
source, resizes and letterboxes onto black (or fills the slot exactly with
`--stretch`), and Floyd-Steinberg dithers by default — which matters, since flat
truncation bands visibly on skin tones and gradients at 5/6/5.

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

Verified **on the owner's scope**, 2026-09-04, end to end:

- SoC `0x1663` (F1C100s) and a Winbond `EFh`/`40h` **2,097,152-byte** flash,
  reported by `spiflash-info` — the 2 MiB figure confirmed against the chip
  rather than against a dump.
- The layout and splash format, now on three images including this unit.
  Extract-then-reinsert is byte-exact.
- A 298 × 130 splash replaced, read back **byte-identical to the intended
  image**, and rendered correctly on the panel.
- A full-screen 800 × 480 splash at `0x100000` with a patched SPL, likewise
  read back byte-identical and rendered correctly.
- FEL entry via the internal card, and `sd-wipe.bin` to undo it.
- The BT0 checksum algorithm, on the stock SPL, upstream's `fel-sdboot.sunxi`,
  and a patched SPL that boots.
- The scope runs **firmware v3.0** — printed in its own stock splash. CLAUDE.md
  previously listed v3.0 as "not obtained"; this unit shipped with it.

Still not verified:

- The A/B display panel variants. Official firmware ships as `-A-` and `-B-`
  builds with a documented symptom of the image shifting left on the wrong one.
  The splash rendered correctly here with no horizontal offset, on both
  geometries — one unit, so evidence, not proof.
- Why the v3.0 app will not start when loaded by upstream's bootloader. It is
  loaded and jumped to; the screen stays black. Cache coherency was the obvious
  suspect and NOPing the two cache enables did not change it.
- The `.BMP` checksum field still matches no sum over any range tried, and is
  left untouched. A patched bitmap with a stale field boots fine — now
  confirmed on hardware, twice.

## Working notes

- Say what is measured and what is inferred. This project has already had one
  wrong assumption (4 MiB) carried into a published document; the fix was
  checking against a real dump rather than reasoning harder.
- Whatever goes in the splash shows on every power-on, so it is more public
  than a wallpaper — worth a mention, not a lecture. That mention was made, the
  owner decided, and the decision is recorded at the top of this file. Do not
  re-litigate it.
- `var()` does not work in SVG presentation attributes. In `bench-guide.html`
  every SVG fill and stroke comes from a CSS class for this reason.
- The owner runs every privileged command themselves, in their own terminal:
  `sudo` cannot prompt through Claude Code's `!` prefix. Hand over **one-line**
  commands — `\` continuations get mangled on paste — and say what output means
  success, because you will not see it unless they paste it back.
- `index.html` borrows the CSS from the owner's site verbatim. When editing it,
  reuse the existing class vocabulary (`.ev`, `.dead`, `.tag ok|inf|bad`,
  `.pull`, `.compare`, `.shot-label`) rather than adding classes, and watch
  specificity — `.shot figcaption` beats `.shot-label`, which is why the
  before/after labels are `div`s.
