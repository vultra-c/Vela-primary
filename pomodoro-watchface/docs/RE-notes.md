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
pageOnResume()/pageOnPause()                  -- face lifecycle
SCRIPT_PATH                                   -- face data dir
```

### Correction: the Lua standard libraries *are* linked in

Earlier notes here claimed "no `io`/`os`, watchface Lua is sandboxed", on the
grounds that the image contains no `luaopen_io`/`luaopen_os` strings. That
reasoning was invalid: `luaL_openlibs` calls `luaopen_io` as a **direct symbol
reference**, so no such string would exist even when the library is present.
Searching for what the libraries actually emit instead:

| liolib / loslib / loadlib marker | occurrences |
|---|---|
| `FILE*` (`LUA_FILEHANDLE`, the file metatable) | 1 |
| `attempt to use a closed file` | 1 |
| `rwa` (`l_checkmode` mode characters) | 1 |
| `cannot open file '%s' (%s)` | 1 |
| `invalid mode` | 1 |
| `date result cannot be represented in this installation` | 1 |
| `_LOADED` / `_PRELOAD` / `luaopen_%s` / the croot "no file" error | 1 each |
| `coroutine`, `math`, `debug`, `table`, `string`, `io`, `os` library names | present |

These strings sit interleaved in one rodata cluster starting at `0x5CE436`
(`invalid conversion specifier '%s'`, `invalid mode`, `rwa`, the croot "no
file" error, `cannot open file '%s' (%s)`) and another at `0x5CD7F3` (`FILE*`,
`attempt to use a closed file`) — i.e. `lualib.c`, `liolib.c`, `loslib.c` and
`loadlib.c` are all compiled into the image. The Lua 5.4 stdlib set is there.

What is **still unverified** is which of those libraries the *watchface*
`lua_State` opens. The relevant support is: the Canopus framework's own
installer watchface is documented to submit its payload "仅用普通 `io.open`" on
this same Vela platform family, which only works if `io` is reachable from a
watchface script. The installer watchface in this repo therefore uses plain
`io.open` too, and **guards the call** so a device test distinguishes "`io`
missing" from "path not writable".

Either way the stock face scripts we recovered use no `io`/`os`; nothing in the
shipped `dist/Pomodoro.face` depends on this question.

The `miwear` Lua module exists (`luaopen_miwear`, `lua_module_miwear_close`,
`miwear.topic`, `miwear_apps_available`) but its app-side surface is narrow;
native-app publication (`appID`/`pageID` keys, `WATCHFACE`/`LUA`/`APPID`
strings near the mlua init) is gated inside miwear's protobuf phone protocol.

dataman fields seen in firmware (non-exhaustive): `timeHour/Minute/Second`,
`dateYear/Month/Day/Week`, `healthStepCount/HeartRate/Calorie`,
`systemStatusBattery/Charge/Bluetooth`, `weatherCurrentTemperature`, …

## 7. Native apps on this firmware — the two real channels

### 7a. Quickapp / rpk (Xiaomi's sanctioned third-party app channel)

The AP image ships a full quickapp stack (`proxyquickapp/*`, AIOTJS engine,
`ferry::PackageManager::installRpk`):

- Transfer comes from the **phone app over protobuf**
  (`miwear_pb_msg_prepare_install`, `quickapp_install_prepare/_start`,
  `btmsg_mass_file_qapp_cb`), not from watch-local scripts.
- The `.rpk` is a ZIP verified with mbedtls (`app_verify_info`,
  `app_block.signature_block`, `verify_block_signature`, fingerprint stored in
  `/quickapp/rpk_info.json`), unpacked to `/data/quickapp/app/<pkg>/`, config
  in `/data/app/quickapp/config.json`, icons fetched from the phone.
- Launchable names are protobuf app ids (`com.xiaomi.miwear.tomatotimer` — a
  stock 番茄钟 already exists internally).

This channel gives real app-list apps but requires Xiaomi's signing chain and
the phone-side install protocol — not reproducible from a watchface.

### 7b. Canopus kernel-module channel (what third-party devs actually use)

Per [Canopus-Module-BluetoothAudio](https://github.com/Searchstars/Canopus-Module-BluetoothAudio)
(and its docs), the Canopus framework provides:

- a device-resident **manager**, itself delivered as an install-watchface,
  exposing `/data/canopus/inbox` + `/canopus/install`;
- **kernel modules** (NuttX `insmod` path) loaded from signed CMI1 payloads;
- a **native app ABI**: `ModuleDescriptorV1` with
  `FLAG_HAS_NATIVE_APP|FLAG_REGISTERS_LAUNCHER_ENTRY`, staged publication
  `publish_native_app_stage(1) = app_install(app_desc, pages, n)` /
  `(2) = launcher_add(app_id)` — exactly the launcher functions found in our
  firmware (`app_launcher_add`, `launcher_page_*`, `sort_apps`);
- per-firmware **target packs** with resolved symbol addresses.

Result: the module's pages run at native level with the page framework
(`on_create/on_resume/on_pause/on_destroy`), fully equivalent to a system app.
Framework + target packs are closed source; module payloads must be signed.

**Two corrections to earlier notes in this file** (verified against the
upstream README and a fresh image scan):

1. That project's supported targets are Band 10 Pro `3.101.036`, Band 10 Pro
   `3.101.043` and Band 11 `4.100.139`. **Mi Band 9 Pro `3.1.175` is not a
   Canopus target id.** The framework picks a private ABI backend from
   `targets/<target-id>.env` and fails closed on an unknown target, so a
   nine-pro pack has to be authored, not reused.
2. The loader description above (`insmod` of a NuttX binfmt module) is how the
   *framework* describes itself; this image contains **no such loader**. See
   §10 for the measured boundary.

### 7c. Our own target pack (do this instead of waiting for the framework)

`tools/target_pack.py` turns the AP image into the per-firmware target pack:
25 of 26 needed entry points resolve with exact firmware string matches, plus
per-function field-offset evidence for reconstructing the descriptor layouts.
Output: `native-app/targets/xiaomi-band-9-pro-3.1.175.{md,json}`. The one
unresolved name is `app_lookup` — this firmware exposes the app registry
through `app_install` / `packagemanager_app_unregister` but has no string by
that name (it is deliberately *not* fuzzed onto `quickapp_get_appinfo`, which
is unrelated).

### Consequence for this repo

- Watchface Lua (path shipped in `dist/`) can never appear in the app list.
- The app-list 番茄钟 is implemented in `native-app/` as a Canopus module
  skeleton + installer watchface (see `native-app/README.md`).

## 8. Notifications

- **Available from watchface Lua:** `vibrator` (haptics only).
- **Notification tray is present in the firmware and now has addresses.**
  Earlier notes here said tray insertion was merely "capability-gated / not
  confirmed"; it is confirmed:

  | symbol | address | notes |
  |---|---|---|
  | `lvx_notification_init_message` | `0x2C45F390` | builds a message object |
  | `lvx_notification_insert_message` | `0x2C4F1C44` | 13 callers; same fn refs `notification_manager` + `lvx_notification_start_reminder` |
  | `lvx_notification_insert_many_message` | `0x2C2B4D58` | batch |
  | `lvx_notification_remove_message` | `0x2C4ED540` | |
  | `lvx_notification_remove_all_appid_message` | `0x2C4DFB4E` | per-app clear |
  | `lvx_notification_update_message_icon` | `0x2C2AFEAC` | in-place icon swap |

  None of this is reachable from watchface Lua (no `io`/`os`, no notification
  binding registered), which is exactly why "支持系统通知" is a native-app
  requirement and not a watchface one. From a native module the haptics path is
  `miwear_vibrator_run` `0x2C4602CC` / `miwear_vibrator_cancel` `0x2C460388`.
- ANCS/dialog notification display itself is owned by the `notifications`
  system app; the symbols above insert into its model.

## 9. The loader boundary (measured)

Searched `vela_ap.bin` **and** `vela_factory.bin` (plus `bl`/`bl2`) for every
way native code could be brought in at runtime:

| capability | occurrences |
|---|---|
| `0x7fELF` magic constant | 0 / 0 |
| `binfmt` layer | 0 / 0 |
| `NXFLAT` | 0 / 0 |
| `insmod` / `rmmod` | 0 / 0 |
| `dlopen` / `dlsym` | 0 / 0 |
| `binfmt_loadmodule` / `binfmt_execmodule` | 0 / 0 |
| `CONFIG_BINFMT` / `CONFIG_MODULE` | 0 / 0 |

What *is* present: the NuttX task/group model (`group/*`,
`task/task_posixspawn.c`), NSH as a **builtin** (`nsh_main` next to
`task/task_start.c` in rodata; `/bin/nsh` next to NSH's `HOME=/root`), a
file-backed `rammap`, procfs, and the AIOTJS native bridge
(`jse_nativeproxy.cpp`, `NativeProxy`, `__folme_native_require__`, the
`native://` URI regex). `LUA_CPATH=…?.so` is Lua's stock default string and
`package.loadlib` would need the absent `dlopen`.

Conclusion: the destination of native code (app registry, page callbacks,
launcher, notification tray, haptics) is fully present and now addressable,
but **no loader exists on this firmware** — so a Canopus-style module needs a
one-time native bootstrap that we have not found statically.
[`docs/userland-loader-blueprint.md`](userland-loader-blueprint.md) enumerates
the four candidate channels, the resident-supervisor contract, and exactly what
unblocks each one.

## 10. Limitations / next steps

- Stock face 14 is used as the container host, so its background images and
  header stay (dark clouds bg — fits the pomodoro aesthetic fine). A full
  container rebuild (moving blocks + rewriting all header tables) is possible
  but unnecessary for injection.
- Only `theme*.lua` payloads are swapped; themes 1–3 all run the same script.
  To differentiate themes, write three script variants within the 2468-byte
  budget.
- System notification center integration is **not** available to watchface
  scripts (no notification binding is registered in the Lua state; the
  `miwear` module's surface is narrow), but it *is* available to a native
  module — see §8, where the `lvx_notification_*` entry points are listed with
  addresses. Vibration remains the only notification channel a watchface has.
