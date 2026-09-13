#!/bin/sh
# Build the pomodoro Canopus module for Mi Band 9 Pro firmware 3.1.175.
#
# Mirrors the reference module's build-device.sh:
#   1. nightly cross-compile for cortex-m33 (thumbv8m.main-none-eabi)
#   2. ld.lld -r (relocatable link, like a kernel module)
#   3. objcopy strip of thin-LTO bytecode + debug sections
#   4. Canopus ELF verifier + CMI1 signing happen inside the private framework
#      (scripts/sign.sh of AstralSightStudios/Canopus)
#
# Requirements:
#   rustup toolchain install nightly
#   git clone https://github.com/AstralSightStudios/Canopus  (sibling folder)
#
# Output: build/xiaomi-band-9-pro-3.1.175/pomodoro.elf

set -e
CANOPUS_ROOT="${CANOPUS_ROOT:-$(dirname "$0")/../../../Canopus}"
TARGET_ID="xiaomi-band-9-pro-3.1.175"
OUT_DIR="$(dirname "$0")/../build/$TARGET_ID"
NIGHTLY_CARGO="${NIGHTLY_CARGO:-cargo +nightly}"
RUST_OBJCOPY="${RUST_OBJCOPY:-rust-objcopy}"

cd "$(dirname "$0")/.."

if [ ! -d "$CANOPUS_ROOT" ]; then
    echo "error: Canopus framework not found at $CANOPUS_ROOT" >&2
    echo "       clone it and/or set CANOPUS_ROOT (closed source, access required)" >&2
    exit 1
fi

export CANOPUS_TARGET="$TARGET_ID"

"$NIGHTLY_CARGO" build --release --features device \
    -Z unstable-options \
    --target thumbv8m.main-none-eabi

mkdir -p "$OUT_DIR"
# Relocatable link (kernel-module style) with size-focused section handling.
ld.lld -r -o "$OUT_DIR/pomodoro.elf" \
    target/thumbv8m.main-none-eabi/release/libpomodoro_canopus.rlib
"$RUST_OBJCOPY" --remove-section=.llvmbc --strip-debug "$OUT_DIR/pomodoro.elf"

echo "built: $OUT_DIR/pomodoro.elf"
echo "next:  sign with the framework CLI (CMI1), then place the signed payload in"
echo "       watchfaces/pomodoro-installer/resource/$TARGET_ID.bin"
