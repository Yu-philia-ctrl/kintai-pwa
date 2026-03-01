# attendance-pwa ディレクトリ構成

> 最終更新: 2026-03-01 12:03:07  
> このファイルは `generate_structure.py` により自動生成されます（毎朝 07:30）。

## ファイルツリー

```
attendance-pwa/
├── .github/
│   └── workflows/
│       └── ci.yml
├── data/
│   ├── kintai_store.bak1772329776
│   ├── kintai_store.bak1772329884
│   ├── kintai_store.bak1772329917
│   └── kintai_store.json
├── logs/
│   ├── dates/
│   │   └── 2026-02-28/
│   │       ├── server.log
│   │       └── watchdog.log
│   ├── jinjer_end.log
│   ├── jinjer_end_err.log
│   ├── monthly.log
│   ├── monthly_err.log
│   ├── server.log
│   ├── server_err.log
│   ├── structure.log
│   ├── structure_err.log
│   ├── watchdog.log
│   └── watchdog_err.log
├── .env
├── .gitignore
├── create_monthly_report.py
├── docker-compose.yml
├── Dockerfile
├── generate_structure.py
├── icon-192.png
├── icon-512.png
├── icon-apple.png
├── import.html
├── index.html
├── jinjer_server.py
├── jinjer_sync_2025-10_to_2026-02.json
├── jinjer_sync_2026-02.json
├── kintai_backup_2026-02-22.json
├── manifest.json
├── migrate_data.py
├── recover.html
├── report_sync.py
├── requirements.txt
├── STRUCTURE.md
├── sw.js
├── sync_jinjer.py
└── watchdog.py
```

## ファイル一覧

| ファイル | サイズ | 最終更新 | 説明 |
|---|---|---|---|
| `.env` | 165 B | 2026-02-24 20:53 | 認証情報（git 管理外）— jinjer ログイン情報 |
| `.gitignore` | 492 B | 2026-02-28 19:14 | Git 除外設定 |
| `create_monthly_report.py` | 2.1 KB | 2026-02-27 19:19 | 翌月作業報告書 Excel 自動生成スクリプト（月初実行） |
| `docker-compose.yml` | 1.8 KB | 2026-03-01 12:02 |  |
| `Dockerfile` | 1.1 KB | 2026-03-01 12:02 |  |
| `generate_structure.py` | 8.8 KB | 2026-02-28 01:43 | このファイル — STRUCTURE.md 自動生成（毎朝実行） |
| `icon-192.png` | 3.9 KB | 2026-02-22 15:27 | PWA アイコン 192×192px |
| `icon-512.png` | 10.8 KB | 2026-02-22 15:27 | PWA アイコン 512×512px |
| `icon-apple.png` | 3.7 KB | 2026-02-22 15:27 | iOS ホーム画面アイコン |
| `import.html` | 124.4 KB | 2026-02-22 19:23 | データインポート補助ページ |
| `index.html` | 652.5 KB | 2026-03-01 12:02 | 勤怠カレンダー PWA 本体（全 UI・ロジック） |
| `jinjer_server.py` | 59.2 KB | 2026-03-01 11:53 | ローカル API サーバー (port 8899) — jinjer 同期・報告書 API |
| `jinjer_sync_2025-10_to_2026-02.json` | 18.3 KB | 2026-02-24 20:41 |  |
| `jinjer_sync_2026-02.json` | 3.4 KB | 2026-02-24 19:18 |  |
| `kintai_backup_2026-02-22.json` | 131.7 KB | 2026-02-22 14:59 |  |
| `manifest.json` | 1.1 KB | 2026-02-24 20:53 | PWA マニフェスト（アイコン・表示設定） |
| `migrate_data.py` | 1.4 KB | 2026-02-22 14:50 | データ移行スクリプト（旧フォーマット対応） |
| `recover.html` | 19.5 KB | 2026-02-25 15:31 | 緊急復旧ページ（PWA クラッシュ時） |
| `report_sync.py` | 17.9 KB | 2026-02-28 01:12 | 作業報告書 Excel ↔ kintai データ変換ライブラリ |
| `requirements.txt` | 181 B | 2026-03-01 12:02 |  |
| `STRUCTURE.md` | 6.8 KB | 2026-03-01 11:45 | このファイル — ディレクトリ構成図（自動生成） |
| `sw.js` | 2.1 KB | 2026-03-01 12:03 | Service Worker — オフライン対応・キャッシュ戦略 |
| `sync_jinjer.py` | 15.5 KB | 2026-03-01 02:50 | jinjer 勤怠データ取得スクリプト（Playwright） |
| `watchdog.py` | 2.2 KB | 2026-02-28 01:43 |  |
| `logs/jinjer_end.log` | 46 B | 2026-02-28 19:10 |  |
| `logs/jinjer_end_err.log` | 856 B | 2026-02-28 19:10 |  |
| `data/kintai_store.bak1772329776` | 15.2 KB | 2026-03-01 04:05 |  |
| `data/kintai_store.bak1772329884` | 10.0 KB | 2026-03-01 10:49 |  |
| `data/kintai_store.bak1772329917` | 10.0 KB | 2026-03-01 10:51 |  |
| `data/kintai_store.json` | 10.0 KB | 2026-03-01 10:51 |  |
| `logs/monthly.log` | 0 B | 2026-03-01 10:06 |  |
| `logs/monthly_err.log` | 449 B | 2026-03-01 10:06 |  |
| `logs/server.log` | 169.7 KB | 2026-03-01 12:03 |  |
| `logs/server_err.log` | 141 B | 2026-02-27 20:09 |  |
| `logs/structure.log` | 214 B | 2026-03-01 10:06 |  |
| `logs/structure_err.log` | 0 B | 2026-02-28 13:06 |  |
| `logs/watchdog.log` | 112.7 KB | 2026-03-01 12:02 |  |
| `logs/watchdog_err.log` | 0 B | 2026-02-28 01:41 |  |
| `.github/workflows/ci.yml` | 1.6 KB | 2026-02-24 20:53 |  |
| `logs/dates/2026-02-28/server.log` | 169.5 KB | 2026-03-01 12:03 |  |
| `logs/dates/2026-02-28/watchdog.log` | 112.7 KB | 2026-03-01 12:02 |  |

