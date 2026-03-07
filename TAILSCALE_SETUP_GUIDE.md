# kintai PWA — Tailscale VPN 外部接続 完全手順書

> Mac・iPhone が **同じWi-Fiに繋がっていない状態** (モバイル回線・外出先) でも
> kintai PWA のローカルサーバー(port 8899)に安全に接続するためのガイドです。

---

## なぜ Tailscale が必要か

```
【同じWi-Fi内】
iPhone ───────── Wi-Fiルーター ─────── Mac(192.168.x.x:8899)  ✅ 繋がる

【外出先・モバイル回線】
iPhone ───── 4G/5G ─────── インターネット ─── Mac  ✅ 通常は繋がらない

【Tailscale VPN導入後】
iPhone ─── 4G/5G ─── Tailscale クラウド ─── Mac  ✅ 必ず繋がる
           (暗号化)   (中継サーバー)         (100.65.221.66)
```

Tailscale は各デバイスに **プライベートIP (100.x.x.x)** を割り当て、
どのネットワークからでも安全に通信できるメッシュVPNです。

---

## 接続の仕組み

| 接続方法 | URL | 使える場面 |
|---------|-----|---------|
| **Tailscale HTTPS** (serve) | `https://yusukemacbook-pro.taile663f5.ts.net` | Tailscale ON 時・最優先 |
| **Tailscale HTTP** | `http://yusukemacbook-pro.taile663f5.ts.net:8899` | Tailscale ON 時 |
| **Tailscale IP** | `http://100.65.221.66:8899` | Tailscale ON 時 |
| **同じWi-Fi** | `http://192.168.x.x:8899` | 同一LAN内のみ |
| **Cloudflare Tunnel** | `https://*.trycloudflare.com` | Mac起動中は常時 (URL変動) |

---

## 前提条件

- **Mac**: Tailscale インストール済み (`/Applications/Tailscale.localized/`)
- **iPhone**: App Store から **Tailscale** をインストール
- **アカウント**: 同一Tailscaleアカウント (`grhhwj7ppq@`) でサインイン済み

---

## セットアップ手順

### Step 1: Mac — Tailscale を起動する

**毎回手動で起動する方法:**
```bash
/Applications/Tailscale.localized/Tailscale.app/Contents/MacOS/Tailscale up
```

**起動確認:**
```bash
/Applications/Tailscale.localized/Tailscale.app/Contents/MacOS/Tailscale status
```
以下が表示されれば OK:
```
100.65.221.66   yusukemacbook-pro  ...   macOS  -
100.93.30.114   iphone-13-pro      ...   iOS    -
```

> ⚠️ **注意**: App Store版 Tailscale は macOS ログイン時に自動起動しますが、
> 何らかの原因で停止することがあります。
> 自動復旧のため `com.kintai.tailscale-watchdog` (launchd) が5分ごとに監視中。

---

### Step 2: Mac — Tailscale serve を起動する (初回のみ)

```bash
/Applications/Tailscale.localized/Tailscale.app/Contents/MacOS/Tailscale serve --bg 8899
```

成功メッセージ:
```
https://yusukemacbook-pro.taile663f5.ts.net/
|-- proxy http://127.0.0.1:8899
Serve started and running in the background.
```

**serve の状態確認:**
```bash
/Applications/Tailscale.localized/Tailscale.app/Contents/MacOS/Tailscale serve status
```

> ✅ serve 設定は Tailscale に保存されるため、Tailscale 再起動後も自動で復元されます。

---

### Step 3: iPhone — Tailscale を ON にする

1. iPhone で **Tailscale アプリ**を開く
2. スイッチを ON にする
3. VPN プロファイルの許可を求められたら「許可」
4. ステータスが **「接続済み」** になることを確認

**iPhone の Tailscale IP**: `100.93.30.114`

---

### Step 4: iPhone — 接続先 URL を登録する (QR スキャン)

1. **Mac の kintai PWA** にアクセス: `http://localhost:8899/`
2. **ハブ** タブを開く
3. サーバーカード右上の 🔄 をタップして更新
4. **QR コードが表示される** → iPhone のカメラでスキャン
5. GitHub Pages が開き、Tailscale URL が **自動保存** される

> QR コードには以下の情報が埋め込まれています:
> - `?ts=https://yusukemacbook-pro.taile663f5.ts.net`
> - `?lan=http://yusukenoMacBook-Pro.local:8899`

---

