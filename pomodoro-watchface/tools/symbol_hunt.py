#!/usr/bin/env python3
"""symbol_hunt.py — static symbol resolver for Xiaomi Vela flat firmware images.

Pure stdlib. Built for the Mi Band 9 Pro AP image (vela_ap.bin, Cortex-M33 /
Thumb-2, NuttX) but works on any flat ARM Thumb-2 image of the same shape.

What it does
------------
1. Calibrates the image load base address by requiring that literal-pool words
   and PC-relative LDR targets consistently point at C string starts
   (file offset == memory offset for a flat image).
2. Scans Thumb-2 code for function prologues (push {..., lr}) to build a
   function-start table, then attributes every `ldr rX, [pc, #imm]` literal
   load that resolves to a string to its containing function.
3. Decodes every BL to build a call graph between discovered functions.
4. Sweeps keyword groups (launcher / pagemanager / quickapp / notifications /
   vibrator / miwear-lua / canopus / watchface-registry) and emits JSON +
   Markdown evidence: for each interesting string, which function references
   it, how big that function is, and who calls it.

Subcommands
-----------
  base       <img>                     print the calibrated image base
  xref       <img> <substr> [...]      interactive string cross-reference
  sweep      <img> [...] --json F --md F [--disasm N]
                                       full module sweep, writes JSON+MD
  unpack-ota <ota.bin> <outdir> [--only REGEX]
                                       recover members from a concatenated OTA
                                       zip (handles prepended payload bytes)
  calls      <img> <func_off_hex>      who calls the function at file offset

Example
-------
  python3 tools/symbol_hunt.py sweep /tmp/fw/vela_ap.bin \
      --json targets/vela_ap.symbols.json --md targets/vela_ap.symbols.md
"""

import argparse
import array
import bisect
import json
import os
import re
import struct
import sys
import zlib
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def load_image(path):
    with open(path, "rb") as fh:
        return fh.read()


def find_strings(data, min_len=4):
    """dict: file offset -> ascii string (NUL-terminated printable runs)."""
    out = {}
    start = None
    n = len(data)
    for i in range(n):
        b = data[i]
        if 32 <= b < 127:
            if start is None:
                start = i
            continue
        if start is not None:
            if b == 0 and (i - start) >= min_len:
                out[start] = data[start:i].decode("ascii")
            start = None
    return out


def word_array(data):
    words = array.array("I")
    usable = len(data) - (len(data) % 4)
    words.frombytes(data[:usable])
    return words


def halfword_array(data):
    hw = array.array("H")
    usable = len(data) - (len(data) % 2)
    hw.frombytes(data[:usable])
    return hw


# ---------------------------------------------------------------------------
# Image base calibration
# ---------------------------------------------------------------------------


def calibrate_base(data, words, strings, verbose=True):
    """Find the load address B such that many literal words equal B + str_off."""
    total = len(data)
    lo_bound, hi_bound = 0x08000000, 0x80000000

    # Long unique strings make good anchors: their literal pointers are rare
    # in random data, so candidate bases survive filtering only if real.
    anchors = sorted(
        (off for off, s in strings.items() if len(s) >= 24),
        key=lambda off: -len(strings[off]),
    )[:16]
    if not anchors:
        anchors = sorted(strings, key=lambda off: -len(strings[off]))[:8]

    wordset = set(words)

    # Candidate bases from the first anchor: B = W - off for pointer-looking W.
    a0 = anchors[0]
    cands = set()
    for w in words:
        b = w - a0
        if lo_bound <= b < hi_bound and (b & 0xFFF) == 0:
            cands.add(b)
    if verbose:
        print(f"[base] anchor candidates: {len(cands)}", file=sys.stderr)

    # Filter: every anchor must have at least one literal pointer at B+off.
    for off in anchors[1:]:
        nxt = set()
        for b in cands:
            if (off + b) in wordset:
                nxt.add(b)
        cands = nxt
        if len(cands) <= 1:
            break
    if verbose:
        print(f"[base] after anchor filter: {len(cands)}", file=sys.stderr)

    if not cands:
        return None

    # Score survivors: count words W where (W - B) lands exactly on a string.
    ptr_words = [w for w in words if lo_bound <= w < hi_bound]
    if len(ptr_words) > 300000:
        step = len(ptr_words) // 300000
        ptr_words = ptr_words[::step]

    best, best_score = None, -1
    for b in sorted(cands):
        score = 0
        hi = b + total
        for w in ptr_words:
            if b <= w < hi and (w - b) in strings:
                score += 1
        if score > best_score:
            best, best_score = b, score
    if verbose:
        print(f"[base] best base 0x{best:08X} score={best_score}", file=sys.stderr)
    return best


