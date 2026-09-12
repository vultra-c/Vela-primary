#!/usr/bin/env python3
"""
facepack.py — Xiaomi Vela (Mi Band 9 Pro) .face watchface packer/unpacker.

Reverse-engineered container format (from firmware 3.1.175 OTA, vela_ap.bin):

  .face / resource.bin container:
    Header:
      0x00  u32  magic 0x1234A55A   (bytes: 5A A5 34 12)
      0x04  u8   per-face byte (values 0..38 observed across stock faces;
                 purpose unknown — always preserve from the host container)
      0x05  u8   0x00 in every stock face (preserve)
      0x28  12B  watchface ID as ASCII, NUL-padded (e.g. "120917345196";
                 same slot EasyFace/LuaDevTemplate patch)
      0xAC  u32  payload start (header end)
      (rest of header: style/widget/font tables referencing payload offsets)

    Payload (from 0xAC to EOF) = sequence of blocks:
      A) resource block: {u32 raw_size, u16 w, u16 h, u32 comp_len} + image data
         (A55A21E0 magic variants, raw LVGL pixels, etc.)
      B) name segment ("kind5"): 
         {u24 payload_size, u8 name_len, 16B zeros} + name (no NUL) + payload
         e.g. "_lua/theme1/theme1.lua" -> plain-text Lua source
         "_lua/theme1/1.bin"      -> LVGL image / font data

    Header tables record 16-byte entries {kind:u32, res:u32, off:u32, size:u32}
    where kind>>24 selects the class (2=image, 3=asset, 5=named file) and
    off/size point at payload blocks. Styles map widget ids to these entries.

  Install paths on device (from firmware strings):
    /system/watchface/<id>/          builtin faces
    /data/app/watchface/market/<id>/ user-installed faces (resource.bin inside)
    /data/app/watchface/watchface_list.json   face registry (id/name/type/...)

Usage:
  facepack.py unpack <face.bin> <outdir>       extract all name segments
  facepack.py list <face.bin>                  show container contents
  facepack.py inject <face.bin> <luafile> <segname> [out.bin]
                                               replace a .lua payload in place
                                               (keeps header tables valid)
  facepack.py setid <face.bin> <newid> [out]   patch the 12-byte watchface ID

The inject path is what makes "system native app injection" work: the stock
watchface already contains Lua script segments; we swap their payload with ourown script of exactly the same byte size (padding with '-- ' comments), so
every header offset/table stays valid without rebuilding the container."""

import struct
import sys
import os
import re

MAGIC = 0x1234A55A
ID_OFFSET = 0x28
ID_SIZE = 12  # verified: stock IDs are 9 or 12 ASCII digits, NUL-padded


def read_face(path):
    with open(path, "rb") as f:
        data = f.read()
    magic, = struct.unpack_from("<I", data, 0)
    if magic != MAGIC:
        raise ValueError(f"bad magic {magic:#x} (expected {MAGIC:#x})")
    return data


def get_id(data):
    raw = data[ID_OFFSET:ID_OFFSET + ID_SIZE]
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace")


def set_id(data, new_id):
    b = bytearray(data)
    idb = new_id.encode("ascii")
    if len(idb) > ID_SIZE:
        raise ValueError("watchface ID too long (max 12 characters)")
    b[ID_OFFSET:ID_OFFSET + ID_SIZE] = idb.ljust(ID_SIZE, b"\x00")
    return bytes(b)


def data_start(data):
    return struct.unpack_from("<I", data, 0xAC)[0]


def iter_segments(data):
    """Yield (abs_off, header_off, name, payload_off, payload_size, total_len)
    for every name segment found anywhere in the file."""
    n = len(data)
    i = 0
    while i < n - 20:
        if data[i + 4:i + 20] == b"\x00" * 16:
            size = data[i] | data[i + 1] << 8 | data[i + 2] << 16
            namelen = data[i + 3]
            if 0 < namelen < 64 and size and i + 20 + namelen + size <= n:
                name = data[i + 20:i + 20 + namelen]
                if re.fullmatch(rb"[\x20-\x7e]{4,63}", name):
                    yield (i, i + 20 + namelen, size,
                           name.decode("ascii"), 20 + namelen + size)
                    i += 20 + namelen + size
                    continue
        i += 1


