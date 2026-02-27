# attendance-pwa ディレクトリ構成

> 最終更新: 2026-02-27 19:25:30  
> このファイルは `generate_structure.py` により自動生成されます（毎朝 07:30）。

## ファイルツリー

```
attendance-pwa/
├── .github/
│   └── workflows/
│       └── ci.yml
├── logs/
├── .env
├── .gitignore
├── create_monthly_report.py
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
├── STRUCTURE.md
├── sw.js
└── sync_jinjer.py
```

## ファイル一覧

| ファイル | サイズ | 最終更新 | 説明 |
|---|---|---|---|
| `.env` | 165 B | 2026-02-24 20:53 | 認証情報（git 管理外）— jinjer ログイン情報 |
| `.gitignore` | 334 B | 2026-02-24 20:53 | Git 除外設定 |
| `create_monthly_report.py` | 2.1 KB | 2026-02-27 19:19 | 翌月作業報告書 Excel 自動生成スクリプト（月初実行） |
| `generate_structure.py` | 8.5 KB | 2026-02-27 19:14 | このファイル — STRUCTURE.md 自動生成（毎朝実行） |
| `icon-192.png` | 3.9 KB | 2026-02-22 15:27 | PWA アイコン 192×192px |
| `icon-512.png` | 10.8 KB | 2026-02-22 15:27 | PWA アイコン 512×512px |
| `icon-apple.png` | 3.7 KB | 2026-02-22 15:27 | iOS ホーム画面アイコン |
| `import.html` | 124.4 KB | 2026-02-22 19:23 | データインポート補助ページ |
| `index.html` | 312.7 KB | 2026-02-27 19:19 | 勤怠カレンダー PWA 本体（全 UI・ロジック） |
| `jinjer_server.py` | 9.3 KB | 2026-02-27 19:16 | ローカル API サーバー (port 8899) — jinjer 同期・報告書 API |
| `jinjer_sync_2025-10_to_2026-02.json` | 18.3 KB | 2026-02-24 20:41 |  |
| `jinjer_sync_2026-02.json` | 3.4 KB | 2026-02-24 19:18 |  |
| `kintai_backup_2026-02-22.json` | 131.7 KB | 2026-02-22 14:59 |  |
| `manifest.json` | 1.1 KB | 2026-02-24 20:53 | PWA マニフェスト（アイコン・表示設定） |
| `migrate_data.py` | 1.4 KB | 2026-02-22 14:50 | データ移行スクリプト（旧フォーマット対応） |
| `recover.html` | 19.5 KB | 2026-02-25 15:31 | 緊急復旧ページ（PWA クラッシュ時） |
| `report_sync.py` | 17.6 KB | 2026-02-27 19:23 | 作業報告書 Excel ↔ kintai データ変換ライブラリ |
| `STRUCTURE.md` | 4.4 KB | 2026-02-27 19:14 | このファイル — ディレクトリ構成図（自動生成） |
| `sw.js` | 2.1 KB | 2026-02-27 12:39 | Service Worker — オフライン対応・キャッシュ戦略 |
| `sync_jinjer.py` | 7.6 KB | 2026-02-24 20:54 | jinjer 勤怠データ取得スクリプト（Playwright） |
| `.github/workflows/ci.yml` | 1.6 KB | 2026-02-24 20:53 |  |

## スクリプト一覧

| スクリプト | 役割 | 実行タイミング |
|---|---|---|
| `generate_structure.py` | STRUCTURE.md を自動生成 | launchd 毎朝 07:30 |
| `sync_jinjer.py` | jinjer から勤怠データを取得し JSON 出力 | 手動 / launchd 毎月28日 |
| `jinjer_server.py` | ローカル API サーバー (port 8899) | 手動 or launchd 起動時 |
| `report_sync.py` | Excel ↔ kintai データ変換 | jinjer_server.py から呼び出し |
| `create_monthly_report.py` | 翌月作業報告書 Excel を自動生成 | launchd 毎月1日 08:00 |
| `migrate_data.py` | 旧フォーマットデータ移行 | 手動（必要時のみ） |

## launchd 自動化ジョブ状態

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
