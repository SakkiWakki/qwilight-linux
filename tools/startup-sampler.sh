#!/bin/bash
# Samples the game and wineserver every 2 s for $1 seconds into $2 (per-interval CPU, not cumulative).
SECS=${1:-120}; OUT=$2; : > "$OUT"; pg=0; pw=0
for i in $(seq 1 $((SECS/2))); do
  P=$(pgrep -x Qwilight.exe | head -1); W=$(pgrep -x wineserver | head -1)
  if [ -n "$P" ]; then
    g=$(awk '{print $14+$15}' /proc/$P/stat 2>/dev/null); w=$(awk '{print $14+$15}' /proc/$W/stat 2>/dev/null)
    echo "$(date +%T) game_cpu=$(( (g-pg)*100/200 ))% thr=$(ps -o nlwp= -p $P | tr -d ' ') stat=$(ps -o stat= -p $P) read=$(awk '/read_bytes/{print $2}' /proc/$P/io) srv_cpu=$(( (w-pw)*100/200 ))%" >> "$OUT"; pg=$g; pw=$w
  fi
  sleep 2
done
