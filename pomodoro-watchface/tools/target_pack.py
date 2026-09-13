#!/usr/bin/env python3
"""target_pack.py — generate a per-firmware target pack from a Vela AP image.

A "target pack" (the thing the closed-source Canopus framework keeps private
for every supported device/firmware) is, mechanically, two things:

  1. a set of **named firmware entry points** the module needs to call
     (app registration, launcher publication, page framework, haptics), and
  2. the **struct layouts / constants** those entry points use.

This tool recovers (1) directly from the image with `symbol_hunt`'s string ->
function resolution, and records (2) as field-offset evidence read out of the
disassembly of those functions.  It also records *negative* evidence: the
loader capabilities the image does NOT have, which is what decides whether a
native module can ever be loaded on this firmware at all.

Nothing here is guessed silently: every entry carries how it was resolved and
which capabilities depend on it, and unresolved entries stay in the pack.

Usage
-----
  python3 tools/target_pack.py /tmp/fw/vela_ap.bin \\
      --target xiaomi-band-9-pro-3.1.175 \\
      --json native-app/targets/xiaomi-band-9-pro-3.1.175.json \\
      --md   native-app/targets/xiaomi-band-9-pro-3.1.175.md

Pure stdlib (capstone optional, only for the disassembly snippets).
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from symbol_hunt import (  # noqa: E402
    Analysis,
    calibrate_base,
    find_strings,
    load_image,
    word_array,
)

# ---------------------------------------------------------------------------
# Curated symbol map: what the pomodoro native app needs from the firmware
# ---------------------------------------------------------------------------
# role        : what the module does with it
# capability  : canopus capability name it satisfies (see canopus.toml)
# strings     : candidate literal strings; the first that resolves wins
# required    : False => nice-to-have (module still runs without it)

SYMBOLS = [
    # --- identity / app registration -------------------------------------
    dict(
        role="app_install",
        capability="native-app.register",
        required=True,
        strings=["app_install"],
        note="registers an app descriptor + page descriptor array; "
        "refs 'free_app' and '[%s] %s: [%s] installation failed'",
    ),
    dict(
        role="app_unregister",
        capability="native-app.register",
        required=False,
        strings=["packagemanager_app_unregister"],
        note="recoverable app removal / app list bookkeeping",
    ),
    dict(
        role="app_lookup",
        capability="native-app.register",
        required=True,
        # Exact-name only: a substring search would match the unrelated
        # `quickapp_get_appinfo`, which is not the app-registry query.
        strings=["app_lookup"],
        note="is-the-app-installed query used as the publication gate; this "
        "firmware exposes the app registry from app_install/app_unregister but "
        "not under this name - see the UNRESOLVED entry",
    ),
    # --- page framework ---------------------------------------------------
    dict(
        role="page_register",
        capability="ui.dispatch",
        required=True,
        strings=["activitymanager_page_register"],
        note="registers a page descriptor with the page manager; "
        "refs 'register page is NULL'",
    ),
    dict(
        role="pagemanager_prelaunch_check",
        capability="ui.dispatch",
        required=False,
        strings=["lvx_pagemanager_prelaunch_check"],
        note="app launch pre-check ('qapp')",
    ),
    dict(
        role="on_resume_wrapped",
        capability="ui.dispatch",
        required=False,
        strings=["on_resume_wrapped"],
        note="page-manager lifecycle wrapper (page on_resume trampoline)",
    ),
    dict(
        role="on_destroy_wrapped",
        capability="ui.dispatch",
        required=False,
        strings=["on_destroy_wrapped"],
        note="page-manager lifecycle wrapper (async ui destroy stack)",
    ),
    # --- launcher ---------------------------------------------------------
    dict(
        role="launcher_add",
        capability="launcher.entry",
        required=True,
        strings=["app_launcher_add"],
        note="adds the published app id to the launcher list",
    ),
    dict(
        role="launcher_page_insert_icon",
        capability="launcher.entry",
        required=False,
        strings=["launcher_page_insert_icon"],
        note="inserts a launcher grid icon",
    ),
    dict(
        role="launcher_page_remove_icon",
        capability="launcher.entry",
        required=False,
        strings=["launcher_page_remove_icon"],
        note="removes a launcher grid icon",
    ),
    dict(
        role="launcher_page_get_icon_appid",
        capability="launcher.entry",
        required=False,
        strings=["launcher_page_get_icon_appid"],
        note="app-id <-> icon handle lookup ('icon is no in launcher')",
    ),
    dict(
        role="create_app_icon",
        capability="launcher.entry",
        required=False,
        strings=["create_app_icon"],
        note="builds the launcher icon widget (also handles the calendar icon)",
    ),
    dict(
        role="sort_apps",
        capability="launcher.entry",
        required=False,
        strings=["sort_apps"],
        note="re-sorts the app list after insertion",
    ),
    # --- haptics (the notification channel) --------------------------------
    dict(
        role="miwear_vibrator_run",
        capability="vibrator",
        required=True,
        strings=["miwear_vibrator_run"],
        note="system-wide vibrator entry (57 callers: reminders, alarms, UI)",
    ),
    dict(
        role="miwear_vibrator_cancel",
        capability="vibrator",
        required=True,
        strings=["miwear_vibrator_cancel"],
        note="cancels a running vibration pattern",
    ),
    dict(
        role="miwear_vibrator_set_mode",
        capability="vibrator",
        required=False,
        strings=["miwear_vibrator_set_mode"],
        note="reads 'persist.vibration.mode'",
    ),
    dict(
        role="lua_vibrator_start",
        capability="vibrator",
        required=False,
        strings=["lua_vibrator_start"],
        note="Lua 'vibrator.start' binding - reference implementation of the "
        "same call our native code makes; takes a table with 'is_repeat'",
    ),
    # --- notification tray (the real system-notification channel) ----------
    # Confirmed present on this firmware: the full lvx_notification_message_*
    # API. This is what makes "system notification" possible for a native app,
    # and it is unreachable from watchface Lua.
    dict(
        role="notification_init_message",
        capability="notifications.tray",
        required=True,
        strings=["lvx_notification_init_message"],
        note="initialises a notification message object owned by the module",
    ),
    dict(
        role="notification_insert_message",
        capability="notifications.tray",
        required=True,
        strings=["lvx_notification_insert_message"],
        note="pushes one message into the system notification tray; same "
        "function also references notification_manager and "
        "lvx_notification_start_reminder",
    ),
    dict(
        role="notification_insert_many_message",
        capability="notifications.tray",
        required=False,
        strings=["lvx_notification_insert_many_message"],
        note="batch insertion",
    ),
    dict(
        role="notification_remove_message",
        capability="notifications.tray",
        required=False,
        strings=["lvx_notification_remove_message"],
        note="removes a message (e.g. when the pomodoro page is opened)",
    ),
    dict(
        role="notification_remove_all_appid_message",
        capability="notifications.tray",
        required=False,
        strings=["lvx_notification_remove_all_appid_message"],
        note="clears every message this app pushed",
    ),
    dict(
        role="notification_remove_all_normal_message",
        capability="notifications.tray",
        required=False,
        strings=["lvx_notification_remove_all_normal_message"],
        note="clears the tray (system-wide)",
    ),
    dict(
        role="notification_update_message_icon",
        capability="notifications.tray",
        required=False,
        strings=["lvx_notification_update_message_icon"],
        note="swaps the icon of an existing message in place",
    ),
    dict(
        role="notification_start_reminder",
        capability="notifications.tray",
        required=False,
        strings=["lvx_notification_start_reminder"],
        note="reminder-style notification (the firmware's own alarm/reminder "
        "path); reference for message field semantics",
    ),
    # --- launcher app-table plumbing (page list) ---------------------------
    dict(
        role="launcher_page_main_update_layout",
        capability="ui.dispatch",
        required=False,
        strings=["launcher_page_main_update_layout"],
        note="launcher main page layout refresh after registration",
    ),
]

# Capabilities whose absence decides whether native code can run at all.
LOADER_INDICATORS = [
    ("ELF magic constant (0x7f 'ELF')", b"\x7fELF"),
    ("binfmt loader layer", b"binfmt"),
    ("NXFLAT loader", b"NXFLAT"),
    ("kernel module loader (insmod)", b"insmod"),
    ("module unload (rmmod)", b"rmmod"),
    ("dynamic linker (dlopen)", b"dlopen"),
    ("dynamic linker (dlsym)", b"dlsym"),
    ("binfmt loadmodule", b"loadmodule"),
    ("binfmt execmodule", b"execmodule"),
    ("CONFIG_BINFMT knob string", b"CONFIG_BINFMT"),
    ("CONFIG_MODULE knob string", b"CONFIG_MODULE"),
]

# Positive evidence that the image *does* contain the pieces a future loader
# would have to build on (task/group/spawn model, file-backed mmap, NSH).
PLATFORM_INDICATORS = [
    ("NuttX task/group model", b"group/group_create.c"),
    ("posix_spawn path", b"task/task_posixspawn.c"),
    ("NuttX NSH shell (builtin)", b"nsh_main"),
    ("file-backed rammap", b"mmap/fs_rammap.c"),
    ("procfs", b"procfs/fs_procfs.c"),
    ("Lua 5.4 host", b"luaopen_lvgl"),
    ("AIOTJS quickapp engine", b"AIOTJS"),
    ("AIOTJS native proxy", b"jse_nativeproxy.cpp"),
]


def field_offsets(analysis, func_off, max_insns=120):
    """Collect `ldr/str rX, [rY, #imm]` offsets seen in a function.

    These are the raw hints a target pack needs when reconstructing the
    struct a firmware entry point expects (which field lives at which offset).
    """
    snip = analysis.disasm(func_off, max_insns)
    if not snip:
        return None, None
    offsets = []
    for line in snip:
        m = re.search(r"\b(ldr|str|ldrb|strb|ldrh|strh|ldrd|strd)\s+\S+?,\s*\[(\w+)(?:,\s*#(0x[0-9a-f]+|\d+))?\]", line)
        if not m:
            continue
        reg = m.group(2)
        if reg == "pc":
            # pc-relative loads are literal-pool reads, not struct fields
            continue
        imm = m.group(3)
        offsets.append((m.group(1), reg, int(imm, 0) if imm else 0))
    return snip, offsets


def resolve(analysis, strings, candidates):
    """Resolve a candidate name to a firmware function.

    Exact string equality is tried first and is reported as
    `match_kind="exact"`.  Only if nothing matches exactly does it fall back to
    substring matching, which is reported as `match_kind="substring"` so a
    loose hit can never be mistaken for a confirmed symbol.
    """
    by_text = {}
    for off, text in strings.items():
        by_text.setdefault(text, off)

    def entry(str_off, text, kind, cand):
        func_off, count = analysis.funcs_referencing(str_off)[0]
        return {
            "string": text,
            "matched_candidate": cand,
            "match_kind": kind,
            "string_addr": "0x%08X" % (analysis.base + str_off),
            "func_off": func_off,
            "func_addr": "0x%08X" % (analysis.base + func_off),
            "size": analysis.func_size(func_off),
            "str_refs": count,
            "callers": len(analysis.func_callers.get(func_off, [])),
        }

    for cand in candidates:
        str_off = by_text.get(cand)
        if str_off is not None and analysis.funcs_referencing(str_off):
            return entry(str_off, cand, "exact", cand)
    for cand in candidates:
        for str_off, text in analysis.search_strings(cand):
            if analysis.funcs_referencing(str_off):
                return entry(str_off, text, "substring", cand)
    return None


def build_pack(image, target_id, disasm_n):
    data = load_image(image)
    words = word_array(data)
    strings = find_strings(data)
    base = calibrate_base(data, words, strings, verbose=False)
    analysis = Analysis(data, base)
    analysis.strings = strings
    analysis.scan_functions()
    analysis.scan_code()

    entries = []
    for spec in SYMBOLS:
        hit = resolve(analysis, strings, spec["strings"])
        entry = {
            "role": spec["role"],
            "capability": spec["capability"],
            "required": spec["required"],
            "note": spec["note"],
            "resolved": hit is not None,
        }
        if hit:
            entry.update(hit)
            snip, offs = field_offsets(analysis, hit["func_off"], disasm_n or 24)
            if snip:
                entry["disasm"] = snip
            if offs:
                # de-duplicate, keep first-seen order (reads usually come first)
                seen, uniq = set(), []
                for kind, reg, off in offs:
                    key = (kind, off)
                    if key in seen:
                        continue
                    seen.add(key)
                    uniq.append({"op": kind, "base_reg": reg, "offset": off})
                entry["struct_offsets"] = uniq
        else:
            entry["searched_strings"] = spec["strings"]
        entries.append(entry)

    loader = [
        {"indicator": label, "count": data.count(pat)}
        for label, pat in LOADER_INDICATORS
    ]
    platform = [
        {"indicator": label, "count": data.count(pat)}
        for label, pat in PLATFORM_INDICATORS
    ]

    return {
        "target_id": target_id,
        "image": os.path.basename(image),
        "image_size": len(data),
        "load_base": "0x%08X" % base,
        "functions": len(analysis.func_starts),
        "string_refs": analysis.literal_string_refs,
        "bl_decoded": analysis.bl_count,
        "bl_resolved": analysis.resolved_bl,
        "symbols": entries,
        "loader_indicators": loader,
        "platform_indicators": platform,
        "generator": "tools/target_pack.py",
    }


def render_md(pack):
    out = []
    out.append("# Target pack — `%s`" % pack["target_id"])
    out.append("")
    out.append(
        "Generated by `%s` from `%s` (%d bytes). Addresses are load addresses "
        "(`%s` + file offset), resolved purely by static analysis — nothing here "
        "was taken from the closed-source Canopus framework."
        % (pack["generator"], pack["image"], pack["image_size"], pack["load_base"])
    )
    out.append("")
    out.append(
        "- functions discovered (Thumb-2 `push {..., lr}` prologues): **%d**"
        % pack["functions"]
    )
    out.append("- string literal references attributed to functions: **%d**" % pack["string_refs"])
    out.append(
        "- BL sites decoded: **%d**, resolved to known functions: **%d**"
        % (pack["bl_decoded"], pack["bl_resolved"])
    )
    out.append("")

    out.append("## 1. Entry points")
    out.append("")
    out.append("| role | capability | req | firmware string | kind | addr | size | str refs | callers |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for e in pack["symbols"]:
        if e["resolved"]:
            out.append(
                "| `%s` | %s | %s | `%s` | %s | `%s` | %d | %d | %d |"
                % (
                    e["role"],
                    e["capability"],
                    "yes" if e["required"] else "no",
                    e["string"],
                    e["match_kind"],
                    e["func_addr"],
                    e["size"],
                    e["str_refs"],
                    e["callers"],
                )
            )
    out.append("")
    out.append(
        "`kind` is how the firmware string was matched: `exact` is a confirmed "
        "symbol name; `substring` is a looser hit that still needs review before "
        "being called."
    )
    out.append("")
    for e in pack["symbols"]:
        if not e["resolved"]:
            out.append(
                "- **UNRESOLVED** `%s` (capability `%s`, %s) — searched %s"
                % (
                    e["role"],
                    e["capability"],
                    "required" if e["required"] else "optional",
                    ", ".join("`%s`" % s for s in e["searched_strings"]),
                )
            )
    out.append("")

    out.append("## 2. Field-offset evidence")
    out.append("")
    out.append(
        "Offsets observed inside each resolved entry point's own disassembly "
        "(`ldr/str rX, [rY, #imm]`), i.e. the raw material for reconstructing "
        "the descriptor layouts the firmware expects. Offsets are ungrouped: the "
        "base register identifies which object they belong to (r0 = first "
        "argument, r1/r2 = next, sp-relative = locals)."
    )
    out.append("")
    for e in pack["symbols"]:
        if not e["resolved"] or not e.get("struct_offsets"):
            continue
        reads = [o for o in e["struct_offsets"] if o["op"].startswith("ldr")]
        writes = [o for o in e["struct_offsets"] if o["op"].startswith("str")]
        out.append("### `%s` @ `%s`" % (e["role"], e["func_addr"]))
        out.append("")
        out.append("- loads: %s" % _fmt_offsets(reads))
        out.append("- stores: %s" % _fmt_offsets(writes))
        out.append("")
    out.append("")

    out.append("## 3. Boundary evidence — what this firmware does *not* have")
    out.append("")
    out.append(
        "A target pack is not enough to run a module: something has to **load** "
        "it. These are the loader capabilities searched for in the image, with "
        "their occurrence counts."
    )
    out.append("")
    out.append("| indicator | occurrences |")
    out.append("|---|---|")
    for i in pack["loader_indicators"]:
        out.append("| %s | %d |" % (i["indicator"], i["count"]))
    out.append("")
    out.append("## 4. Platform evidence — what it *does* have")
    out.append("")
    out.append("| indicator | occurrences |")
    out.append("|---|---|")
    for i in pack["platform_indicators"]:
        out.append("| %s | %d |" % (i["indicator"], i["count"]))
    out.append("")
    out.append(
        "See [`../../docs/userland-loader-blueprint.md`](../../docs/userland-loader-blueprint.md) "
        "for what these two tables together imply, and "
        "[`../../docs/RE-notes.md`](../../docs/RE-notes.md) for the full analysis."
    )
    out.append("")
    return "\n".join(out)


def _fmt_offsets(offsets):
    """Render offsets grouped by what the base register most likely is."""
    if not offsets:
        return "_(none)_"

    def group(reg):
        if reg in ("r0", "r1", "r2", "r3"):
            return "args"
        if reg in ("sp",):
            return "locals"
        return "objects"

    buckets = {"args": [], "objects": [], "locals": []}
    for o in offsets:
        buckets[group(o["base_reg"])].append(o)
    labels = {
        "args": "r0-r3 (call arguments, i.e. the descriptor being installed)",
        "objects": "r4-r11 (callee-saved objects the function builds/reads)",
        "locals": "sp (stack locals, not part of any public struct)",
    }
    parts = []
    for key in ("args", "objects", "locals"):
        items = sorted(set("%s[%s+0x%X]" % (o["op"], o["base_reg"], o["offset"]) for o in buckets[key]))
        if not items:
            continue
        parts.append("%s: %s" % (labels[key], ", ".join("`%s`" % i for i in items)))
    return "; ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--target", required=True, help="target id, e.g. xiaomi-band-9-pro-3.1.175")
    ap.add_argument("--json", default=None)
    ap.add_argument("--md", default=None)
    ap.add_argument("--disasm", type=int, default=140, help="instructions per entry to scan for offsets")
    args = ap.parse_args(argv)

    pack = build_pack(args.image, args.target, args.disasm)
    resolved = sum(1 for e in pack["symbols"] if e["resolved"])
    print(
        "[target-pack] %s: %s -> %d/%d symbols resolved, base %s"
        % (
            args.target,
            os.path.basename(args.image),
            resolved,
            len(pack["symbols"]),
            pack["load_base"],
        ),
        file=sys.stderr,
    )
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as fh:
            json.dump(pack, fh, indent=1)
        print("[target-pack] wrote %s" % args.json, file=sys.stderr)
    if args.md:
        os.makedirs(os.path.dirname(os.path.abspath(args.md)), exist_ok=True)
        with open(args.md, "w") as fh:
            fh.write(render_md(pack))
        print("[target-pack] wrote %s" % args.md, file=sys.stderr)


if __name__ == "__main__":
    main()
