# Pomodoro 番茄钟 for Mi Band 9 Pro — Vela RE & two delivery paths

Reverse-engineering of the Xiaomi Smart Band 9 Pro firmware `3.1.175.bin`
(Vela OS / NuttX / Lua 5.4 + lugl) with two results:

1. **`dist/Pomodoro.face`** — a working Lua 番茄钟 watchface built by
   injecting our script into a stock face container (works today, no firmware
   modification). It is a *watchface*, i.e. it lives in the watchface list,
   not the app list.
2. **`native-app/`** — the corrected implementation of what was actually
   requested: a 番茄钟 that shows up **in the system app list like a native
   app**, with real system notifications. On this platform that is done the way
   [Canopus-Module-BluetoothAudio](https://github.com/Searchstars/Canopus-Module-BluetoothAudio)
   does it (install watchface → manager → native module →
   `app_install` + `launcher_add`). The module source, installer watchface,
   build scripts **and a target pack generated from the firmware itself** are in
   [`native-app/`](native-app/README.md).
3. **`tools/target_pack.py` + `targets/`** — the firmware analysis that a native
   module needs: 27,474 functions mapped, 25 of 26 required firmware entry
   points resolved with exact addresses, plus the field-offset evidence for
   rebuilding the descriptor structs.

**Read [`docs/userland-loader-blueprint.md`](docs/userland-loader-blueprint.md)
before expecting the app list to change:** this firmware contains **no ELF,
`binfmt`, `insmod`, `dlopen` or `NXFLAT` loader**, so a native module cannot be
brought in by itself — the blueprint quantifies that boundary and lists the four
candidate ways around it. The watchface in `dist/` needs no loader and works
today.

Built by reverse-engineering the stock firmware `3.1.175.bin` and its bundled
Lua watchfaces. See [docs/RE-notes.md](docs/RE-notes.md) for the full analysis
(watchface container, Lua API surface, quickapp/rpk pipeline, launcher
registration).

## What the Lua watchface does

- 25 min focus / 5 min break / 15 min long break every 4 rounds
- Live countdown, progress bar, mode & round indicators
- Tap left half to RESET, right half to START/PAUSE
- **Notifications via vibration**: long buzz when focus ends, short buzz when
  break ends (uses the firmware `vibrator` service available to Lua scripts)
- Pauses automatically when the watchface deactivates (`pageOnPause`)

## Files

```
app/lua/main.lua         readable pomodoro source
app/lua/main_packed.lua  minified build artifact (fits the 2468-byte slot)
app/lua/probe.lua        device probe script (what a watchface can actually do here)
tools/facepack.py        .face container packer/unpacker (list/unpack/inject/setid/selftest)
tools/device_probe.py    builds dist/Probe.face from that probe script
tools/probe_elf.py       hand-built minimal ARM ELF probes (loader yes/no test)
tools/device-recon.sh    read-only fact collection to run on the watch
tools/check_build.py     build verifier (container diff + Lua parse)
tools/symbol_hunt.py     firmware symbol resolver (base calibration, string xref, call graph)
tools/target_pack.py     per-firmware target pack generator (entry points + struct offsets)
docs/RE-notes.md         full reverse-engineering notes
docs/userland-loader-blueprint.md  the loader boundary + the four channels around it
docs/device-recon.md     what to run on the device, in order, and what it means
docs/canopus-reimpl-feasibility.md  feasibility report (中文)
docs/stock_theme1.lua    stock Lua watchface recovered from firmware (reference)
targets/vela_ap.symbols.{md,json}   raw sweep evidence over the whole AP image
dist/Pomodoro.face       ready-to-install watchface binary (id 491552737)
dist/Probe.face          ready-to-install device probe (id 491552739)
dist/probes/*.elf,*.o    loader yes/no probes for use with a shell
native-app/              native-module path (app-list app) — see its README
```

## Next step: run the device probe

[`docs/device-recon.md`](docs/device-recon.md) is the shortest path from a
physical watch to a finished native app. Install `dist/Probe.face` like any
watchface and read the screen: it reports whether watchface Lua gets `io`/`os`,
which paths are readable, which directories are writable, and — most
importantly — whether a **Canopus/AstroBox supervisor** is already on the
device. If it is, its files are the missing piece of the whole framework, and
the recon doc says exactly which ones to copy out.

## Install

`dist/Pomodoro.face` is a standard Xiaomi `.face` package (magic
`5A A5 34 12`). Install it like any marketplace watchface — via the Mi Fitness
watchface installer or by pushing it as
`/data/app/watchface/market/491552737/resource.bin` on a rooted/dev band and
adding an entry to `/data/app/watchface/watchface_list.json`.

## Build it yourself

```
# 1. get a Lua watchface to use as container host (face_14 from the OTA)
# 2. inject the pomodoro script into all three theme slots
python3 tools/facepack.py inject <stock.face> app/lua/main_packed.lua \
        _lua/theme1/theme1.lua /tmp/s1.bin
python3 tools/facepack.py inject /tmp/s1.bin app/lua/main_packed.lua \
        _lua/theme2/theme2.lua /tmp/s2.bin
python3 tools/facepack.py inject /tmp/s2.bin app/lua/main_packed.lua \
        _lua/theme3/theme3.lua Pomodoro.face

# 3. set your own watchface ID (must match watchface_list.json)
python3 tools/facepack.py setid Pomodoro.face 491552737

# 4. verify
python3 tools/check_build.py
```

## How the injection works (short version)

Stock Lua watchfaces store their script as a named segment
`_lua/themeN/themeN.lua` inside the `.face` container. The firmware's
`watchface_manager` loads and runs it with Lua 5.4 + lugl. We overwrite those
payloads in place — same byte size, so every header table stays valid — and
patch the 12-byte watchface ID at offset 0x28 (NUL-padded; header bytes 0x04
and 0x05 are preserved exactly — they are not version/length fields).

## API surface available to watchface scripts

`lvgl` (Object/Label/Image/Arc/Timer/Font…), `dataman.subscribe(...)`
(clock/health/weather/system data), `topic.subscribe(...)` (sensors +
`miwear.topic`), `vibrator.start{...}` (haptic notifications). The Lua 5.4
standard libraries (`io`, `os`, `package`) **are linked into the image** —
whether the watchface state opens them is the one thing RE-notes §6 leaves
unverified (the installer watchface probes it on device).
Lifecycle: `pageOnResume()` / `pageOnPause()`. Script dir: `SCRIPT_PATH`.

**System notifications are not available to watchface scripts** (no
notification binding is registered in that Lua state), but they *are* available
to a native module: `lvx_notification_init_message`, `lvx_notification_insert_message`,
`lvx_notification_remove_message` and five more are all present in the firmware
with resolved addresses — see the target pack.
