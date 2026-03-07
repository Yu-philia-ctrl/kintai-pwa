#!/bin/bash
# Tailscale HTTPS Serve セットアップ
# kintai サーバー (port 8899) を Tailscale HTTPS で公開する
# 実行: bash setup_tailscale_serve.sh

TS="/Applications/Tailscale.localized/Tailscale.app/Contents/MacOS/Tailscale"

echo "[INFO] Tailscale Serve を設定中..."
"$TS" serve --bg 8899
sleep 2
"$TS" serve status
echo "[INFO] 完了。HTTPS URL は上記を確認してください。"
