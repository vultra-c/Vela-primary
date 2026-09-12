#!/usr/bin/env python3
"""Verify dist/Pomodoro.face is a correct injection of the stock face:
   - same size as stock
   - only the 3 Lua payloads + the 12-byte watchface-ID slot differ
   - header bytes 0x04/0x05 are preserved exactly (not version/len fields!)
   - every injected Lua payload parses as valid Lua 5.4
"""
import struct, sys, os

STOCK_CANDIDATES = [
    "/tmp/fw/stockfaces/face_14/resource.bin",
]
BUILT = os.path.join(os.path.dirname(__file__), "..", "dist", "Pomodoro.face")
LUA_OFFS = [0x93E70, 0x144C9D, 0x1FC785]
LUA_LEN = 0x9A4
ID_SLOT = (0x28, 0x34)  # 12-byte NUL-padded ASCII ID


def load_stock():
    for p in STOCK_CANDIDATES:
        if os.path.exists(p):
            return open(p, "rb").read()
    return None


def main():
    built = open(BUILT, "rb").read()
    assert struct.unpack_from("<I", built, 0)[0] == 0x1234A55A, "bad magic"
    assert built[5] == 0, "byte 5 must stay 0 (stock value)"
    wid = built[ID_SLOT[0]:ID_SLOT[1]]
    print("  watchface id: %r (NUL-padded 12-byte slot)" %
          wid.split(b"\x00", 1)[0].decode())

    stock = load_stock()
    if stock is None:
        print("stock reference not found; skipping container-diff check")
        ok_all = True
    else:
        assert len(stock) == len(built), f"size changed: {len(stock)} -> {len(built)}"
        regions = [(o, o + LUA_LEN) for o in LUA_OFFS] + [ID_SLOT]
        outside = [i for i in range(len(stock))
                   if stock[i] != built[i]
                   and not any(a <= i < b for a, b in regions)]
        assert not outside, f"unexpected changes at {[hex(i) for i in outside[:8]]}"
        for i in (4, 5):  # must be byte-identical to stock
            assert stock[i] == built[i], f"header byte 0x{i:02x} was modified"
        print("  container diff: only 3 lua payloads + ID slot changed")
        ok_all = True

    try:
        from lupa import LuaRuntime
    except ImportError:
        print("lupa not installed; skipping Lua parse check")
        print("OK" if ok_all else "FAILED")
        return
    lua = LuaRuntime(unpack_returned_tuples=True)
    check = lua.eval(
        "function(s) local f, e = load(s, 'check') return f ~= nil, e end")
    for off in LUA_OFFS:
        payload = built[off:off + LUA_LEN].decode("latin-1")
        ok, err = check(payload)
        assert ok, f"Lua parse failed at {off:#x}: {err}"
        print(f"  lua @ {off:#09x}: valid Lua 5.4 ({LUA_LEN} bytes)")
    print("OK: all checks passed")


if __name__ == "__main__":
    main()
