#!/usr/bin/env python3
"""
STRUCTURE.md 自動生成スクリプト
attendance-pwa ディレクトリの構成図を常に最新の状態で STRUCTURE.md に出力する。

使い方:
  python3 generate_structure.py

launchd により毎朝 07:30 に自動実行される。
"""
import os
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
OUTPUT = ROOT / 'STRUCTURE.md'

# 除外するパス
EXCLUDE = {'.git', '__pycache__', 'node_modules', '.DS_Store'}

# ファイルごとの説明
FILE_DESCRIPTIONS = {
    'index.html':             '勤怠カレンダー PWA 本体（全 UI・ロジック）',
    'sw.js':                  'Service Worker — オフライン対応・キャッシュ戦略',
    'manifest.json':          'PWA マニフェスト（アイコン・表示設定）',
    'recover.html':           '緊急復旧ページ（PWA クラッシュ時）',
    'import.html':            'データインポート補助ページ',
    'jinjer_server.py':       'ローカル API サーバー (port 8899) — jinjer 同期・報告書 API',
    'sync_jinjer.py':         'jinjer 勤怠データ取得スクリプト（Playwright）',
    'report_sync.py':         '作業報告書 Excel ↔ kintai データ変換ライブラリ',
    'create_monthly_report.py': '翌月作業報告書 Excel 自動生成スクリプト（月初実行）',
    'generate_structure.py':  'このファイル — STRUCTURE.md 自動生成（毎朝実行）',
    'migrate_data.py':        'データ移行スクリプト（旧フォーマット対応）',
    'icon-192.png':           'PWA アイコン 192×192px',
    'icon-512.png':           'PWA アイコン 512×512px',
    'icon-apple.png':         'iOS ホーム画面アイコン',
    'STRUCTURE.md':           'このファイル — ディレクトリ構成図（自動生成）',
    '.env':                   '認証情報（git 管理外）— jinjer ログイン情報',
    '.gitignore':             'Git 除外設定',
}


def human_size(path: Path) -> str:
    try:
        s = path.stat().st_size
        if s < 1024:
            return f'{s} B'
        elif s < 1024 * 1024:
            return f'{s / 1024:.1f} KB'
        else:
            return f'{s / 1024 / 1024:.1f} MB'
    except Exception:
        return '?'


