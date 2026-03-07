#!/bin/bash
# tailscale_watchdog.sh — Tailscale + serve を常時維持するウォッチドッグ
# launchd から 5分ごとに呼び出される

TS_BIN="/Applications/Tailscale.localized/Tailscale.app/Contents/MacOS/Tailscale"
LOG="/Users/crystallization/root/attendance-pwa/logs/tailscale_watchdog.log"

ts_log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# ログローテーション (1MB超えで削除)
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 1048576 ] && mv "$LOG" "${LOG}.bak"

# 1. Tailscale 起動確認
STATUS=$("$TS_BIN" status 2>&1)
if echo "$STATUS" | grep -q "is stopped\|Logged out"; then
    ts_log "Tailscale stopped — running tailscale up"
    "$TS_BIN" up 2>&1 | ts_log
    sleep 5
fi

# 2. tailscale serve 確認・再設定
SERVE=$("$TS_BIN" serve status 2>&1)
if ! echo "$SERVE" | grep -q "8899"; then
    ts_log "serve not running — starting serve --bg 8899"
    "$TS_BIN" serve --bg 8899 2>&1 | ts_log
fi
