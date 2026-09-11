#!/bin/bash
# Headless beta run: Xvfb :6 + Mesa llvmpipe, Steam copy as shipped, log to $1, run for $2 seconds.
LOG="$1"; SECS="${2:-90}"; S=$(dirname "$LOG")
D=/mnt/Yucky/SteamLibrary/steamapps/common/Qwilight
Xvfb :6 -screen 0 2560x1440x24 +extension GLX -nolisten tcp >/dev/null 2>&1 & XPID=$!
sleep 2
sed "s|^LOG=.*|LOG=\"$LOG\"|" ~/.steam/root/compatibilitytools.d/qwilight-wine/run > $S/run-betahl; chmod +x $S/run-betahl
cd "$D"
env DISPLAY=:6 QWILIGHT_DEV_BUILD="${QWILIGHT_DEV_BUILD:-0}" LIBGL_ALWAYS_SOFTWARE=1 __GLX_VENDOR_LIBRARY_NAME=mesa __EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/50_mesa.json GALLIUM_DRIVER=llvmpipe \
    QWILIGHT_WINEDEBUG="${QWDEBUG:-warn+module,+timestamp,+seh}" setsid nohup $S/run-betahl waitforexitandrun "$D/Qwilight.exe" >/dev/null 2>&1 < /dev/null &
sleep 5
PID=$(pgrep -x Qwilight.exe | head -1); echo "pid ${PID:-none} at $(date +%T)"
for i in $(seq 1 "$SECS"); do sleep 1
  if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then echo "exited after ${i}s"; break; fi
  if [ $((i % 10)) -eq 0 ]; then DISPLAY=:6 import -window root "$S/$(basename "$LOG" .log)-$i.png" 2>/dev/null; fi
done
echo "alive: $(kill -0 "$PID" 2>/dev/null && echo yes || echo no)"
if kill -0 "$PID" 2>/dev/null; then kill "$PID"; sleep 3; kill -0 "$PID" 2>/dev/null && kill -9 "$PID"; fi
kill $XPID 2>/dev/null
