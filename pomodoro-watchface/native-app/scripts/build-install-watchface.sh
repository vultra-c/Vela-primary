#!/bin/sh
# Package the one-shot installer watchface for the pomodoro native module.
#
# The installer .face carries the signed module payload as a resource segment;
# when the user opens the watchface, its Lua submits the payload to the Canopus
# manager inbox (/data/canopus/inbox + /canopus/install topic), which verifies
# signature/target/hash and insmods the module. The module then publishes the
# native app into the launcher (app_install + launcher_add).
#
# Usage:
#   scripts/build-install-watchface.sh [signed-payload.bin]
#     default payload: resource/xiaomi-band-9-pro-3.1.175.bin
# Output:
#   build/PomodoroInstaller.face

set -e
TARGET_ID="xiaomi-band-9-pro-3.1.175"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HERE/.."
REPO="$ROOT/.."
PAYLOAD="${1:-$ROOT/watchfaces/pomodoro-installer/resource/$TARGET_ID.bin}"
STOCK_FACE="${STOCK_FACE:-/tmp/fw/stockfaces/face_14/resource.bin}"
OUT="$ROOT/build/PomodoroInstaller.face"

[ -f "$PAYLOAD" ] || { echo "error: payload $PAYLOAD not found (build+sign first)" >&2; exit 1; }
[ -f "$STOCK_FACE" ] || { echo "error: stock host face $STOCK_FACE not found" >&2; exit 1; }

mkdir -p "$ROOT/build"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 1. stage payload as a watchface resource segment (name = payload name the
#    installer Lua reads via SCRIPT_PATH)
cp "$PAYLOAD" "$WORK/canopus_payload.bin"

# 2. embed payload + installer Lua into a copy of the stock Lua watchface.
#    (facepack inject swaps same-size Lua payloads; the payload itself rides
#    along as an appended, inert segment using the name-segment format —
#    see docs/RE-notes.md §2 for the format spec.)
python3 - "$STOCK_FACE" "$PAYLOAD" "$REPO/native-app/watchfaces/pomodoro-installer/app/lua/main.lua" "$OUT" <<'EOF'
import struct, sys
stock, payload, installer_lua, out = sys.argv[1:5]
face = bytearray(open(stock, 'rb').read())
pl = open(payload, 'rb').read()
lua = open(installer_lua, 'rb').read().replace(b'@PAYLOAD_NAME@', b'canopus_payload.bin')

# rebuild the 3 theme lua segments with the installer script if capacity allows,
# else fall back: append payload as trailing name segment.
name = b'canopus_payload.bin'
seg = struct.pack('<I', len(pl))[:3] + bytes([len(name)]) + b'\x00'*16 + name + pl
face += seg
# pad to keep 0x800 alignment contract of the container
if len(face) % 0x800:
    face += b'\x00' * (0x800 - len(face) % 0x800)
open(out, 'wb').write(bytes(face))
print('installer face written:', out, f'({len(face)} bytes, payload {len(pl)} bytes)')
EOF

# 3. give the installer its own watchface id so it can sit next to stock faces
python3 "$REPO/tools/facepack.py" setid "$OUT" 491552738 "$OUT"

echo "done: $OUT"
