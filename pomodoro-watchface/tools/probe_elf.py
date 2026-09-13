#!/usr/bin/env python3
"""probe_elf.py — hand-built minimal ARM Cortex-M ELF probes.

Purpose
-------
`vela_ap.bin` and `vela_factory.bin` contain no ELF loader, no `binfmt`, no
`insmod` and no `dlopen` (see `docs/userland-loader-blueprint.md` §1). That is
a statement about the firmware *image*. It cannot rule out a loader that was
linked into a partition we do not have, or a third-party supervisor that shipped
its own loader as a file on the device.

The only way to settle it is to hand the device a binary and see whether
anything runs it. This tool writes those binaries — three variants, each chosen
so that the observable outcome is unambiguous and the payload cannot corrupt
anything:

  exec   ET_EXEC, one PT_LOAD at 0x2C600000, entry executes `udf #0`.
         If any loader execs it, the CPU takes an undefined-instruction fault:
         NuttX prints an "unexpected ISR"/fault report on the console/log and
         usually reboots the watch. A reboot within a second of the command,
         with a fault line in the log, is *positive* evidence.

  mark   ET_EXEC, same layout, entry writes the ASCII marker `VWPROBE` to a
         fixed RAM address and then spins. Use this one when you have a debugger
         or can dump memory: a marker in RAM proves execution without relying on
         the crash log. It hangs the watch deliberately, so only run it when a
         reboot is acceptable.

  rel    ET_REL, a single `.text` section holding the same `udf #0` stub with a
         defined `_probe_init` symbol — the shape a NuttX `insmod` of a kernel
         module would expect. Use with `insmod probe_rel.o`.

All three are ~1 KB and hand-assembled here (no toolchain required, which is why
this can run in this workspace at all).

Usage
-----
  python3 tools/probe_elf.py                 # writes all three to dist/probes/
  python3 tools/probe_elf.py --out /tmp/x    # different directory
  python3 tools/probe_elf.py --base 0x2C600000

Tests to run on the device (only meaningful with a shell):

  exec <path>/probe_exec.elf        # or: /bin/probe_exec.elf
  insmod <path>/probe_rel.o
  nsh -c "exec <path>/probe_exec.elf"

A plain "command not found", "Not a valid executable" or "-ENOEXEC" means no
loader for that format — which is exactly the answer the blueprint predicts.
"""

import argparse
import os
import struct

EM_ARM = 40
ET_EXEC = 2
ET_REL = 1

# ---- thumb-2 instruction encodings we need --------------------------------
UDF_0 = 0xDE00          # `udf #0` (16-bit, permanently undefined)
BKPT_0 = 0xBE00         # `bkpt #0`


def thumb_udf_stub():
    """`udf #0; b .` — 6 bytes of definitive fault-then-hang."""
    return struct.pack("<HH", UDF_0, 0xE7FE)


MARKER = int.from_bytes(b"VWPB", "little")


def thumb_marker_stub(base: int):
    """Write a marker word **into its own image**, then spin.

    Layout (all inside the single PT_LOAD, so nothing outside the probe's own
    mapping is ever touched — no guessed RAM address involved):

        +0  ldr r0, [pc, #4]   ; r0 = base+16   (literal at base+8)
        +2  ldr r1, [pc, #4]   ; r1 = 'VWPB'    (literal at base+12)
        +4  str r1, [r0]       ; marker lands at base+16
        +6  b .
        +8  .word base+16
        +12 .word 'VWPB'
        +16 .word 0            ; scratch word the store above targets

    The 16-bit `ldr Rt,[pc,#imm8*4]` form is used instead of movw/movt because
    it needs no wide encoding — the part that is easy to get subtly wrong when
    hand-assembling.
    """
    code = struct.pack("<HHHH", 0x4801, 0x4902, 0x6001, 0xE7FE)
    return code + struct.pack("<III", base + 16, MARKER, 0)


# ---- ELF writers -----------------------------------------------------------

def _ehdr(etype, entry, phoff, shoff, phnum, shnum, shstrndx):
    return struct.pack(
        "<4sBBBBB7xHHIIIIIHHHHHH",
        b"\x7fELF",
        1,          # EI_CLASS = 32-bit
        1,          # EI_DATA = little endian
        1,          # EI_VERSION
        0,          # EI_OSABI = System V
        0,          # EI_ABIVERSION
        etype,
        EM_ARM,
        1,          # e_version
        entry,
        phoff,
        shoff,
        0x05000000,  # e_flags: EABI v5
        52,          # e_ehsize
        32,          # e_phentsize
        phnum,
        40,          # e_shentsize
        shnum,
        shstrndx,
    )