### Step 5: テスト — Wi-Fi 外からの接続確認

1. iPhone の **Wi-Fi をOFF** にする (モバイル回線に切り替え)
2. Tailscale は **ON のまま**
3. Safari で `https://yu-philia-ctrl.github.io/kintai-pwa/` にアクセス
4. 自動的にサーバーへ接続される (数秒かかる場合あり)
5. ハブタブの「🔒 Tailscale で接続する」ボタンが **「HTTPS 接続可」** と表示

**または直接アクセス:**
```
https://yusukemacbook-pro.taile663f5.ts.net
```
→ kintai PWA のトップページが表示される

---

## トラブルシューティング

### ❌ 「サーバが見つかりません」

**原因と対処:**

| 原因 | 対処 |
|------|------|
| Mac の Tailscale が停止している | `tailscale up` を実行 |
| iPhone の Tailscale が OFF | Tailscale アプリで ON にする |
| Mac の kintai サーバーが停止 | `curl http://localhost:8899/api/health` で確認 |
| QR コードをスキャンしていない | Step 4 を実行して URL を登録 |

**Mac 側の一括確認コマンド:**
```bash
# Tailscale 状態
/Applications/Tailscale.localized/Tailscale.app/Contents/MacOS/Tailscale status

# serve 状態
/Applications/Tailscale.localized/Tailscale.app/Contents/MacOS/Tailscale serve status

# kintai サーバー状態
curl http://localhost:8899/api/health
```

---

### ❌ 「HTTPS 接続可」が表示されない

iPhone の localStorage に Tailscale URL が保存されていません。

**解決方法:**
1. Mac で `http://localhost:8899/` → ハブタブ
2. サーバーカードの QR を iPhone でスキャン
3. iPhone で `_kintai_tailscale_https_url` が保存されたか確認:
   - Safari の開発ツール → localStorage を確認

---

### ❌ Tailscale serve が起動しない

```
Serve is not enabled on your tailnet.
```

**原因**: Tailscale 管理パネルで serve 機能が無効になっている。

**対処**: 以下 URL をブラウザで開いて「Enable」をクリック:
```
https://login.tailscale.com/f/serve?node=ni9ph9BGX211CNTRL
```

---

### ❌ Tailscale が頻繁に停止する

**原因**: macOS がバッテリー節約のため VPN を停止させる場合がある。

**対処:**
1. `システム設定` → `バッテリー` → `使用中に最適化` → OFF
2. watchdog が5分ごとに自動復旧するため、通常5分以内に自動回復

**手動で watchdog を実行:**
```bash
bash ~/root/attendance-pwa/tailscale_watchdog.sh
```

---

## 接続フロー図

```
iPhone (Wi-Fi OFF / モバイル)
    │
    ├─ Tailscale ON? ──── NO → Tailscale アプリを ON にする
    │
    ├─ YES
    │   │
    │   └─ GitHub Pages にアクセス
    │       (https://yu-philia-ctrl.github.io/kintai-pwa/)
    │           │
    │           ├─ _resolveServerUrl() 実行
    │           │   ├─ Step 1: localhost:8899 → ❌ (外出先)
    │           │   ├─ Step 2: Cloudflare Tunnel → ✅ (あれば)
    │           │   ├─ Step 3: LAN (192.168.x.x) → ❌ (外出先)
    │           │   └─ Step 4: Tailscale HTTPS → ✅ (Tailscale ON)
    │           │       └─ https://yusukemacbook-pro.taile663f5.ts.net
    │           │
    │           └─ サーバー接続完了 ✅
    │
Mac (Tailscale ON + serve 稼働中)
    └─ Tailscale serve が localhost:8899 を HTTPS でラップ
       → iPhone からアクセス可能
```

---

## launchd 自動化 (設定済み)

| ジョブ | 役割 | 間隔 |
|--------|------|------|
| `com.kintai.tailscale-watchdog` | Tailscale UP + serve を5分ごとに確認・再起動 | 5分 |
| `com.kintai.server` | kintai サーバー (port 8899) 常時稼働 | KeepAlive |
| `com.kintai.watchdog` | kintai サーバー死活監視 | 1分 |

---

## 緊急時: 直接 URL 入力

iPhone Safari で以下を直接入力すれば接続可能 (Tailscale ON 必須):

```
https://yusukemacbook-pro.taile663f5.ts.net
```

---

*作成: 2026-03-07 | kintai PWA v53*