## スクリプト一覧

| スクリプト | 役割 | 実行タイミング |
|---|---|---|
| `generate_structure.py` | STRUCTURE.md を自動生成 | launchd 毎朝 07:30 / サーバー起動時 |
| `watchdog.py` | サーバー死活監視・自動 kickstart | launchd 60秒ごと |
| `sync_jinjer.py` | jinjer から勤怠データを取得し JSON 出力 | 手動 / launchd 毎月28日 |
| `jinjer_server.py` | ローカル API サーバー (port 8899) | launchd 常時稼働 (KeepAlive) |
| `report_sync.py` | Excel ↔ kintai データ変換 | jinjer_server.py から呼び出し |
| `create_monthly_report.py` | 翌月作業報告書 Excel を自動生成 | launchd 毎月1日 08:00 |
| `migrate_data.py` | 旧フォーマットデータ移行 | 手動（必要時のみ） |

## launchd 自動化ジョブ状態

- **com.kintai.server**: ✅ 登録済み — KeepAlive — API サーバー port 8899 (常時稼働)
- **com.kintai.watchdog**: ✅ 登録済み — 60秒ごと — サーバー死活監視・自動再起動
- **com.kintai.structure**: ✅ 登録済み — 毎朝 07:30 — STRUCTURE.md 更新
- **com.kintai.monthly**: ✅ 登録済み — 毎月 1日 08:00 — 翌月 Excel 自動生成
- **com.kintai.jinjer-end**: ✅ 登録済み — 毎月 28日 19:00 — jinjer 月末同期

plist 格納先: `/Users/crystallization/Library/LaunchAgents`

## API エンドポイント (jinjer_server.py port 8899)

| エンドポイント | メソッド | 説明 |
|---|---|---|
| `/api/jinjer` | GET | jinjer から勤怠データを取得（`?months=2026-02`） |
| `/api/reports` | GET | Work_Report フォルダのファイル一覧を返す |
| `/api/reports/read` | GET | 指定月の Excel を JSON 化（`?year=2026&month=02`） |
| `/api/reports/sync` | POST | kintai データを Excel に書き込み |
| `/api/reports/generate` | POST | 翌月 Excel を自動生成 |
| `/api/structure` | GET | STRUCTURE.md の内容を返す |

## 関連ディレクトリ

| パス | 用途 |
|---|---|
| `~/Library/Mobile Documents/com~apple~CloudDocs/kintai/` | jinjer 同期 JSON の iCloud バックアップ |
| `~/Library/Mobile Documents/com~apple~CloudDocs/:root/Work_Report/` | 作業報告書 Excel ファイル群 |
| `~/Library/LaunchAgents/` | launchd plist ファイル |