# ---------------------------------------------------------------------------
# Code scan: functions, literal->string attribution, call graph
# ---------------------------------------------------------------------------


class Analysis:
    def __init__(self, data, base):
        self.data = data
        self.base = base
        self.total = len(data)
        self.words = word_array(data)
        self.hws = halfword_array(data)
        self.strings = find_strings(data)
        self.func_starts = []
        self.func_strings = {}       # func_off -> {str_off: count}
        self.func_callers = defaultdict(list)   # func_off -> [(caller_off, site)]
        self.func_sites = defaultdict(list)     # func_off -> [bl site]
        self.literal_string_refs = 0
        self.bl_count = 0
        self.resolved_bl = 0

    # -- function start detection ------------------------------------------
    def scan_functions(self):
        hws = self.hws
        n = len(hws)
        starts = []
        i = 0
        while i < n - 1:
            hw = hws[i]
            # 16-bit push {..., lr}: 1011 0 1 0 1 <list>   -> 0xB5xx
            if (hw & 0xFF00) == 0xB500:
                starts.append(i * 2)
                i += 1
                continue
            # 32-bit push {..., lr}: 0xE92D 1x<list>
            if hw == 0xE92D and (hws[i + 1] & 0xC000) == 0x4000:
                starts.append(i * 2)
                i += 2
                continue
            i += 1
        starts.sort()
        self.func_starts = starts

    def func_of(self, file_off):
        """Nearest function start at or before file_off, else None."""
        idx = bisect.bisect_right(self.func_starts, file_off) - 1
        if idx < 0:
            return None
        return self.func_starts[idx]

    def func_size(self, func_off):
        idx = bisect.bisect_right(self.func_starts, func_off)
        if idx < len(self.func_starts):
            return self.func_starts[idx] - func_off
        return min(0x10000, self.total - func_off)

    # -- main scan: LDR literals + BL ---------------------------------------
    def scan_code(self):
        hws = self.hws
        base = self.base
        total = self.total
        strings = self.strings
        func_strings = self.func_strings
        func_callers = self.func_callers
        func_sites = self.func_sites
        starts = self.func_starts
        n = len(hws)
        i = 0
        while i < n:
            hw = hws[i]
            addr = i * 2
            # ---- LDR literal (16-bit): 01001 Rt imm8
            if (hw & 0xF800) == 0x4800:
                imm8 = hw & 0xFF
                # PC-relative offsets cancel the load base: literal lives at
                # Align4(PC) + imm8*4 in *file* coordinates.
                lit = ((addr + 4) & ~3) + imm8 * 4
                if 0 <= lit <= total - 4:
                    val = self.words[lit >> 2] - base
                    if val in strings:
                        f = self.func_of(addr)
                        if f is not None:
                            d = func_strings.setdefault(f, {})
                            d[val] = d.get(val, 0) + 1
                            self.literal_string_refs += 1
                i += 1
                continue
            # ---- LDR literal (32-bit): 0xF8DF (add) / 0xF85F (sub)
            if hw == 0xF8DF or hw == 0xF85F:
                imm12 = hws[i + 1] & 0xFFF
                lit = addr + 4 + (imm12 if hw == 0xF8DF else -imm12)
                if 0 <= lit <= total - 4:
                    val = self.words[lit >> 2] - base
                    if val in strings:
                        f = self.func_of(addr)
                        if f is not None:
                            d = func_strings.setdefault(f, {})
                            d[val] = d.get(val, 0) + 1
                            self.literal_string_refs += 1
                i += 2
                continue
            # ---- BL: hw1 0xF000-0xF7FF, hw2 11J1 1 J2 imm10lo
            if (hw & 0xF800) == 0xF000:
                hw2 = hws[i + 1]
                if (hw2 & 0x5000) == 0x5000:
                    self.bl_count += 1
                    s = (hw >> 10) & 1
                    imm10 = hw & 0x3FF
                    j1 = (hw2 >> 13) & 1
                    j2 = (hw2 >> 11) & 1
                    imm10lo = hw2 & 0x3FF
                    i1 = (~(j1 ^ s)) & 1
                    i2 = (~(j2 ^ s)) & 1
                    imm = (s << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm10lo << 1)
                    if s:
                        imm -= 1 << 25
                    target = addr + 4 + imm
                    if 0 <= target < total:
                        f = self.func_of(addr)
                        t = self.func_of(target) if target in starts else None
                        tgt = t if t is not None else (target if target in starts else None)
                        if tgt is not None:
                            self.resolved_bl += 1
                            func_callers[tgt].append((f, addr))
                            if f is not None:
                                func_sites[f].append(target)
                i += 2
                continue
            i += 1

    # -- reporting -----------------------------------------------------------
    def funcs_referencing(self, str_off):
        out = []
        for f, refs in self.func_strings.items():
            if str_off in refs:
                out.append((f, refs[str_off]))
        return sorted(out)

    def search_strings(self, needle, cap=12):
        hits = [(off, s) for off, s in self.strings.items() if needle in s]
        hits.sort(key=lambda t: (len(t[1]), t[0]))
        return hits[:cap]

    def disasm(self, func_off, max_insns=24):
        try:
            import capstone
        except ImportError:
            return None
        md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
        md.detail = False
        chunk = self.data[func_off:func_off + min(self.func_size(func_off), max_insns * 4 + 16)]
        out = []
        for ins in md.disasm(chunk, self.base + func_off):
            out.append(f"  {ins.address:08X}: {ins.mnemonic} {ins.op_str}")
            if len(out) >= max_insns:
                break
        return out


