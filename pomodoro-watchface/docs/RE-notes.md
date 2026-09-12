# Mi Band 9 Pro — Vela Watchface Reverse Engineering & Pomodoro Injection

Target: Xiaomi Smart Band 9 Pro, firmware `3.1.175.bin` (50 MB OTA from this repo).

## 1. Firmware container

`3.1.175.bin` is a ZIP package. The main application image is `vela_ap.bin`
(7,774,696 bytes) — Xiaomi **Vela OS** (NuttX-based RTOS).

Key strings found in `vela_ap.bin`:

| String | Meaning |
|---|---|
| `luaopen_lvgl`, `lugl_*` | Lua 5.4 + **lugl** (LVGL 8 bindings) |
| `luaopen_dataman`, `lua_topic_subscribe` | data/topic subscription APIs |
| `luaopen_vibrator`, `lua_vibrator_start/cancel` | vibration API |
| `luaopen_screen`, `luaopen_navigator`, `luaopen_activity` | system modules |
| `SCRIPT_PATH`, `?.lua` | watchface script loader |
| `watchface_manager_get_watchface_info_from_file` | face installer/loader |
| `/data/app/watchface/market/<id>/` | installed-face storage |
| `/data/app/watchface/watchface_list.json` | face registry |
| `/system/watchface/<id>/` | 15 builtin faces |

## 2. Watchface storage format (`.face` / `resource.bin`)

All 15 builtin faces were recovered from the OTA payload; all share magic
`5A A5 34 12`. Face **14** (`120917345196`, "Pastel clouds 2") is a **Lua
watchface** — it contains named `_lua/...` segments. This is our injection host.

### Header

```
0x00  u32  magic 0x1234A55A        (5A A5 34 12)
0x04  u8   per-face byte, 0..38 observed (unknown purpose; preserve from host)
0x05  u8   0x00 in every stock face
0x08  32B  zeros / flags
0x28  12B  watchface ID as ASCII, NUL-padded   (e.g. "120917345196";
           stock IDs are 9 or 12 digits — this is the slot EasyFace patches)
0xAC  u32  payload start            (header end)
0x74  u32  name-blob offset (face display name, multi-lang)
...        style/widget/font tables (16-byte records:
           {kind:u32, res:u32, off:u32, size:u32})
           kind>>24: 2=image class, 3=asset, 5=named file (kind5)
```

### Payload (from `0xAC` to EOF)

Two block types, tiled back-to-back:

1. **Resource block** — images/fonts:
   `{u32 raw_size, u16 w, u16 h, u32 comp_len}` + pixel/compressed data
   (variants: raw RGB565, `A55A21E0`-magic compressed, etc.)
2. **Name segment** (the Lua host!):
   ```
   u24  payload_size
   u8   name_len
   16B  zeros
   name (no NUL), e.g. "_lua/theme1/theme1.lua"
   payload (plain Lua source / LVGL image / font)
   ```

Face 14 contains 9 segments:

```
_lua/theme1/theme1.lua   0x9A4 bytes   <- Lua watchface script (theme A)
_lua/theme1/1.bin        0x66F44       <- background image
_lua/theme1/1.png        0x494D1
_lua/theme2/theme2.lua   0x9A4         <- theme B
_lua/theme2/2.bin, 2.png
_lua/theme3/theme3.lua   0x9A4         <- theme C
_lua/theme3/3.bin, 3.png
```

The stock `theme1.lua` (kept in `docs/stock_theme1.lua`) confirms the runtime:

- `SCRIPT_PATH` global = face install dir
- `lvgl` module with `Object/Label/Image/Timer/Font/HOR_RES/VER_RES/OPA/FLAG/...`
- lifecycle: `pageOnResume()` / `pageOnPause()` called by firmware
- screen: 336×480 (per official MiBand9Pro examples; `HOR_RES()/VER_RES()` used)

### Face 14 header quirks (verified)

- `0xA8: 0x80000000`, `0xAC: payload_start`, `0xB0: widget count`
- widget pairs `(u32 param_off, u32 type)` per style; three style blocks
  (one per theme) at `0xA8/0x148/0x1FC…`-ish offsets, each ~0xA0 bytes apart
- kind5 table entries at `0x588-0x5F8` (style1), `0x8F8-0x968` (style2),
  `0xB08-0xB78` (style3) reference the 9 name segments by `(off,size)`

## 3. Injection approach

