#!/usr/bin/env python3
"""device_probe.py — build `dist/Probe.face`, a stock-Lua device probe.

Why this exists
---------------
Everything a native module needs is now recovered from the firmware
(see `native-app/targets/`), but three questions can only be answered by a
device, and all three decide the design:

  1. Does the **watchface** Lua state open the standard `io`/`os` libraries?
     (They are linked into the firmware; whether that state opens them is not
     decidable statically — see `docs/RE-notes.md` §6.)
  2. Which paths on the device are readable / writable from a watchface?
  3. Is a **Canopus / AstroBox manager already installed**? If it is, its files
     are on the device filesystem and that is the "answer sheet" for the whole
     framework — far more useful than any further static analysis.

This tool packs `app/lua/probe.lua` into a copy of a stock Lua watchface (same
in-place injection the Pomodoro face uses) and gives it its own watchface id so
it can sit next to `Pomodoro.face`. Install it like any custom face, open it,
and read the results on screen (tap = next page). It also tries to save
`vw_probe.txt` next to the script.

Usage
-----
  python3 tools/device_probe.py                       # -> dist/Probe.face
  python3 tools/device_probe.py --stock <stock.face> --id 491552739
  python3 tools/device_probe.py --out /tmp/Probe.face
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from facepack import (  # noqa: E402
    MAGIC,
    iter_segments,
    read_face,
    set_id,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULT_STOCK = "/tmp/fw/stockfaces/face_14/resource.bin"
DEFAULT_LUA = os.path.join(ROOT, "app", "lua", "probe.lua")
DEFAULT_OUT = os.path.join(ROOT, "dist", "Probe.face")
DEFAULT_ID = 491552739  # next to Pomodoro.face (491552737/491552738)


def find_lua_segments(data):
    """All `_lua/.../*.lua` name segments, in container order."""
    out = []
    for hoff, poff, psize, name, total in iter_segments(data):
        if name.endswith(".lua") and name.startswith("_lua/"):
            out.append((hoff, poff, psize, name, total))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stock", default=DEFAULT_STOCK)
    ap.add_argument("--lua", default=DEFAULT_LUA)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--id", default=str(DEFAULT_ID))
    args = ap.parse_args(argv)
    if not str(args.id).isdigit():
        raise SystemExit("watchface id must be decimal digits, got %r" % args.id)

    if not os.path.exists(args.stock):
        raise SystemExit(
            "stock host face not found: %s\n"
            "  pass --stock <face_14 resource.bin> (extract it from the OTA with\n"
            "  `python3 tools/symbol_hunt.py unpack-ota 3.1.175.bin <dir>`)" % args.stock
        )

    stock = read_face(args.stock)
    script = open(args.lua, "rb").read()
    segments = find_lua_segments(stock)
    if not segments:
        raise SystemExit("no _lua/.../*.lua segments in %s" % args.stock)

    built = bytearray(stock)
    regions = []
    for hoff, poff, psize, name, total in segments:
        if len(script) > psize:
            # Try the other theme slots: stock faces give all of them the same
            # size, so this is a hard limit of the host container.
            raise SystemExit(
                "probe script is %d bytes but segment %r holds %d; shrink it"
                % (len(script), name, psize)
            )
        pad = psize - len(script)
        payload = script
        if pad:
            filler = bytearray()
            while len(filler) < pad:
                room = pad - len(filler)
                chunk = min(room, 100)
                if chunk <= 2:
                    filler += b" " * chunk
                else:
                    filler += b"--" + b" " * (chunk - 3) + b"\n"
            payload = script + bytes(filler[:pad])
        assert len(payload) == psize
        built[poff:poff + psize] = payload
        regions.append((poff, poff + psize))
        print("  injected %-32s %d bytes (capacity %d)" % (name, len(script), psize))

    built = bytearray(set_id(bytes(built), str(args.id)))
    regions.append((0x28, 0x34))
    result = bytes(built)

    # ---- verification -----------------------------------------------------
    if len(result) != len(stock):
        raise SystemExit("container size changed: %d -> %d" % (len(stock), len(result)))
    outside = [
        i for i in range(len(stock))
        if stock[i] != result[i] and not any(a <= i < b for a, b in regions)
    ]
    assert not outside, "unexpected changes at %s" % [hex(i) for i in outside[:8]]
    for i in (4, 5):
        assert stock[i] == result[i], "header byte 0x%02x was modified" % i
    assert int.from_bytes(result[0:4], "little") == MAGIC, "bad magic"
    print("  container diff: %d lua payloads + ID slot only" % len(segments))

    try:
        from lupa import LuaRuntime
        lua = LuaRuntime(unpack_returned_tuples=True)
        check = lua.eval("function(s) local f, e = load(s, 'p') return f ~= nil, e end")
        for a, b in regions[:-1]:
            ok, err = check(result[a:b].decode("latin-1"))
            assert ok, "Lua parse failed at 0x%x: %s" % (a, err)
        print("  lua: valid Lua 5.4")
    except ImportError:
        print("  lua: lupa not installed, parse check skipped")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as fh:
        fh.write(result)
    print("wrote %s (id %s, %d bytes)" % (args.out, args.id, len(result)))
    print("install like any custom face, then open it and read the screen")
    print("(tap = next page; it also tries to save vw_probe.txt next to the script)")


if __name__ == "__main__":
    main()
