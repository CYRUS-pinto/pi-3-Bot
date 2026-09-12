#!/bin/bash
# TARS camera watchdog: the camera MUST work after any restart, always.
# Owns everything TARS cannot do for itself:
#   1. waits for the mini phone over ADB (Pi boots faster than the phone enumerates),
#   2. (re)creates `adb forward tcp:8090 tcp:8080` whenever it is missing
#      (forwards die on every adb restart / Pi reboot / cable wiggle — the old
#      one-shot forward in ExecStartPre raced boot and never retried),
#   3. (re)starts the IP Webcam server in the app (tap START) when the stream is dead.
# TARS itself retries the 8090 endpoint every 2s, so once this script turns the
# bytes back on, the robot locks on with zero human touches. Runs forever.
ADB="adb"
APP_ACTIVITY="com.pas.webcam/.Rolling"
START_TAP_X=226
START_TAP_Y=754
log() { echo "tars-camwatch: $*"; }

ensure_adb() {
    for _ in $(seq 1 30); do
        $ADB start-server >/dev/null 2>&1
        if $ADB devices 2>/dev/null | grep -q "	device$"; then
            return 0
        fi
        sleep 2
    done
    return 1
}

ensure_forward() {
    $ADB forward --list 2>/dev/null | grep -q "tcp:8090" && return 0
    $ADB forward tcp:8090 tcp:8080 >/dev/null 2>&1
    sleep 1
    $ADB forward --list 2>/dev/null | grep -q "tcp:8090"
}

stream_alive() {
    _bytes=$(curl -s --max-time 6 -o /dev/null -w "%{size_download}" http://127.0.0.1:8090/video 2>/dev/null)
    [ -n "$_bytes" ] && [ "$_bytes" -gt 10000 ] 2>/dev/null
}

start_app_server() {
    # ponytail 2026-09-12: unattended boot — phone may be asleep/locked after its own reboot.
    # Wake it (needs: no PIN lock, just swipe/none) so the START tap can land.
    $ADB shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1
    $ADB shell wm dismiss-keyguard >/dev/null 2>&1
    sleep 1
    $ADB shell am start -n "$APP_ACTIVITY" >/dev/null 2>&1
    sleep 3
    for _ in 1 2 3; do
        stream_alive && return 0
        $ADB shell input tap "$START_TAP_X" "$START_TAP_Y" >/dev/null 2>&1
        sleep 4
    done
    stream_alive
}

log "watchdog started"
while true; do
    if ! ensure_adb; then
        log "no adb device yet, retrying"
        sleep 10
        continue
    fi
    if ! ensure_forward; then
        log "forward missing and re-add failed, will retry"
    fi
    if stream_alive; then
        : # healthy — stay quiet
    else
        log "stream dead, (re)starting IP Webcam server"
        if start_app_server; then
            log "stream alive"
        else
            log "still dead after app start attempts, retrying"
        fi
    fi
    sleep 15
done
