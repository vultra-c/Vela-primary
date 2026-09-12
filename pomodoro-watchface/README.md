# Pomodoro Watchface (番茄钟) for Mi Band 9 Pro — Vela Lua Injection

A native **Pomodoro focus timer** injected into the Xiaomi Smart Band 9 Pro's
system as a Lua watchface — no firmware modification required.

Built by reverse-engineering the stock firmware `3.1.175.bin` (Vela OS /
Lua 5.4 / lugl-LVGL) and its bundled Lua watchfaces. See
[docs/RE-notes.md](docs/RE-notes.md) for the full analysis.

## What it does

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
tools/facepack.py        .face container packer/unpacker (list/unpack/inject/setid/selftest)
tools/check_build.py     build verifier (container diff + Lua parse)
docs/RE-notes.md         full reverse-engineering notes
docs/stock_theme1.lua    stock Lua watchface recovered from firmware (reference)
dist/Pomodoro.face       ready-to-install watchface binary (id 491552737)
```

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
(clock/health/weather/system data), `topic.subscribe(...)` (sensors),
`vibrator.start{...}` (haptic notifications), plus the full Lua 5.4 stdlib.
Lifecycle: `pageOnResume()` / `pageOnPause()`. Script dir: `SCRIPT_PATH`.
