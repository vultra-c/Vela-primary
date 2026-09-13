# Userland loader blueprint — running native code on 3.1.175

**Question this document answers:** the pomodoro native app, the target pack
([`../native-app/targets/xiaomi-band-9-pro-3.1.175.md`](../native-app/targets/xiaomi-band-9-pro-3.1.175.md))
and the launcher/notification entry points all exist — so what actually *loads*
the module, and what do we do since this firmware has no loader?

Everything below is derived from `vela_ap.bin` / `vela_factory.bin` static
analysis. Regenerate with:

```sh
python3 tools/symbol_hunt.py sweep /tmp/fw/vela_ap.bin /tmp/fw/vela_factory.bin \
    --json targets/vela_ap.symbols.json --md targets/vela_ap.symbols.md
python3 tools/target_pack.py /tmp/fw/vela_ap.bin \
    --target xiaomi-band-9-pro-3.1.175 \
    --json native-app/targets/xiaomi-band-9-pro-3.1.175.json \
    --md   native-app/targets/xiaomi-band-9-pro-3.1.175.md
```

## 1. The loader boundary (measured, not assumed)

Searched every unpacked partition; occurrence counts are from the raw images:

| Loader capability | `vela_ap.bin` | `vela_factory.bin` |
|---|---|---|
| `0x7f 'ELF'` magic constant | 0 | 0 |
| `binfmt` layer | 0 | 0 |
| `NXFLAT` loader | 0 | 0 |
| `insmod` / `rmmod` | 0 | 0 |
| `dlopen` / `dlsym` | 0 | 0 |
| `binfmt_loadmodule` / `binfmt_execmodule` | 0 | 0 |
| `CONFIG_BINFMT` / `CONFIG_MODULE` knob strings | 0 | 0 |

Meanwhile the image *does* contain the infrastructure a loader would sit on:

| Platform capability | Evidence |
|---|---|
| NuttX task/group model | `group/group_create.c`, `group/group_setuptaskfiles.c`, `task/task_setup.c` |
| `posix_spawn` code path | `task/task_posixspawn.c`, `spawn/lib_psa_adddup2.c` |
| NSH shell present as a **builtin** | `nsh_main`, `task/task_start.c` adjacent in rodata; `/bin/nsh` next to the NSH `HOME=/root` string |
| File-backed mmap | `mmap/fs_rammap.c`, device registered as `rammap` |
| Lua 5.4 host + full stdlib | `lugl_*`; `LUA_CPATH`; the **liolib/loslib/loadlib marker cluster** — `FILE*` (`LUA_FILEHANDLE`), `attempt to use a closed file`, `rwa`, `cannot open file '%s' (%s)`, `invalid mode`, `date result cannot be represented in this installation`, `_LOADED`, `_PRELOAD`, `luaopen_%s` (see RE-notes §6) |
| AIOTJS quickapp engine | `quickjs/quickjs.c`, `AIOTJS`, `ferry::PackageManager::installRpk` |
| **AIOTJS native bridge** | `jse_nativeproxy.cpp`, `NativeProxy`, `__folme_native_require__`, `native://` URI regex, `installPlugins` |

Two consequences:

1. **Nothing on this firmware can load an ELF from the filesystem or from
   flash.** The Lua `LUA_CPATH` for `?.so` is a stock Lua compile-time default:
   `package.loadlib` would need `dlopen`, which is absent. (`io`/`os`/`package`
   themselves *are* linked in — that is a data-write capability, not a code-load
   one, and it is what Channel A depends on.)
2. **Threads, tasks, page callbacks and the launcher/notification APIs are all
   present and addressable.** The *destination* of native code exists; only the
   *transport* is missing. That is exactly the gap the closed-source Canopus
   framework fills on its supported targets.

### 1.1 What that means for Canopus on this device

The reference module's README lists its supported targets as Band 10 Pro
`.036`, Band 10 Pro `.043` and Band 11 `.139`. **Mi Band 9 Pro `3.1.175` is not
one of them**, and `CANOPUS_ROOT` selects a private per-target ABI backend from
`targets/<target-id>.env`; an unknown target "fail closed" before any private
ABI call. So even with framework access we would have to author a nine-pro
target pack (this document set is the static-analysis half of that) *and* the
framework's supervisor would still have to get onto the device through whatever
bootstrap it uses — a step that is not documented in the public repo.

## 2. The four candidate channels

Ranked by "does not require information we cannot obtain".

### Channel A — data-only: drive the existing app registry (no new code)

*Premise:* the launcher and page manager build their state from data the OS
already persists (`/data/app/...`, `watchface_list.json`, quickapp
`config.json`, `rpk_info.json`). If **any** of that data is writable by
something a watchface install can touch, an app-list entry can be created
without loading any code.

*Firmware support:* `app_install`, `packagemanager_app_unregister`,
`activitymanager_page_register`, `launcher_page_insert_icon`, `create_app_icon`,
`sort_apps`, `launcher_data_reload_app_name`, `load_app_info`.

*Supporting evidence:* the Lua **`io` library is linked into the image**
(`FILE*` = `LUA_FILEHANDLE`, `attempt to use a closed file`, `rwa`,
`cannot open file '%s' (%s)`, `invalid mode` — the liolib string cluster at
`0x5CD7F3`/`0x5CE436`). The earlier "no io, watchface Lua is sandboxed" note in
RE-notes was based on the absence of a `luaopen_io` *string*, which would not
exist even when the library is present. Independently, the Canopus framework's
own installer watchface is documented to submit its payload with plain
`io.open`, on this same Vela platform family.