Because the header tables reference payload blocks by absolute offset, the
safe injection keeps **all block sizes unchanged**: overwrite each
`_lua/themeN/themeN.lua` payload **in place** (exactly 0x9A4 bytes, padded
with `--` line comments), and patch the 12-byte watchface ID at `0x28`
(NUL-padded; header bytes 0x04/0x05 are preserved untouched — they are not
version/length fields).

Result: `dist/Pomodoro.face` — byte-diff against the stock face shows changes
only in the 3 Lua payloads + the ID field (verified by `tools/check_build.py`).

Install: push the file to the band as a custom watchface
(`/data/app/watchface/market/<id>/resource.bin` + entry in
`watchface_list.json`), which is what the Zepp Life/watchface-market
install flow does for `.face` packages.

## 4. The Pomodoro watchface (番茄钟)

`app/lua/main.lua` (readable) / `app/lua/main_packed.lua` (minified to fit
2468 bytes) implements:

- 25 min FOCUS / 5 min BREAK / 15 min LONG BREAK every 4 rounds
- progress bar, mode + round labels, MiSans font
- tap zones: left half = RESET, right half = START/PAUSE
- **notification support** via the firmware vibration service:
  `require("vibrator")` → `vibrator.start{duration=...}` (long buzz when a
  focus round ends, short when a break ends, tick on every tap)
- `pageOnPause/pageOnResume` lifecycle: timer stops when the face is
  deactivated (firmware-managed screen off), state redraws on wake
- graceful degradation: every optional API (`vibrator`, `dataman`) is loaded
  with `pcall(require, ...)`, and the Arc widget falls back to a bar if
  `lvgl.Arc` is missing

Verified in a Lua 5.4 simulator (lupa) with stubbed lvgl/dataman/vibrator:
full 4-round flow, phase transitions at exact tick counts, vibration events,
reset behavior.

## 5. Tooling

`tools/facepack.py`:

```
python3 tools/facepack.py list   <face.bin>                 # show segments
python3 tools/facepack.py unpack <face.bin> <outdir>        # extract _lua/...
python3 tools/facepack.py inject <face.bin> <script.lua> <segname> [out]
python3 tools/facepack.py setid  <face.bin> <id> [out]      # 12-byte NUL-padded slot
python3 tools/facepack.py selftest <face.bin>               # setid/get_id round-trip
```

Build (reproduce `dist/Pomodoro.face`):

```
python3 tools/facepack.py inject /tmp/fw/stockfaces/face_14/resource.bin \
        app/lua/main_packed.lua _lua/theme1/theme1.lua /tmp/s1.bin
python3 tools/facepack.py inject /tmp/s1.bin app/lua/main_packed.lua \
        _lua/theme2/theme2.lua /tmp/s2.bin
python3 tools/facepack.py inject /tmp/s2.bin app/lua/main_packed.lua \
        _lua/theme3/theme3.lua /tmp/Pomodoro.face
python3 tools/facepack.py setid /tmp/Pomodoro.face 491552737
```

## 6. Lua API surface available to watchfaces (from firmware + examples)

```
lvgl.Object/Label/Image/Arc/Timer/Font, lvgl.HOR_RES/VER_RES/OPA/FLAG/EVENT/ALIGN
dataman.subscribe("timeSecond"|"timeHour"|"systemStatusBattery"|..., obj, cb)
topic.subscribe("sensor_accel", 0, cb)        -- sensor topics
vibrator.start{...} / vibrator.cancel(...)    -- vibration notifications
os.time/os.date, io, debug, string, math      -- full Lua 5.4 stdlib
pageOnResume()/pageOnPause()                  -- face lifecycle
SCRIPT_PATH                                   -- face data dir
```

dataman fields seen in firmware (non-exhaustive): `timeHour/Minute/Second`,
`dateYear/Month/Day/Week`, `healthStepCount/HeartRate/Calorie`,
`systemStatusBattery/Charge/Bluetooth`, `weatherCurrentTemperature`, …

## 7. Limitations / next steps

- Stock face 14 is used as the container host, so its background images and
  header stay (dark clouds bg — fits the pomodoro aesthetic fine). A full
  container rebuild (moving blocks + rewriting all header tables) is possible
  but unnecessary for injection.
- Only `theme*.lua` payloads are swapped; themes 1–3 all run the same script.
  To differentiate themes, write three script variants within the 2468-byte
  budget.
- System notification center integration would require the miwear app
  (`luaopen_miwear`) surface, which stock watchfaces don't use; vibration is
  the reliable notification channel available to watchface scripts.