def _phdr(offset, vaddr, filesz, memsz, flags):
    return struct.pack("<IIIIIIII", 1, offset, vaddr, vaddr, filesz, memsz, flags, 0x1000)


def _shdr(name, stype, flags, addr, offset, size, link, info, align):
    return struct.pack("<IIIIIIIIII", name, stype, flags, addr, offset, size, link, info, align, 0)


def build_exec(code, base, entry_off=0):
    """ET_EXEC with a single RWX PT_LOAD covering `code`."""
    ehsize = 52
    phentsize = 32
    phoff = ehsize
    code_off = phoff + phentsize
    align = 4
    code_off = (code_off + align - 1) & ~(align - 1)

    shstr = b"\x00.text\x00.shstrtab\x00"
    shstr_off = code_off + len(code)
    shoff = (shstr_off + len(shstr) + 3) & ~3

    out = bytearray()
    out += _ehdr(ET_EXEC, base + entry_off, phoff, shoff, 1, 3, 2)
    out += _phdr(code_off, base, len(code), len(code), 7)  # R|W|X
    out += b"\x00" * (code_off - len(out))
    out += code
    out += shstr
    out += b"\x00" * (shoff - len(out))
    out += _shdr(0, 0, 0, 0, 0, 0, 0, 0, 0)                    # NULL
    out += _shdr(1, 1, 0x6, base, code_off, len(code), 0, 0, 4)  # .text PROGBITS ALLOC|EXEC
    out += _shdr(7, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1)     # .shstrtab STRTAB
    return bytes(out)


def build_rel(code, base=0):
    """ET_REL: .text with a defined global symbol `_probe_init` (insmod shape)."""
    # Section order: 0=NULL 1=.text 2=.shstrtab 3=.symtab 4=.strtab
    # name offsets inside .shstrtab: .text=1, .shstrtab=7, .symtab=17
    shstr = b"\x00.text\x00.shstrtab\x00.symtab\x00.strtab\x00"
    strtab = b"\x00_probe_init\x00"

    code_off = 52  # right after ehdr; section headers go last
    shstr_off = code_off + len(code)
    strtab_off = shstr_off + len(shstr)
    symtab_off = (strtab_off + len(strtab) + 3) & ~3
    # NULL symbol + one STB_GLOBAL/STT_FUNC symbol at value 0 of section 1
    symtab = b"\x00" * 16 + struct.pack("<IIIBBH", 1, 0, 0, 0x12, 0, 1)
    shoff = (symtab_off + len(symtab) + 3) & ~3

    out = bytearray()
    out += _ehdr(ET_REL, 0, 0, shoff, 0, 5, 2)  # shstrndx = .shstrtab = 2
    out += code
    out += shstr
    out += strtab
    out += b"\x00" * (symtab_off - len(out))
    out += symtab
    out += b"\x00" * (shoff - len(out))
    out += _shdr(0, 0, 0, 0, 0, 0, 0, 0, 0)                       # NULL
    out += _shdr(1, 1, 0x6, 0, code_off, len(code), 0, 0, 4)       # .text
    out += _shdr(7, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1)       # .shstrtab
    out += _shdr(17, 2, 0, 0, symtab_off, len(symtab), 4, 1, 4)    # .symtab -> .strtab
    out += _shdr(25, 3, 0, 0, strtab_off, len(strtab), 0, 0, 1)    # .strtab
    return bytes(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist", "probes"))
    ap.add_argument("--base", type=lambda s: int(s, 0), default=0x2C600000,
                    help="load vaddr for the ET_EXEC probes (default 0x2C600000)")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)

    files = {
        "probe_exec.elf": build_exec(thumb_udf_stub(), args.base),
        "probe_mark.elf": build_exec(thumb_marker_stub(args.base), args.base),
        "probe_rel.o": build_rel(thumb_udf_stub()),
    }
    for name, blob in files.items():
        path = os.path.join(args.out, name)
        with open(path, "wb") as fh:
            fh.write(blob)
        print("wrote %-16s %5d bytes" % (path, len(blob)))

    print()
    print("ET_EXEC probes load at 0x%08X; `probe_mark.elf` writes 0x%08X into its own"
          % (args.base, MARKER))
    print("image at 0x%08X +16, so a memory dump proves execution without touching" % args.base)
    print("anything outside the mapping. Only meaningful with a shell; see")
    print("docs/device-recon.md step 3.")


if __name__ == "__main__":
    main()