*Blocker:* whether the **watchface** `lua_State` actually opens `io` (i.e.
calls `luaL_openlibs`) is not decidable statically — that is a one-line device
test (`io == nil`?), which the installer watchface in this repo reports on
screen. If it is open, the write path is the same one Canopus already uses.

*Verdict:* cheapest to try, and the only channel that produces a launcher icon
with zero native code. Worth a device experiment; cannot be settled statically.

### Channel B — AIOTJS native proxy (quickapp `.rpk`)

*Premise:* `AIOTJS` exposes a JS→native bridge (`jse_nativeproxy.cpp`,
`NativeProxy`, `native://` URI dispatch, `installPlugins`, `pluginEvents`). A
quickapp page is a real app-list app, and inside it the bridge reaches native
functions without any dynamic loading.

*Blocker:* the `.rpk` is verified with mbedtls (`app_verify_info`,
`app_block.signature_block`, `verify_block_signature`) and arrives over the
phone protobuf link (`miwear_pb_msg_prepare_install`, `quickapp_install_start`).
We would need a signing chain accepted by this firmware, or a way to make the
package manager install an unsigned/re-signed `.rpk`.

*Verdict:* the only path that yields a **genuine app-list application**, and the
one the community already uses for third-party watch-side apps. It is an
install-authenticity problem, not a loader problem.

### Channel C — a resident supervisor + injector (what Canopus actually is)

*Premise:* get one native blob execution "for free" once, then keep a
supervisor resident that can register modules through the ABI we already
recovered.

*Blocker:* the one-time bootstrap. Candidates, in order of how plausible the
firmware makes them:

1. **NSH console** — NSH is present as a builtin. A real shell (UART pads, a
   factory/debug mode, or a leaked `nsh` entry point) gives builtin commands
   only (nothing to `exec`), but builtins include flash/memory tools on many
   Vela builds. This is the most promising bootstrap if a shell is reachable.
2. **An existing writable autostart hook** — something the boot sequence
   already reads from `/data` and that can be made to *contain* code rather than
   data (e.g. a font, RLE image, or watchface resource parsed by a decoder).
   This is where a memory-corruption bug would be needed; static analysis cannot
   find it.
3. **The phone link** — the miwear protobuf channel already ships signed
   payloads of several kinds; a type-confusion there would be the "cleanest"
   remote bootstrap, and the `DATA_TYPE_EXEC_TYPE` string in the file-transfer
   code (`[%s] %s: got: DATA_TYPE_EXEC_TYPE : %d`) is worth investigating as a
   named execution path.

*Verdict:* this is the real engineering work, and it requires a physical device.

### Channel D — patch the firmware image and flash it

We have the whole AP image and a full function/string map. Patching `app_install`
call sites, or adding a boot-time hook into the image, is mechanically simple —
but it is a firmware modification, not an installable app, so it does not match
"install the watchface and the app appears". Only relevant for a development
device.

## 3. The supervisor contract (what to build, once one channel works)

Whatever bootstrap is used, the resident supervisor only has to satisfy this
small contract; the module side is already written against it
(`native-app/src/lib.rs`).

```
boot (or first activation)
  ├─ identity guard: verify firmware id/version before touching any address
  ├─ map the module text into the module arena
  ├─ resolve the module's import table against the target pack
  │    (app_install, activitymanager_page_register, app_launcher_add,
  │     miwear_vibrator_run/cancel, lvx_notification_*)
  ├─ call canopus_mod_prepare(ctx)
  └─ call canopus_mod_activate(ctx)

app publication (staged, because the launcher needs the registry event first)
  stage 1: module calls app_install(app_desc, pages[], n)
           supervisor confirms the registry entry exists
  stage 2: module calls app_launcher_add(app_id)
           supervisor triggers launcher refresh (launcher_page_main_update_layout)

runtime
  ├─ page lifecycle -> module callbacks (on_create/resume/pause/destroy)
  ├─ timer dispatch  -> module tick
  ├─ notifications   -> lvx_notification_init_message + insert_message
  └─ haptics         -> miwear_vibrator_run / cancel

teardown
  └─ remove the launcher entry, then either reboot or unload
```

Two properties the module already relies on and the supervisor must honour:

* **Addresses are per-firmware.** Every pointer above comes from the target
  pack for exactly this image; a mismatched pack must fail closed *before* the
  first call, never after.
* **Staged publication is mandatory.** `app_install` and `app_launcher_add` are
  separate generations; adding the launcher entry in the same tick as the
  install is what produces a dead icon.

## 4. What unblocks this project

| # | Item | Unblocks |
|---|---|---|
| 1 | **One Canopus module ELF** (`bluetooth-audio.elf` for any target) | CMI1 container format, module↔supervisor handshake, real import table. Turns Channels C/D from design into implementation. |
| 2 | **A Canopus install watchface `.face`** | The exact install-request wire format and where the signature is checked. |
| 3 | **One device on hand (any firmware)** | Settles Channel A, tests reachability of NSH, allows `/proc` + memory inspection instead of static guessing. |
| 4 | **`openvela` BSP source for this SoC (best-effort)** | Replaces guessed symbol semantics with the real header definitions. |

Without 1–3, Section 3 is as far as static analysis can take it, and the
shipped product is the Lua watchface in [`../dist/Pomodoro.face`](../dist/Pomodoro.face)
plus the complete, verified native-app source tree waiting for a loader.
