#!/bin/bash
# Joins the camera's Wi-Fi, queries its HTTP API, saves the responses,
# then ALWAYS switches back to the original Wi-Fi (trap EXIT + time limit).
# Usage: packages/tools/probe_camera.sh [path1 path2 ...]   (default: list of known endpoints)

cd "$(dirname "$0")/../.." || exit 1
set -a; . ./.env; set +a

IF=$(networksetup -listallhardwareports | awk '/Wi-Fi|AirPort/{getline; print $2}')
HOME_SSID=$(networksetup -getairportnetwork "$IF" | sed 's/^Current Wi-Fi Network: //')
HOST="${CAMERA_HOST:-192.168.0.1}"
OUT="probe_results/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
LOG="$OUT/log.txt"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

restore() {
  log "Back to the original Wi-Fi: $HOME_SSID"
  networksetup -removepreferredwirelessnetwork "$IF" "$CAMERA_WIFI_SSID" >/dev/null 2>&1
  for i in 1 2 3; do
    networksetup -setairportnetwork "$IF" "$HOME_SSID" >>"$LOG" 2>&1
    sleep 5
    cur=$(networksetup -getairportnetwork "$IF" | sed 's/^Current Wi-Fi Network: //')
    [ "$cur" = "$HOME_SSID" ] && break
    log "Attempt $i failed (now: $cur), Wi-Fi off/on"
    networksetup -setairportpower "$IF" off; sleep 2; networksetup -setairportpower "$IF" on; sleep 8
  done
  # attendre Internet (max ~40 s)
  for i in $(seq 1 20); do curl -s -m 2 -o /dev/null https://api.anthropic.com && break; sleep 2; done
  log "Network now: $(networksetup -getairportnetwork "$IF")"
  log "Results in: $OUT"
}
trap restore EXIT

# Time limit: the script is killed after 240 s (the trap still restores the Wi-Fi)
( sleep 240; kill -TERM $$ 2>/dev/null ) & WATCHDOG=$!
trap 'log "TIMEOUT"; exit 1' TERM

log "Joining $CAMERA_WIFI_SSID on $IF (from $HOME_SSID)"
networksetup -setairportnetwork "$IF" "$CAMERA_WIFI_SSID" "$CAMERA_WIFI_PASSWORD" >>"$LOG" 2>&1

# wait for an IP address on the camera's network (max ~30 s)
for i in $(seq 1 15); do
  ip=$(ipconfig getifaddr "$IF")
  [ -n "$ip" ] && ping -c1 -t1 "$HOST" >/dev/null 2>&1 && break
  sleep 2
done
log "SSID actuel : $(networksetup -getairportnetwork "$IF") / IP locale : ${ip:-aucune}"
ifconfig "$IF" | grep inet >>"$LOG"
netstat -rn -f inet | grep "$IF" | head -5 >>"$LOG"

if [ $# -gt 0 ]; then PATHS=("$@"); else
  PATHS=(/ /v1/ping /v1/props /v1/constants /v1/photos /v1/params /v1/params/camera /v1/params/device /v1/status)
fi

# Each entry: "/path" (GET) or "METHOD /path"
for entry in "${PATHS[@]}"; do
  if [[ "$entry" == *" "* ]]; then m="${entry%% *}"; p="${entry#* }"; else m=GET; p="$entry"; fi
  base="$OUT/${m}$(echo "$p" | tr '/?&=' '____')"
  log "$m http://$HOST$p"
  if [ "$m" = HEAD ]; then
    curl -s -I -m 10 "http://$HOST$p" -o /dev/null -D "$base.headers" 2>>"$LOG"
  else
    curl -s -X "$m" -m 20 --max-filesize 5000000 "http://$HOST$p" -D "$base.headers" -o "$base.body" 2>>"$LOG"
  fi
  allow=$(grep -i '^allow:' "$base.headers" 2>/dev/null | tr -d '\r')
  log "  -> $(head -1 "$base.headers" 2>/dev/null | tr -d '\r') ($(wc -c <"$base.body" 2>/dev/null | tr -d ' ') bytes) $allow"
done

kill "$WATCHDOG" 2>/dev/null
exit 0