# ---------------------------------------------------------------------------
# Sweep groups
# ---------------------------------------------------------------------------

GROUPS = {
    "launcher": [
        "app_launcher_add", "launcher_add", "launcher_remove", "launcher_page",
        "sort_apps", "create_app_icon", "app_install", "app_lookup",
        "app_remove", "prelaunch", "app_manager", "appmanager", "app list",
    ],
    "pagemanager": [
        "pagemanager", "page_manager", "register page", "on_create",
        "on_resume", "on_pause", "on_destroy", "not stopped",
        "page framework", "page_framework",
    ],
    "quickapp": [
        "quickapp", "installRpk", "rpk_info", "manifest.json",
        "/data/quickapp", "/data/app/quickapp", "signature_block",
        "app_verify", "tomatotimer",
    ],
    "notifications": [
        "lvx_notification", "notification_insert", "/data/app/notifications",
        "notifications",
    ],
    "vibrator": [
        "vibrator", "miwear_vibrator", "vib_", "haptic",
    ],
    "miwear-lua": [
        "luaopen_", "lugl", "SCRIPT_PATH", "dataman", "miwear.topic",
        "miwear_apps", "watchface_manager",
    ],
    "canopus-kernel-module": [
        "canopus", "insmod", "rmmod", "nxmod", "/dev/canopus", "CMI1",
        "sys_call_table",
    ],
    "watchface-registry": [
        "watchface_list.json", "resource.bin", "/data/app/watchface",
        "/system/watchface", "market/",
    ],
    "aiotjs-quickapp-engine": [
        "binary_parse", "AIOTJS", "ferry::", "PackageManager", "app_block",
    ],
}


