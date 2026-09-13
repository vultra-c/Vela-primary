# Device reconnaissance — what to run on your Band 9 Pro, in order

This is the shortest path from "I have the device" to "we can finish this".
It exists because static analysis has hit its ceiling in a specific place, and
the next fact needed is a **runtime observation**, not another symbol.

## Why a device is needed, and why it is *not* an AI-skill problem

Canopus's loader is **not in the Xiaomi firmware**. Every loader-ish primitive is
absent from both AP and factory images (`0x7fELF`, `binfmt`, `NXFLAT`, `insmod`,
`dlopen` — all count 0, see
[`userland-loader-blueprint.md`](userland-loader-blueprint.md) §1). The thing that
actually loads Canopus modules is Canopus's own compiled supervisor, which ships
as a file on the device — so:

* there is **no binary in the firmware to reverse** for that layer, and
* if AstroBox/Canopus has **ever been installed on your watch**, its supervisor
  and any modules it installed are sitting on `/data` right now.

Whoever wrote Canopus used the device to find the bootstrap. That is the step a
device replaces, and that is what this document gets you.

Everything that *is* in the firmware is already reverse-engineered:
27,474 functions mapped, 25 of 26 entry points resolved with addresses, and the
descriptor field offsets recorded in
[`../native-app/targets/xiaomi-band-9-pro-3.1.175.md`](../native-app/targets/xiaomi-band-9-pro-3.1.175.md).

---

## Step 1 — the watchface probe (no root, no adb, ~15 minutes)

`dist/Probe.face` is a normal custom watchface. It needs no privileges: it runs
in the same Lua sandbox the shipped Pomodoro face runs in.

```sh
python3 tools/device_probe.py     # rebuild it if needed -> dist/Probe.face
```

Install it exactly like `dist/Pomodoro.face`, open it from the watchface list,
and read the pages (tap = next page).

### What the screen tells us

| Line on screen | Meaning | Consequence |
|---|---|---|
| `io=1` | the watchface Lua state **does** open the standard `io` library | the Canopus-style install path (a watchface writing a payload file) is viable on this firmware |
| `io=0` | `io` is linked in but not opened for watchfaces | watchface-based delivery is dead; everything then depends on a shell-based route |
| `exe=1` | `os.execute` exists | there is *some* exec path — a large finding on its own |
| `popen=1` | `io.popen` exists | same |
| `require: …` | which modules a watchface can `require` | tells us how much of the Lua surface the sandbox exposes |

Then the `READ` block, one line per path:

| Result | Meaning |
|---|---|
| `/data/canopus ... OK` | **the Canopus supervisor is on your device** → go to Step 2 and dump it; that is the whole framework's answer sheet |
| `/dev/canopus OK` | the supervisor is loaded and has registered its character device |
| `/proc/version OK`, `/proc/kconfig OK` | we can read the kernel's own view — including whether any module/binfmt support exists |
| `/etc/init.d/rcS OK` | we can read the boot script and find every autostart hook → the likely persistence point |
| `/data/app/quickapp/config.json OK` | the quickapp registry is readable → Channel A gets a concrete target |
| `-` | path absent or not readable from this sandbox |

Finally the `WRITE` block marks each candidate directory `W` (writable), `!`
(wrote but read back wrong) or `n` (could not open). If you see
`/data/canopus/inbox/ W`, the installer design in
`native-app/watchfaces/pomodoro-installer/` works exactly as written.

If `saved` appears at the end, the full report was also written to
`vw_probe.txt` next to the script — that file is the artifact I want.

**Send back:** the screen contents (photos are fine) and, if you can retrieve
it, `vw_probe.txt`.

---

## Step 2 — get a shell (only if you can; this is where the answer sheet is)

Ranked by how much they unlock:

1. **adb shell** — if your firmware/phone app exposes developer mode with adb,
   this is the cleanest. Then:
   ```sh
   adb shell sh - < tools/device-recon.sh 2>&1 | tee recon.txt
   ```
2. **UART pads on the PCB** — if you are willing to open the watch, the serial
   console usually reaches NSH directly. Best case, but hardware work.
3. **AstroBox itself** — if AstroBox is already installed, it can already read
   and write files on `/data`; its own file features are enough to copy
   `/data/canopus/**` out. You do not need a general shell for this.

`tools/device-recon.sh` is read-only: no writes, no mounts, no `insmod`, no
deletes. It prints identity, loader capability (`insmod`/`exec`/`nsh` presence,
`/proc/modules`, `/proc/kconfig`), mounts, **Canopus/AstroBox artifacts**,
boot/autostart hooks, app registries, and `/data` layout.

### The single most valuable thing to retrieve

If `/data/canopus` exists, copy the whole tree out (any method), and in
particular:

* the supervisor binary itself (the thing that loads modules),
* any `*.bin` / `*.elf` module payload it installed,
* `/data/canopus/inbox` contents,
* any config/db file that names targets, hashes or signatures.

With **one** of those binaries I can reverse the CMI1 container, the
module↔supervisor handshake and the real import table — i.e. finish what was
asked, for real. That is the "answer sheet" referred to throughout the docs.

---

## Step 3 — the loader test (only with a shell)

`tools/probe_elf.py` builds three ~250-byte ELF files by hand. They exist to
answer one yes/no question the firmware image cannot: **is there anything on
this device that will run a binary it is handed?**

```sh
python3 tools/probe_elf.py       # -> dist/probes/{probe_exec.elf,probe_mark.elf,probe_rel.o}
```

| File | Run it with | Positive signal | Interpretation |
|---|---|---|---|
| `probe_exec.elf` | `exec <path>/probe_exec.elf` or `/bin/probe_exec.elf` | NuttX prints a fault/`unexpected ISR` report and the watch reboots within a second | something loaded and jumped to our code → a loader exists |
| `probe_mark.elf` | same | CPU spins; a memory dump at `0x2C600000+16` contains `VWPB` | execution confirmed without relying on the crash log (it writes the marker **inside its own mapping**, so nothing else is touched) |
| `probe_rel.o` | `insmod <path>/probe_rel.o` | any NuttX module-loader diagnostic other than "unknown command" | `CONFIG_MODULE`-style loading is present |

A plain `command not found`, `-ENOEXEC`, `Not a valid executable` or
`invalid binary format` is also a result — it is the one the blueprint predicts.
Record the **exact** error text; the string tells us which loader path was tried.

Step 3 deliberately hangs or faults the watch; only run it when a reboot is
acceptable, and note that nothing it writes leaves its own memory mapping.

---

## Step 4 — send results back

Useful to hand back, in priority order:

1. `/data/canopus/**` (or any Canopus/AstroBox binary you find anywhere)
2. the `Probe.face` screen results / `vw_probe.txt`
3. `tools/device-recon.sh` output
4. the exact text of any `exec` / `insmod` error, if you ran Step 3

With (1) the remaining work is implementation, not discovery. With only (2)+(3)
I can still settle which of the four channels in the blueprint is open and
rewrite the installer accordingly.
