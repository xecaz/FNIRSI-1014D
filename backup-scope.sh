#!/usr/bin/env bash
#
# backup-scope.sh - take a verified backup of a FNIRSI 1014D's SPI flash.
#
# READ ONLY. Nothing here writes to the scope. Run it before anything else,
# and do not proceed to a write until it prints ALL CHECKS PASSED.
#
# Prerequisite: the scope is in FEL mode and enumerating as 1f3a:efe8.
# See the bench guide, step 1.
#
# Usage:  ./backup-scope.sh [output-directory]

set -euo pipefail

OUTDIR="${1:-backup-$(date +%Y%m%d-%H%M%S)}"
FEL="${FEL:-./sunxi-fel}"

# 1014D is a 2 MiB part. The later 1013D is 4 MiB; if `info` later reports
# geometry that makes no sense, check this first.
SIZE=0x200000

# Per-unit calibration. 0x1FD000 / 4096 = page 509, two 4 KiB pages.
CAL_SKIP=509
CAL_COUNT=2

say(){ printf '\n\033[1m== %s\033[0m\n' "$*"; }
die(){ printf '\n\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

if [ -x "$FEL" ]; then
  FEL="$(cd "$(dirname "$FEL")" && pwd)/$(basename "$FEL")"
elif command -v "$FEL" >/dev/null 2>&1; then
  FEL="$(command -v "$FEL")"
else
  die "sunxi-fel not found at '$FEL'. Set FEL=/path/to/sunxi-fel."
fi
PROJECT="$(pwd)"

say "Checking the scope is in FEL mode"
if ! lsusb | grep -q '1f3a:efe8'; then
  die "scope not found on USB (expected 1f3a:efe8).
     Insert the FEL SD card, power-cycle the scope, and try again."
fi
lsusb | grep '1f3a:efe8'

"$FEL" version || die "sunxi-fel could not talk to the device"

mkdir -p "$OUTDIR"
cd "$OUTDIR"

say "Read 1 of 2  (${SIZE} bytes)"
sudo "$FEL" spiflash-read 0 "$SIZE" backup-1.bin \
  || die "first read failed - is this a sunxi-fel build with SPI support?"

say "Read 2 of 2  (independent read, for comparison)"
sudo "$FEL" spiflash-read 0 "$SIZE" backup-2.bin \
  || die "second read failed"

sudo chown "$(id -u):$(id -g)" backup-1.bin backup-2.bin 2>/dev/null || true

say "Comparing the two reads"
cmp backup-1.bin backup-2.bin \
  || die "the two reads DISAGREE. The dump is unreliable - do not trust it,
     and do not write anything to the scope. Reseat the cable and retry."
echo "reads agree"

say "Sizes and hashes"
ls -l backup-1.bin | awk '{print "  size:", $5, "bytes"}'
EXPECT=$((SIZE))
ACTUAL=$(stat -c%s backup-1.bin)
[ "$ACTUAL" -eq "$EXPECT" ] || die "expected $EXPECT bytes, got $ACTUAL"
sha256sum backup-1.bin | tee backup.sha256

say "Extracting the calibration region on its own"
dd if=backup-1.bin of=calibration-0x1FD000.bin \
   bs=4096 skip="$CAL_SKIP" count="$CAL_COUNT" status=none
ls -l calibration-0x1FD000.bin | awk '{print "  size:", $5, "bytes"}'
if [ "$(tr -d '\0' < calibration-0x1FD000.bin | wc -c)" -eq 0 ]; then
  printf '\033[33m  warning: calibration region is all zeroes - unexpected.\033[0m\n'
fi

say "Layout of your scope"
cd "$PROJECT"
python3 fnirsi_splash.py info "$OUTDIR/backup-1.bin"

say "Extracting your stock splash"
python3 fnirsi_splash.py extract "$OUTDIR/backup-1.bin" \
        -o "$OUTDIR/stock-splash.png" -s 2

printf '\n\033[32m== ALL CHECKS PASSED ==\033[0m\n'
cat <<EOF

  $OUTDIR/backup-1.bin              full chip image, verified by double read
  $OUTDIR/calibration-0x1FD000.bin  the irreplaceable part, on its own
  $OUTDIR/backup.sha256             hash of the backup
  $OUTDIR/stock-splash.png          your scope's current boot logo

Now copy $OUTDIR OFF THIS MACHINE before going any further.

Then check stock-splash.png actually looks like your scope's boot logo, and
that the geometry above matches the image you want to install. Only then
build a patched image:

  python3 fnirsi_splash.py replace $OUTDIR/backup-1.bin ossiloscope.jpg -o patched.bin

EOF