def run_sweep(images, json_path, md_path, disasm_n):
    analyses = []
    for path in images:
        data = load_image(path)
        words = word_array(data)
        strings = find_strings(data)
        base = calibrate_base(data, words, strings)
        an = Analysis(data, base)
        an.strings = strings
        an.scan_functions()
        an.scan_code()
        analyses.append((path, an))
        name = os.path.basename(path)
        print(
            f"[sweep] {name}: base=0x{base:08X} funcs={len(an.func_starts)} "
            f"str_refs={an.literal_string_refs} bl={an.bl_count} "
            f"bl_resolved={an.resolved_bl}",
            file=sys.stderr,
        )

    md = ["# symbol_hunt sweep output\n"]
    json_out = {"images": [], "groups": {}}
    for path, an in analyses:
        json_out["images"].append({
            "path": path,
            "base": f"0x{an.base:08X}",
            "functions": len(an.func_starts),
            "string_refs": an.literal_string_refs,
            "bl_decoded": an.bl_count,
            "bl_resolved": an.resolved_bl,
        })
    md.append(
        "Generated by `tools/symbol_hunt.py sweep` — static analysis only, "
        "addresses are load addresses (file offset + calibrated base).\n"
    )
    for path, an in analyses:
        md.append(f"\n## Image `{os.path.basename(path)}`\n")
        md.append(f"- load base: `0x{an.base:08X}`" if an.base else "- load base: unresolved")
        md.append(f"- functions found (push lr prologues): {len(an.func_starts)}")
        md.append(f"- string literal references resolved: {an.literal_string_refs}")
        md.append(f"- BL sites decoded: {an.bl_count} (resolved to known functions: {an.resolved_bl})\n")

    for group, keywords in GROUPS.items():
        json_out["groups"][group] = {}
        md.append(f"\n## group: {group}\n")
        for kw in keywords:
            entries = []
            for path, an in analyses:
                for str_off, text in an.search_strings(kw):
                    refs = an.funcs_referencing(str_off)
                    if not refs:
                        continue
                    for func_off, count in refs[:4]:
                        callers = an.func_callers.get(func_off, [])
                        entry = {
                            "image": os.path.basename(path),
                            "string": text,
                            "string_addr": f"0x{an.base + str_off:08X}",
                            "func_addr": f"0x{an.base + func_off:08X}",
                            "func_off": f"0x{func_off:X}",
                            "func_size": an.func_size(func_off),
                            "refs": count,
                            "callers": len(callers),
                        }
                        if disasm_n:
                            snip = an.disasm(func_off, disasm_n)
                            if snip:
                                entry["disasm"] = snip
                        entries.append(entry)
            if entries:
                json_out["groups"][group][kw] = entries
                md.append(f"\n### `{kw}`\n")
                md.append("| image | string | str addr | func addr | func off | size | refs | callers |")
                md.append("|---|---|---|---|---|---|---|---|")
                for e in entries[:16]:
                    md.append(
                        f"| {e['image']} | `{e['string'][:60]}` | {e['string_addr']} "
                        f"| {e['func_addr']} | {e['func_off']} | {e['func_size']} "
                        f"| {e['refs']} | {e['callers']} |"
                    )
                if disasm_n and entries and entries[0].get("disasm"):
                    e = entries[0]
                    md.append(f"\n<details><summary>disasm of {e['func_addr']} (first hit)</summary>\n")
                    md.append("```")
                    md.extend(e["disasm"])
                    md.append("```\n</details>\n")

    if json_path:
        os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(json_out, fh, ensure_ascii=False, indent=1)
        print(f"[sweep] wrote {json_path}", file=sys.stderr)
    if md_path:
        os.makedirs(os.path.dirname(os.path.abspath(md_path)), exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")
        print(f"[sweep] wrote {md_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# OTA unpack (handles prepended bytes / shifted central directory)
# ---------------------------------------------------------------------------


def unpack_ota(ota_path, outdir, only=None):
    data = load_image(ota_path)
    os.makedirs(outdir, exist_ok=True)
    rx = re.compile(only) if only else None
    i = 0
    made = 0
    while True:
        i = data.find(b"PK\x03\x04", i)
        if i < 0:
            break
        try:
            (_ver, flags, method, _mt, _md, _crc, csize, usize, nlen, elen) = struct.unpack_from(
                "<HHHHHIIIHH", data, i + 4
            )
        except struct.error:
            break
        name_bytes = data[i + 30:i + 30 + nlen]
        i += 30 + nlen + elen
        try:
            name = name_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if name.endswith("/"):
            continue
        if rx and not rx.search(name):
            continue
        if flags & 0x8 or csize == 0:
            # streaming entry: feed deflate stream until it ends
            if method != 8:
                i += csize
                continue
            d = zlib.decompressobj(-15)
            out = bytearray()
            j = i
            try:
                while j < len(data) and not d.eof:
                    out += d.decompress(data[j:j + 65536])
                    j += 65536
            except zlib.error:
                pass
            content = bytes(out)
            i = j
        else:
            comp = data[i:i + csize]
            i += csize
            if method == 0:
                content = comp
            elif method == 8:
                try:
                    content = zlib.decompress(comp, -15)
                except zlib.error:
                    continue
            else:
                continue
        dest = os.path.join(outdir, os.path.basename(name))
        with open(dest, "wb") as fh:
            fh.write(content)
        made += 1
        print(f"[unpack-ota] {name} -> {dest} ({len(content)} bytes)")
    print(f"[unpack-ota] extracted {made} files")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_xref(args):
    data = load_image(args.image)
    words = word_array(data)
    base = calibrate_base(data, words, find_strings(data, min_len=4))
    an = Analysis(data, base)
    an.strings = find_strings(data)
    an.scan_functions()
    an.scan_code()
    print(f"image {args.image} base=0x{base:08X} funcs={len(an.func_starts)}")
    for needle in args.needles:
        print(f"\n== {needle!r} ==")
        for str_off, text in an.search_strings(needle):
            print(f"  string @0x{an.base + str_off:08X}: {text[:90]!r}")
            for func_off, count in an.funcs_referencing(str_off)[:6]:
                callers = an.func_callers.get(func_off, [])
                print(
                    f"    func 0x{an.base + func_off:08X} (off 0x{func_off:X}, "
                    f"size {an.func_size(func_off)}, refs {count}, callers {len(callers)})"
                )


def cmd_calls(args):
    data = load_image(args.image)
    words = word_array(data)
    base = calibrate_base(data, words, find_strings(data, min_len=4))
    an = Analysis(data, base)
    an.strings = find_strings(data)
    an.scan_functions()
    an.scan_code()
    target = int(args.func_off, 0)
    callers = an.func_callers.get(target, [])
    print(f"callers of 0x{base + target:08X}: {len(callers)}")
    for caller, site in callers[:40]:
        cf = caller if caller is not None else 0
        strs = an.func_strings.get(cf, {})
        hint = next(iter(strs), None)
        hint_text = an.strings[hint][:48] if hint is not None else ""
        print(f"  caller func 0x{base + cf:08X} site 0x{base + site:08X} str={hint_text!r}")


def cmd_base(args):
    data = load_image(args.image)
    words = word_array(data)
    base = calibrate_base(data, words, find_strings(data, min_len=4))
    print(f"0x{base:08X}" if base else "unresolved")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("base")
    p.add_argument("image")
    p.set_defaults(fn=cmd_base)

    p = sub.add_parser("xref")
    p.add_argument("image")
    p.add_argument("needles", nargs="+")
    p.set_defaults(fn=cmd_xref)

    p = sub.add_parser("sweep")
    p.add_argument("images", nargs="+")
    p.add_argument("--json", default=None)
    p.add_argument("--md", default=None)
    p.add_argument("--disasm", type=int, default=0)
    p.set_defaults(fn=lambda a: run_sweep(a.images, a.json, a.md, a.disasm))

    p = sub.add_parser("unpack-ota")
    p.add_argument("ota")
    p.add_argument("outdir")
    p.add_argument("--only", default=None)
    p.set_defaults(fn=lambda a: unpack_ota(a.ota, a.outdir, a.only))

    p = sub.add_parser("calls")
    p.add_argument("image")
    p.add_argument("func_off")
    p.set_defaults(fn=cmd_calls)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