def mod_time(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return '?'


def build_tree(base: Path, prefix: str = '', is_last: bool = True) -> list[str]:
    """ファイルツリーを再帰的に構築する（.git 等を除外）"""
    lines = []
    items = sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    items = [i for i in items if i.name not in EXCLUDE]

    for idx, item in enumerate(items):
        last = (idx == len(items) - 1)
        connector = '└── ' if last else '├── '
        ext_prefix = '    ' if last else '│   '

        if item.is_dir():
            lines.append(f'{prefix}{connector}{item.name}/')
            lines.extend(build_tree(item, prefix + ext_prefix, last))
        else:
            lines.append(f'{prefix}{connector}{item.name}')

    return lines


def launchd_status() -> dict[str, str]:
    """launchd ジョブの状態を確認する"""
    jobs = {
        'com.kintai.structure': '毎朝 07:30 — STRUCTURE.md 更新',
        'com.kintai.monthly':   '毎月 1日 08:00 — 翌月 Excel 自動生成',
        'com.kintai.jinjer-end': '毎月 28日 19:00 — jinjer 月末同期',
    }
    result = {}
    try:
        out = subprocess.check_output(['launchctl', 'list'], text=True, stderr=subprocess.DEVNULL)
        loaded = set()
        for line in out.splitlines():
            for job in jobs:
                if job in line:
                    loaded.add(job)
        for job, desc in jobs.items():
            status = '✅ 登録済み' if job in loaded else '❌ 未登録'
            result[job] = f'{status} — {desc}'
    except Exception:
        for job, desc in jobs.items():
            result[job] = f'? 確認不可 — {desc}'
    return result


def generate() -> str:
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    lines = []
    lines.append('# attendance-pwa ディレクトリ構成')
    lines.append('')
    lines.append(f'> 最終更新: {now}  ')
    lines.append('> このファイルは `generate_structure.py` により自動生成されます（毎朝 07:30）。')
    lines.append('')

    # ===== ディレクトリツリー =====
    lines.append('## ファイルツリー')
    lines.append('')
    lines.append('```')
    lines.append('attendance-pwa/')
    tree_lines = build_tree(ROOT)
    lines.extend(tree_lines)
    lines.append('```')
    lines.append('')

    # ===== ファイル一覧表 =====
    lines.append('## ファイル一覧')
    lines.append('')
    lines.append('| ファイル | サイズ | 最終更新 | 説明 |')
    lines.append('|---|---|---|---|')

    all_files = sorted(
        [p for p in ROOT.rglob('*') if p.is_file()
         and not any(ex in p.parts for ex in EXCLUDE)],
        key=lambda p: (len(p.relative_to(ROOT).parts), p.name.lower())
    )
    for f in all_files:
        rel = f.relative_to(ROOT)
        name = str(rel)
        desc = FILE_DESCRIPTIONS.get(f.name, '')
        size = human_size(f)
        mt = mod_time(f)
        lines.append(f'| `{name}` | {size} | {mt} | {desc} |')

    lines.append('')

    # ===== スクリプト役割表 =====
    lines.append('## スクリプト一覧')
    lines.append('')
    lines.append('| スクリプト | 役割 | 実行タイミング |')
    lines.append('|---|---|---|')
    scripts = [
        ('generate_structure.py', 'STRUCTURE.md を自動生成', 'launchd 毎朝 07:30'),
        ('sync_jinjer.py',        'jinjer から勤怠データを取得し JSON 出力', '手動 / launchd 毎月28日'),
        ('jinjer_server.py',      'ローカル API サーバー (port 8899)', '手動 or launchd 起動時'),
        ('report_sync.py',        'Excel ↔ kintai データ変換', 'jinjer_server.py から呼び出し'),
        ('create_monthly_report.py', '翌月作業報告書 Excel を自動生成', 'launchd 毎月1日 08:00'),
        ('migrate_data.py',       '旧フォーマットデータ移行', '手動（必要時のみ）'),
    ]
    for name, role, timing in scripts:
        lines.append(f'| `{name}` | {role} | {timing} |')
    lines.append('')

    # ===== launchd 状態 =====
    lines.append('## launchd 自動化ジョブ状態')
    lines.append('')
    status = launchd_status()
    for job, desc in status.items():
        lines.append(f'- **{job}**: {desc}')
    lines.append('')
    plist_dir = Path.home() / 'Library/LaunchAgents'
    lines.append(f'plist 格納先: `{plist_dir}`')
    lines.append('')

    # ===== API エンドポイント =====
    lines.append('## API エンドポイント (jinjer_server.py port 8899)')
    lines.append('')
    lines.append('| エンドポイント | メソッド | 説明 |')
    lines.append('|---|---|---|')
    endpoints = [
        ('/api/jinjer', 'GET', 'jinjer から勤怠データを取得（`?months=2026-02`）'),
        ('/api/reports', 'GET', 'Work_Report フォルダのファイル一覧を返す'),
        ('/api/reports/read', 'GET', '指定月の Excel を JSON 化（`?year=2026&month=02`）'),
        ('/api/reports/sync', 'POST', 'kintai データを Excel に書き込み'),
        ('/api/reports/generate', 'POST', '翌月 Excel を自動生成'),
        ('/api/structure', 'GET', 'STRUCTURE.md の内容を返す'),
    ]
    for ep, method, desc in endpoints:
        lines.append(f'| `{ep}` | {method} | {desc} |')
    lines.append('')

    # ===== 関連ディレクトリ =====
    lines.append('## 関連ディレクトリ')
    lines.append('')
    lines.append('| パス | 用途 |')
    lines.append('|---|---|')
    related = [
        ('~/Library/Mobile Documents/com~apple~CloudDocs/kintai/', 'jinjer 同期 JSON の iCloud バックアップ'),
        ('~/Library/Mobile Documents/com~apple~CloudDocs/:root/Work_Report/', '作業報告書 Excel ファイル群'),
        ('~/Library/LaunchAgents/', 'launchd plist ファイル'),
    ]
    for path, desc in related:
        lines.append(f'| `{path}` | {desc} |')
    lines.append('')

    return '\n'.join(lines)


if __name__ == '__main__':
    content = generate()
    OUTPUT.write_text(content, encoding='utf-8')
    print(f'✅ STRUCTURE.md を生成しました: {OUTPUT}')
    print(f'   {len(content.splitlines())} 行')
