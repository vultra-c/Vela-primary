#!/bin/sh
# device-recon.sh — read-only fact collection on a Mi Band 9 Pro.
#
# Run it ON the device (NSH/console/whatever shell you have) and send back the
# output. It only reads: no writes, no mounts, no insmod, no deletes, no
# process kills. Every section is wrapped so one failure does not stop the rest.
#
#   sh device-recon.sh 2>&1
#
# If you have adb instead:  adb shell sh - < device-recon.sh 2>&1 | tee recon.txt
#
# What each section answers is documented in docs/device-recon.md.

say() { echo; echo "===== $* ====="; }
try() { "$@" 2>&1 || echo "[failed: $*]"; }
show() { if [ -r "$1" ]; then echo "--- $1"; cat "$1" 2>&1 | head -40; else echo "--- $1: not readable"; fi; }
has() { command -v "$1" >/dev/null 2>&1 && echo "  present: $1" || echo "  absent : $1"; }

say "identity"
try uname -a
show /proc/version
show /proc/cmdline
try id

say "loader capability (the decisive section)"
echo "-- commands that would load code"
for c in insmod rmmod modprobe exec nsh edit dd hexdump md5sum sha1sum tar; do has "$c"; done
echo "-- builtin app table (names only)"
try nsh -c "help" 
show /proc/modules
show /proc/kconfig
show /proc/filesystems
try cat /proc/self/status

say "mounts and writability"
show /proc/mounts
try df
for d in / /data /data/app /data/canopus /system /tmp /dev; do
  if [ -d "$d" ]; then echo "  dir  : $d"; else echo "  no   : $d"; fi
done
echo "(writability is tested by the Probe.face watchface, not here)"

say "Canopus / AstroBox artifacts (the answer sheet, if present)"
for d in /data/canopus /data/canopus/inbox /data/canopus/packages /data/canopus/modules \
         /data/astrobox /data/app/astrobox /dev/canopus /dev/canopus_audio; do
  if [ -e "$d" ]; then
    echo "  FOUND: $d"
    try ls -la "$d"
  fi
done
echo "-- anything named canopus anywhere we can read"
try find /data /system /etc -maxdepth 4 -iname '*canopus*' 2>/dev/null
try find /data /system /etc -maxdepth 4 -iname '*astro*' 2>/dev/null

say "boot / autostart"
for f in /etc/init.d/rcS /etc/rcS /etc/init.d/rc.sysinit /etc/os-release /etc/passwd; do show "$f"; done
try ls -la /etc/init.d
try ls -la /etc
try ls -la /bin

say "apps and registries (where a launcher entry could come from)"
show /data/app/quickapp/config.json
show /data/app/watchface/watchface_list.json
try ls -la /data/app
try ls -la /data/app/watchface
try ls -la /data/app/watchface/market
try ls -la /data/quickapp
try ls -la /data/quickapp/app

say "filesystem capacity and persistence"
try ls -la /data
try ls -la /data/app/notifications
try cat /data/version 2>/dev/null

say "done"
echo "Nothing was modified. Send this whole output back."