def cmd_list(args):
    data = read_face(args[0])
    ds = data_start(data)
    print(f"file        : {args[0]}")
    print(f"size        : {len(data)} bytes")
    print(f"watchface id: {get_id(data)!r}")
    print(f"payload @   : {ds:#x}")
    print(f"{'segment':40s} {'off':>9s} {'size':>9s}")
    for hoff, poff, psize, name, total in iter_segments(data):
        print(f"{name:40s} {poff:#09x} {psize:#09x}")


def cmd_unpack(args):
    face, outdir = args
    data = read_face(face)
    os.makedirs(outdir, exist_ok=True)
    count = 0
    for hoff, poff, psize, name, total in iter_segments(data):
        rel = name.lstrip("_")
        dest = os.path.join(outdir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data[poff:poff + psize])
        print(f"  {name} -> {dest} ({psize} bytes)")
        count += 1
    print(f"extracted {count} segments")


def cmd_inject(args):
    face, luafile, segname = args[0], args[1], args[2]
    out = args[3] if len(args) > 3 else None
    data = read_face(face)
    with open(luafile, "rb") as f:
        new_lua = f.read()

    target = None
    for hoff, poff, psize, name, total in iter_segments(data):
        if name == segname:
            target = (hoff, poff, psize)
            break
    if target is None:
        raise SystemExit(f"segment {segname!r} not found in {face}; "
                         f"use 'list' to see available segments")
    hoff, poff, psize = target

    if len(new_lua) > psize:
        raise SystemExit(
            f"new script is {len(new_lua)} bytes but segment capacity is "
            f"{psize}; shrink the script (minify) to fit")
    # pad with a Lua line comment so the payload size is preserved exactly
    pad = psize - len(new_lua)
    if pad:
        filler = b"--" + b" " * max(0, pad - 2 - 1) + b"\n"
        # build padding that is exactly `pad` bytes and line-comment safe
        filler = bytearray()
        while len(filler) < pad:
            room = pad - len(filler)
            chunk = min(room, 100)
            if chunk <= 2:
                filler += b" " * chunk
            else:
                filler += b"--" + b" " * (chunk - 3) + b"\n"
        new_lua = new_lua + bytes(filler[:pad])
    assert len(new_lua) == psize

    b = bytearray(data)
    b[poff:poff + psize] = new_lua
    result = bytes(b)
    dest = out or face
    with open(dest, "wb") as f:
        f.write(result)
    print(f"injected {luafile} into {segname} "
          f"({psize} bytes, padded {pad}) -> {dest}")


def cmd_setid(args):
    face = args[0]
    new_id = args[1]
    out = args[2] if len(args) > 2 else None
    result = set_id(read_face(face), new_id)
    dest = out or face
    with open(dest, "wb") as f:
        f.write(result)
    print(f"watchface id set to {new_id} -> {dest}")


def selftest(data):
    """Sanity-check set_id/get_id round-trip against the observed stock layout."""
    assert get_id(set_id(data, "491552737")) == "491552737"
    b = set_id(data, "120917345196")
    assert b[0x28:0x34] == b"120917345196"  # full 12-byte slot holds the ID
    assert b[4] == data[4] and b[5] == data[5]  # header bytes 4/5 untouched


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        selftest(read_face(sys.argv[2]))
        print("selftest OK")
        return
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    {
        "list": cmd_list,
        "unpack": cmd_unpack,
        "inject": cmd_inject,
        "setid": cmd_setid,
    }.get(cmd, lambda a: (print(__doc__), sys.exit(1)))(args)


if __name__ == "__main__":
    main()
