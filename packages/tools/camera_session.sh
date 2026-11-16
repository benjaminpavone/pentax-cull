#!/bin/bash
# Joins the camera Wi-Fi, runs a command, then ALWAYS switches back to the original Wi-Fi
# (trap EXIT + watchdog). Lets an AI assistant or a remote shell test the camera without being cut off.
# Usage: packages/tools/camera_session.sh <command> [args...]
# Output goes to probe_results/<date>/ (log.txt + whatever the command writes into $OUT).

cd "$(dirname "$0")/../.." || exit 1
set -a; . ./.env; set +a

IF=$(networksetup -listallhardwareports | awk '/Wi-Fi|AirPort/{getline; print $2}')
HOME_SSID=$(networksetup -getairportnetwork "$IF" | sed 's/^Current Wi-Fi Network: //')
export CAMERA_HOST="${CAMERA_HOST:-192.168.0.1}"
export OUT="probe_results/$(date +%Y%m%d-%H%M%S)"
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
  for i in $(seq 1 20); do curl -s -m 2 -o /dev/null https://api.anthropic.com && break; sleep 2; done
  log "Network now: $(networksetup -getairportnetwork "$IF")"
  log "Results in: $OUT"
}
trap restore EXIT

( sleep "${MAX_SECONDS:-300}"; kill -TERM $$ 2>/dev/null ) & WATCHDOG=$!
trap 'log "TIMEOUT"; pkill -P $$ 2>/dev/null; exit 1' TERM

log "Joining $CAMERA_WIFI_SSID on $IF (from $HOME_SSID)"
for attempt in $(seq 1 6); do
  networksetup -setairportnetwork "$IF" "$CAMERA_WIFI_SSID" "$CAMERA_WIFI_PASSWORD" >>"$LOG" 2>&1
  for i in $(seq 1 10); do
    curl -s -m 2 "http://$CAMERA_HOST/v1/ping" | grep -q '"errCode": *200' && break 2
    sleep 1
  done
  log "Camera not reachable yet (attempt $attempt)"
done
log "Wi-Fi: $(networksetup -getairportnetwork "$IF") / IP: $(ipconfig getifaddr "$IF")"

log "Running: $*"
"$@" 2>&1 | tee -a "$LOG"

kill "$WATCHDOG" 2>/dev/null
exit 0
