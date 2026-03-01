#!/usr/bin/env python3
"""
jinjer同期サーバー — PWAからのTailscale経由リクエストに応答する軽量HTTPサーバー。

使い方:
  python3 jinjer_server.py          # ポート 8899 で起動
  python3 jinjer_server.py 9000     # ポート指定

エンドポイント:
  GET  /api/jinjer?months=2026-02        jinjer 勤怠データ取得
  GET  /api/reports                      作業報告書ファイル一覧
  GET  /api/reports/read?year=2026&month=02  指定月 Excel → JSON
  POST /api/reports/sync                 kintai データ → Excel 書き込み
  POST /api/reports/generate             翌月 Excel 自動生成
  GET  /api/structure                    STRUCTURE.md の内容
  GET  /api/jobs?categories=1,2,3&keywords=Python&platforms=crowdworks,lancers  案件一覧

必要なパッケージ:
  pip install playwright openpyxl
  playwright install chromium
"""
import asyncio
import errno as _errno
import json
import os
import re
import signal as _signal
import subprocess
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
import shutil
from datetime import datetime as _dt, timedelta as _td
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

try:
    from sync_jinjer import scrape_months, convert_all, save_to_icloud_and_local
    _SCRAPER_OK = True
except ImportError as e:
    print(f'[ERROR] sync_jinjer.py のインポートに失敗: {e}')
    _SCRAPER_OK = False

try:
    from report_sync import (
        list_reports, read_report,
        write_report_from_kintai, create_next_month_report,
    )
    _REPORT_OK = True
except (ImportError, SystemExit) as e:
    print(f'[WARN] report_sync.py のインポートに失敗 (報告書機能は無効): {e}')
    _REPORT_OK = False

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
_START_TIME = time.time()           # uptime 計算用
_cache = {}   # cache_key → { ts, data }
CACHE_TTL = 300  # 5分

# Cloudflare Quick Tunnel — 現在のURL を保持するグローバル変数
_CF_TUNNEL_URL: str = ''
_CF_TUNNEL_PROC = None
# ===== iCloud Drive パス定義 =====
# `:root` フォルダ = iCloud Drive のルート（Mac/iPhone 両方からアクセス可能）
_ICLOUD_ROOT = Path.home() / 'Library/Mobile Documents/com~apple~CloudDocs/:root'
ICLOUD_ATT_DIR    = _ICLOUD_ROOT / 'attendance'          # 勤怠アプリ統合フォルダ
ICLOUD_JINJER_DIR = ICLOUD_ATT_DIR / 'jinjer'            # jinjer同期ファイル置き場
ICLOUD_BACKUP_DIR = ICLOUD_ATT_DIR / 'Backup'            # 世代バックアップ置き場
# 旧パス (互換性のため保持)
ICLOUD_DIR = Path.home() / 'Library/Mobile Documents/com~apple~CloudDocs/kintai'
STRUCTURE_MD = _HERE / 'STRUCTURE.md'

# ===== サーバーサイドデータブリッジ =====
# GitHub Pages / localhost / iPhone など異なるオリジン間でデータを共有するためのファイルストア。
# localStorage はオリジンごとに分離されているため、サーバーファイルが橋渡しになる。
DATA_DIR = _HERE / 'data'
DATA_DIR.mkdir(exist_ok=True)
KINTAI_DATA_FILE = DATA_DIR / 'kintai_store.json'   # カレンダーデータ
TASKS_DATA_FILE  = DATA_DIR / 'kintai_tasks.json'   # タスクデータ
# ※ data/ は .gitignore で除外すること（個人データのため）

# ===== フリーランス案件フィード =====
JOBS_CACHE_TTL = 3600  # 1時間
_jobs_cache: dict = {}

# Crowdworks カテゴリ ID → 表示名
CW_CATEGORIES = {
    '1':  'システム開発',
    '2':  'Web制作・Webデザイン',
    '3':  'スマホアプリ開発',
    '7':  'ECサイト構築',
    '9':  'データ入力',
}
# Lancers work_type → 表示名
LANCERS_TYPES = {
    'system': 'システム開発',
    'web':    'Web制作',
    'app':    'アプリ開発',
}
_JOBS_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (kintai-pwa/1.0)'
_ATOM_NS = 'http://www.w3.org/2005/Atom'


def _atom_text(entry, tag: str) -> str:
    return entry.findtext(f'{{{_ATOM_NS}}}{tag}', '') or ''


def _parse_atom_feed(data: bytes, platform: str, category_label: str) -> list:
    """Atom XML バイト列をパースして案件リストを返す"""
    root = ET.fromstring(data)
    jobs = []
    for entry in root.findall(f'{{{_ATOM_NS}}}entry'):
        title   = _atom_text(entry, 'title').strip()
        link_el = entry.find(f'{{{_ATOM_NS}}}link')
        url     = (link_el.get('href', '') if link_el is not None else '')
        summary = (_atom_text(entry, 'summary') or _atom_text(entry, 'content'))
        updated = _atom_text(entry, 'updated')
        uid     = _atom_text(entry, 'id') or url
        # 予算抽出（数字+円）
        budget = ''
        m = re.search(r'([\d,]+)\s*円', summary)
        if m:
            budget = f"¥{m.group(1)}"
        # HTMLタグ除去して短い要約を作成
        clean = re.sub(r'<[^>]+>', '', summary)[:160].strip()
        jobs.append({
            'platform':    platform,
            'category':    category_label,
            'title':       title,
            'url':         url,
            'summary':     clean,
            'budget':      budget,
            'updated':     updated,
            'id':          uid,
            'match_score': 0,
        })
    return jobs


def _fetch_cw_feed(cat_id: str) -> list:
    """Crowdworks カテゴリ Atom フィードを取得してパース"""
    url = f'https://crowdworks.jp/public/jobs/category/{cat_id}.atom'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': _JOBS_UA})
        with urllib.request.urlopen(req, timeout=12) as res:
            data = res.read()
        return _parse_atom_feed(data, 'crowdworks', CW_CATEGORIES.get(cat_id, cat_id))
    except Exception as e:
        print(f'[jobs] CW cat={cat_id}: {e}')
        return []


def _fetch_lancers_feed(work_type: str) -> list:
    """Lancers 検索 Atom フィードを取得してパース"""
    url = f'https://www.lancers.jp/work/search.atom?work_type[]={work_type}&order=new'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': _JOBS_UA})
        with urllib.request.urlopen(req, timeout=12) as res:
            data = res.read()
        return _parse_atom_feed(data, 'lancers', LANCERS_TYPES.get(work_type, work_type))
    except Exception as e:
        print(f'[jobs] Lancers type={work_type}: {e}')
        return []


def _save_to_icloud(target_months: list, pwa_data: dict):
    """スクレイプ結果を iCloud Drive の kintai フォルダに保存する (旧互換)"""
    try:
        ICLOUD_DIR.mkdir(parents=True, exist_ok=True)
        if len(target_months) == 1:
            filename = f'jinjer_sync_{target_months[0]}.json'
        else:
            filename = f'jinjer_sync_{target_months[0]}_to_{target_months[-1]}.json'
        content = json.dumps(pwa_data, ensure_ascii=False, indent=2)
        icloud_path = ICLOUD_DIR / filename
        icloud_path.write_text(content, encoding='utf-8')
        print(f'☁️  iCloud Drive (旧) → {icloud_path}')
    except Exception as e:
        print(f'⚠️  iCloud Driveへの保存失敗: {e}')


def _icloud_backup(kintai_data: dict, label: str = '') -> dict:
    """kintai 勤怠データを iCloud Drive の :root/attendance/ にバックアップする。

    保存先:
      attendance/attendance_backup.json          ← 常に最新版 (既存 app.py と同フォーマット)
      attendance/Backup/attendance_backup_YYYYMMDD_HHMMSS.json ← 世代管理 (最大30件)

    Returns:
      {'ok': bool, 'path': str, 'backup_path': str, 'error': str}
    """
    ts = _dt.now().strftime('%Y%m%d_%H%M%S')
    result = {'ok': False, 'ts': ts}
    try:
        ICLOUD_ATT_DIR.mkdir(parents=True, exist_ok=True)
        ICLOUD_BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # メタ情報を除いたクリーンなデータを作成 (attendance_backup.json 互換フォーマット)
        clean = {k: v for k, v in kintai_data.items()
                 if k not in ('_server_saved_at', '_server_updated_at')}

        content = json.dumps(clean, ensure_ascii=False, indent=2)

        # ── メインバックアップ (attendance_backup.json) を上書き ──────────────
        main_path = ICLOUD_ATT_DIR / 'attendance_backup.json'
        main_path.write_text(content, encoding='utf-8')
        print(f'☁️  iCloud backup → {main_path}')

        # ── 世代バックアップ (Backup/YYYYMMDD_HHMMSS.json) ────────────────────
        bak_name  = f'attendance_backup_{ts}.json'
        bak_path  = ICLOUD_BACKUP_DIR / bak_name
        bak_path.write_text(content, encoding='utf-8')
        print(f'☁️  iCloud Backup  → {bak_path}')

        # 古い世代を 30 件に制限
        baks = sorted(ICLOUD_BACKUP_DIR.glob('attendance_backup_*.json'),
                      key=lambda p: p.stat().st_mtime)
        for old in baks[:-30]:
            old.unlink(missing_ok=True)

        result.update({'ok': True, 'main': str(main_path), 'backup': str(bak_path)})
    except Exception as e:
        print(f'⚠️  iCloudバックアップ失敗: {e}')
        result['error'] = str(e)
    return result


class ReuseHTTPServer(ThreadingMixIn, HTTPServer):
    """マルチスレッド + SO_REUSEADDR HTTPServer
    ThreadingMixIn: 各リクエストを別スレッドで処理 → jinjer同期中もヘルスチェックが応答できる
    """
    allow_reuse_address = True
    daemon_threads = True  # サーバー停止時にデーモンスレッドを強制終了


class JinjerHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f'[jinjer_server] {fmt % args}')

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status=200):
        body = text.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        """POST ボディを JSON として読み込む"""
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        params = parse_qs(parsed.query)

        # ===== /api/health =====
        if path == '/api/health':
            self._send_json({
                'status':          'ok',
                'uptime_seconds':  int(time.time() - _START_TIME),
                'report':          _REPORT_OK,
                'scraper':         _SCRAPER_OK,
            })

        # ===== /api/jinjer =====
        elif path == '/api/jinjer':
            self._handle_jinjer(params)

        # ===== /api/reports =====
        elif path == '/api/reports':
            if not _REPORT_OK:
                self._send_json({'error': 'report_sync.py が利用できません'}, 500)
                return
            self._send_json(list_reports())

        # ===== /api/reports/read =====
        elif path == '/api/reports/read':
            if not _REPORT_OK:
                self._send_json({'error': 'report_sync.py が利用できません'}, 500)
                return
            year  = params.get('year',  [''])[0]
            month = params.get('month', [''])[0]
            if not year or not month:
                self._send_json({'error': 'year と month パラメータが必要です'}, 400)
                return
            data = read_report(year, month)
            if data is None:
                self._send_json({'error': f'{year}年{month}月の Excel が見つかりません'}, 404)
                return
            self._send_json(data)

        # ===== /api/structure =====
        elif path == '/api/structure':
            if STRUCTURE_MD.exists():
                self._send_text(STRUCTURE_MD.read_text(encoding='utf-8'))
            else:
                self._send_text('STRUCTURE.md が生成されていません。generate_structure.py を実行してください。', 404)

        # ===== /api/jobs =====
        elif path == '/api/jobs':
            self._handle_jobs(params)

        # ===== /api/files =====
        elif path == '/api/files':
            self._handle_files_list()

        # ===== /api/files/read =====
        elif path == '/api/files/read':
            self._handle_files_read(params)

        # ===== /api/system/logs =====
        elif path == '/api/system/logs':
            self._handle_system_logs(params)

        # ===== /api/system/logs/dates — 利用可能な日付アーカイブ一覧 =====
        elif path == '/api/system/logs/dates':
            self._handle_log_dates()

        # ===== /api/kintai-data — カレンダーデータ取得 =====
        elif path == '/api/kintai-data':
            self._handle_data_get(KINTAI_DATA_FILE)

        # ===== /api/tasks-data — タスクデータ取得 =====
        elif path == '/api/tasks-data':
            self._handle_data_get(TASKS_DATA_FILE)

        # ===== /api/tailscale-url — Tailscale Serve の HTTPS URL を返す =====
        elif path == '/api/tailscale-url':
            self._handle_tailscale_url()

        # ===== /api/tunnel-url — Cloudflare Quick Tunnel の現在 URL を返す =====
        elif path == '/api/tunnel-url':
            self._handle_tunnel_url()

        # ===== /api/docker/containers — コンテナ一覧 =====
        elif path == '/api/docker/containers':
            self._handle_docker_containers()

        # ===== /api/docker/images — イメージ一覧 =====
        elif path == '/api/docker/images':
            self._handle_docker_images()

        # ===== /api/docker/logs — コンテナログ =====
        elif path == '/api/docker/logs':
            self._handle_docker_logs(params)

        # ===== /api/backup/list — iCloud バックアップ一覧 =====
        elif path == '/api/backup/list':
            self._handle_backup_list()

        # ===== /api/backup/read — バックアップ内容取得 =====
        elif path == '/api/backup/read':
            self._handle_backup_read(params)

        # ===== 静的ファイル配信 — http://localhost:8899/ で PWA を直接表示 =====
        # Safari は HTTPS(GitHub Pages) → HTTP(localhost) の混在コンテンツをブロックするため、
        # Mac では http://localhost:8899/ を直接開くことで同一オリジンになりブロックを回避できる。
        elif path in ('/', '/index.html'):
            self._send_static('index.html')
        elif path in ('/sw.js', '/manifest.json', '/recover.html',
                      '/icon-apple.png', '/icon-192.png', '/icon-512.png'):
            self._send_static(path)

        else:
            self._send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        # ===== /api/kintai-data — カレンダーデータ保存 =====
        if path == '/api/kintai-data':
            self._handle_data_post(KINTAI_DATA_FILE)

        # ===== /api/tasks-data — タスクデータ保存 =====
        elif path == '/api/tasks-data':
            self._handle_data_post(TASKS_DATA_FILE)

        # ===== /api/reports/sync =====
        elif path == '/api/reports/sync':
            if not _REPORT_OK:
                self._send_json({'error': 'report_sync.py が利用できません'}, 500)
                return
            body  = self._read_body()
            year  = str(body.get('year', ''))
            month = str(body.get('month', ''))
            kdata = body.get('kintai_data', {})
            if not year or not month or not kdata:
                self._send_json({'error': 'year, month, kintai_data が必要です'}, 400)
                return
            month = month.zfill(2)
            result = write_report_from_kintai(year, month, kdata)
            self._send_json(result, 200 if result['ok'] else 500)

        # ===== /api/reports/generate =====
        elif path == '/api/reports/generate':
            if not _REPORT_OK:
                self._send_json({'error': 'report_sync.py が利用できません'}, 500)
                return
            body  = self._read_body()
            year  = str(body.get('year', ''))
            month = str(body.get('month', ''))
            if not year or not month:
                self._send_json({'error': 'year と month が必要です'}, 400)
                return
            month = month.zfill(2)
            result = create_next_month_report(year, month)
            self._send_json(result, 200 if result['ok'] else 400)

        # ===== /api/docker/action — コンテナ操作 (start/stop/restart/rm) =====
        elif path == '/api/docker/action':
            self._handle_docker_action()

        # ===== /api/system/restart =====
        elif path == '/api/system/restart':
            self._handle_system_restart()

        # ===== /api/backup/now — 手動 iCloud バックアップ実行 =====
        elif path == '/api/backup/now':
            self._handle_backup_now()

        # ===== /api/backup/restore — バックアップから kintai データを復元 =====
        elif path == '/api/backup/restore':
            self._handle_backup_restore()

        else:
            self._send_json({'error': 'Not found'}, 404)

    # ===== 内部: フリーランス案件フィード =====
    def _handle_jobs(self, params: dict):
        platforms_str  = params.get('platforms',  ['crowdworks,lancers'])[0]
        categories_str = params.get('categories', ['1,2,3'])[0]
        keywords_str   = params.get('keywords',   [''])[0]

        platforms  = [p.strip() for p in platforms_str.split(',')  if p.strip()]
        cat_ids    = [c.strip() for c in categories_str.split(',') if c.strip()]
        keywords   = [k.strip().lower() for k in keywords_str.split(',') if k.strip()]

        cache_key = f"{','.join(sorted(platforms))}|{','.join(sorted(cat_ids))}|{keywords_str}"
        if cache_key in _jobs_cache and time.time() - _jobs_cache[cache_key]['ts'] < JOBS_CACHE_TTL:
            print(f'[jobs cache hit] {cache_key}')
            self._send_json(_jobs_cache[cache_key]['data'])
            return

        all_jobs: list = []
        if 'crowdworks' in platforms:
            for cat in cat_ids:
                all_jobs.extend(_fetch_cw_feed(cat))
        if 'lancers' in platforms:
            for wtype in ['system', 'web', 'app']:
                all_jobs.extend(_fetch_lancers_feed(wtype))

        # キーワードマッチスコアを計算してフィルタリング
        if keywords:
            for job in all_jobs:
                text = (job['title'] + ' ' + job['summary']).lower()
                job['match_score'] = sum(1 for kw in keywords if kw in text)
            all_jobs.sort(key=lambda j: (-j['match_score'], j.get('updated', '')))
        else:
            all_jobs.sort(key=lambda j: j.get('updated', ''), reverse=True)

        result = {
            'jobs':       all_jobs,
            'total':      len(all_jobs),
            'fetched_at': _dt.now().isoformat(),
        }
        _jobs_cache[cache_key] = {'ts': time.time(), 'data': result}
        self._send_json(result)

    # ===== 内部: jinjer 同期 =====
    def _handle_jinjer(self, params: dict):
        if not _SCRAPER_OK:
            self._send_json({'error': 'sync_jinjer.py のインポートに失敗しています'}, 500)
            return

        months_str = params.get('months', [''])[0]
        if not months_str:
            from datetime import date
            months_str = date.today().strftime('%Y-%m')

        target_months = [m.strip() for m in months_str.split(',') if m.strip()]
        cache_key = ','.join(sorted(target_months))

        if cache_key in _cache and time.time() - _cache[cache_key]['ts'] < CACHE_TTL:
            print(f'[cache hit] {cache_key}')
            self._send_json(_cache[cache_key]['data'])
            return

        print(f'[scrape] {target_months}')
        try:
            all_rows = asyncio.run(scrape_months(target_months))
            pwa_data = convert_all(all_rows)
            _cache[cache_key] = {'ts': time.time(), 'data': pwa_data}
            # iCloud + ローカル保存（改善版関数を使用）
            try:
                save_to_icloud_and_local(target_months, pwa_data)
            except Exception as save_e:
                print(f'[WARN] iCloud保存失敗: {save_e}')
            self._send_json(pwa_data)
        except Exception as e:
            print(f'[ERROR] スクレイプ失敗: {e}')
            import traceback; traceback.print_exc()
            self._send_json({'error': str(e)}, 500)

    # ===== ファイル一覧 =====
    def _handle_files_list(self):
        _EXCLUDE_DIRS  = {'.git', '__pycache__', 'node_modules', '.DS_Store'}
        _EXCLUDE_EXTS  = {'.pyc', '.pyo'}
        root = _HERE.resolve()
        files = []
        try:
            for item in sorted(root.rglob('*')):
                rel   = item.relative_to(root)
                parts = rel.parts
                # 除外ディレクトリ配下は全スキップ
                if any(p in _EXCLUDE_DIRS or p.startswith('.') and p != '.env' and p != '.gitignore'
                       for p in parts[:-1]):
                    continue
                if item.name in _EXCLUDE_DIRS:
                    continue
                if item.suffix in _EXCLUDE_EXTS:
                    continue
                try:
                    stat = item.stat()
                    files.append({
                        'path':     str(rel).replace('\\', '/'),
                        'is_dir':   item.is_dir(),
                        'size':     0 if item.is_dir() else stat.st_size,
                        'modified': _dt.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
                    })
                except OSError:
                    pass
        except Exception as e:
            self._send_json({'error': str(e)}, 500)
            return
        self._send_json({'files': files})

    # ===== ファイル内容取得 =====
    def _handle_files_read(self, params: dict):
        file_path = params.get('path', [''])[0]
        if not file_path:
            self._send_json({'error': 'path パラメータが必要です'}, 400)
            return
        # パストラバーサル防止
        try:
            target = (_HERE / file_path).resolve()
            target.relative_to(_HERE.resolve())
        except (ValueError, OSError):
            self._send_json({'error': '不正なパスです'}, 403)
            return
        if not target.exists() or target.is_dir():
            self._send_json({'error': 'ファイルが見つかりません'}, 404)
            return
        # バイナリファイルは返さない
        _BINARY_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.woff', '.woff2',
                        '.ttf', '.eot', '.pdf', '.xlsx', '.xls', '.zip', '.gz'}
        if target.suffix.lower() in _BINARY_EXTS:
            self._send_json({'error': 'バイナリファイルは表示できません'}, 400)
            return
        _MAX = 200 * 1024  # 200KB
        try:
            raw = target.read_text(encoding='utf-8', errors='replace')
            truncated = len(raw) > _MAX
            self._send_json({
                'path':      file_path,
                'content':   raw[:_MAX],
                'size':      target.stat().st_size,
                'truncated': truncated,
            })
        except Exception as e:
            self._send_json({'error': str(e)}, 500)

    def _handle_system_logs(self, params: dict):
        """直近ログを返す。type=server|watchdog, lines=N, date=YYYY-MM-DD"""
        log_type = params.get('type', ['server'])[0]
        lines_n  = min(int(params.get('lines', ['100'])[0]), 1000)
        date_str = (params.get('date', [''])[0]).strip()

        if date_str:
            # 日付指定: アーカイブから取得
            log_file_name = 'watchdog.log' if log_type == 'watchdog' else 'server.log'
            log_file = _LOG_ARCHIVE_DIR / date_str / log_file_name
        else:
            # 最新ファイル
            log_file_name = 'watchdog.log' if log_type == 'watchdog' else 'server.log'
            log_file = _LOG_DIR / log_file_name

        if not log_file.exists():
            self._send_json({'error': 'ログファイルが見つかりません', 'type': log_type, 'date': date_str or 'latest'}, 404)
            return
        all_lines = log_file.read_text(encoding='utf-8', errors='replace').splitlines()
        recent = all_lines[-lines_n:] if len(all_lines) > lines_n else all_lines
        self._send_json({'type': log_type, 'date': date_str or 'latest', 'lines': recent, 'total': len(all_lines)})

    def _handle_log_dates(self):
        """利用可能な日付アーカイブ一覧を返す"""
        dates = []
        if _LOG_ARCHIVE_DIR.exists():
            for d in sorted(_LOG_ARCHIVE_DIR.iterdir(), reverse=True):
                if d.is_dir() and re.match(r'^\d{4}-\d{2}-\d{2}$', d.name):
                    files = [f.name for f in d.iterdir() if f.is_file()]
                    dates.append({'date': d.name, 'files': files})
        self._send_json({'dates': dates, 'archive_dir': str(_LOG_ARCHIVE_DIR), 'count': len(dates)})

    # ===== 静的ファイル配信 =====
    _MIME_MAP = {
        '.html': 'text/html; charset=utf-8',
        '.js':   'application/javascript; charset=utf-8',
        '.json': 'application/json; charset=utf-8',
        '.css':  'text/css; charset=utf-8',
        '.png':  'image/png',
        '.jpg':  'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.svg':  'image/svg+xml',
        '.ico':  'image/x-icon',
        '.woff2': 'font/woff2',
        '.woff':  'font/woff',
        '.txt':   'text/plain; charset=utf-8',
        '.webmanifest': 'application/manifest+json',
    }

    def _send_static(self, rel_path: str):
        """静的ファイルを配信する (パストラバーサル対策済み)"""
        try:
            target = (_HERE / rel_path.lstrip('/')).resolve()
            target.relative_to(_HERE.resolve())  # パストラバーサル防止
        except (ValueError, OSError):
            self._send_json({'error': 'Forbidden'}, 403)
            return
        if not target.exists() or target.is_dir():
            self._send_json({'error': 'Not found'}, 404)
            return
        mime = self._MIME_MAP.get(target.suffix.lower(), 'application/octet-stream')
        try:
            body = target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            # SW / マニフェストはキャッシュを無効化（常に最新を使用）
            if target.name in ('sw.js', 'manifest.json'):
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            else:
                self.send_header('Cache-Control', 'max-age=3600')
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._send_json({'error': str(e)}, 500)

    # ===== データブリッジ: ファイル読み書き =====
    def _handle_data_get(self, data_file: Path):
        """ファイルストアからデータを返す。ファイルが存在しなければ空を返す。"""
        if data_file.exists():
            try:
                raw = data_file.read_text(encoding='utf-8')
                data = json.loads(raw)
                # 最終更新時刻を付与
                stat = data_file.stat()
                data['_server_updated_at'] = _dt.fromtimestamp(stat.st_mtime).isoformat()
                self._send_json(data)
                return
            except Exception as e:
                self._send_json({'error': str(e)}, 500)
                return
        # ファイル未存在 → 空データ返却
        self._send_json({'months': {}, '_server_updated_at': None})

    def _handle_data_post(self, data_file: Path):
        """データをファイルストアに保存する。自動バックアップ付き。"""
        body = self._read_body()
        if not body:
            self._send_json({'error': 'ボディが空です'}, 400)
            return
        try:
            # 既存ファイルがあればローテーションバックアップ (最大3世代)
            if data_file.exists():
                bak = data_file.with_suffix(f'.bak{int(time.time())}')
                data_file.rename(bak)
                # 古いバックアップを3世代を超えたら削除
                baks = sorted(data_file.parent.glob(data_file.stem + '.bak*'), key=lambda p: p.stat().st_mtime)
                for old in baks[:-3]:
                    old.unlink(missing_ok=True)
            body['_server_saved_at'] = _dt.now().isoformat()
            data_file.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding='utf-8')
            # カレンダーデータの場合は iCloud にも自動バックアップ
            icloud_result = {}
            if data_file == KINTAI_DATA_FILE and body.get('months'):
                icloud_result = _icloud_backup(body)
            self._send_json({'ok': True, 'saved_at': body['_server_saved_at'],
                             'icloud': icloud_result})
        except Exception as e:
            self._send_json({'error': str(e)}, 500)

    def _handle_tailscale_url(self):
        """Tailscale Serve の HTTPS URL を検出して返す。
        1. `tailscale serve status --json` で serve 設定を確認
        2. なければ `tailscale status --json` でホスト名を取得して URL を構築
        """
        https_url = None
        method = 'none'

        # ── 方法1: serve 設定から直接取得 ──────────────────────────────────
        try:
            r = subprocess.run(
                ['tailscale', 'serve', 'status', '--json'],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                data = json.loads(r.stdout)
                # SelfDNS フィールドから HTTPS URL を構築
                dns = (data.get('Self', {}).get('DNSName', '') or '').rstrip('.')
                if dns:
                    https_url = f'https://{dns}'
                    method = 'serve-status'
        except Exception:
            pass

        # ── 方法2: tailscale status からホスト名を取得 ────────────────────
        if not https_url:
            try:
                r2 = subprocess.run(
                    ['tailscale', 'status', '--json'],
                    capture_output=True, text=True, timeout=5
                )
                if r2.returncode == 0 and r2.stdout.strip():
                    data2 = json.loads(r2.stdout)
                    dns2 = (data2.get('Self', {}).get('DNSName', '') or '').rstrip('.')
                    if dns2:
                        https_url = f'https://{dns2}'
                        method = 'status'
            except Exception:
                pass

        if https_url:
            self._send_json({
                'https_url': https_url,
                'method': method,
                'note': 'Mac で "tailscale serve --bg 8899" を実行すると、このURLでPWAに接続できます',
            })
        else:
            self._send_json({
                'https_url': None,
                'method': 'not-found',
                'note': 'Tailscale がインストールされていないか、起動していません',
            })

    def _handle_tunnel_url(self):
        """Cloudflare Quick Tunnel の現在 URL を返す。"""
        global _CF_TUNNEL_URL
        if _CF_TUNNEL_URL:
            self._send_json({'url': _CF_TUNNEL_URL, 'active': True})
        else:
            self._send_json({'url': None, 'active': False,
                             'note': 'Cloudflare Tunnel は未起動です'})

    # ===== Docker 管理 =========================================================

    _DOCKER_BIN = '/usr/local/bin/docker'

    def _run_docker(self, args: list, timeout: int = 10) -> tuple[int, str, str]:
        """docker コマンドを実行して (returncode, stdout, stderr) を返す。"""
        try:
            r = subprocess.run(
                [self._DOCKER_BIN] + args,
                capture_output=True, text=True, timeout=timeout
            )
            return r.returncode, r.stdout, r.stderr
        except FileNotFoundError:
            return -1, '', 'docker command not found'
        except subprocess.TimeoutExpired:
            return -1, '', 'docker command timed out'

    def _handle_docker_containers(self):
        """全コンテナの情報を返す (running + stopped)。"""
        rc, out, err = self._run_docker([
            'ps', '-a',
            '--format',
            '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.State}}'
        ])
        if rc != 0:
            self._send_json({'error': err or 'docker ps failed'}, 500)
            return
        containers = []
        for line in out.strip().splitlines():
            parts = line.split('\t')
            if len(parts) < 6:
                continue
            containers.append({
                'id':     parts[0],
                'name':   parts[1],
                'image':  parts[2],
                'status': parts[3],
                'ports':  parts[4],
                'state':  parts[5],
            })
        self._send_json({'containers': containers})

    def _handle_docker_images(self):
        """イメージ一覧を返す。"""
        rc, out, err = self._run_docker([
            'images',
            '--format',
            '{{.ID}}\t{{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}'
        ])
        if rc != 0:
            self._send_json({'error': err or 'docker images failed'}, 500)
            return
        images = []
        for line in out.strip().splitlines():
            parts = line.split('\t')
            if len(parts) < 5:
                continue
            images.append({
                'id':      parts[0],
                'repo':    parts[1],
                'tag':     parts[2],
                'size':    parts[3],
                'created': parts[4],
            })
        self._send_json({'images': images})

    def _handle_docker_logs(self, params: dict):
        """指定コンテナの直近ログを返す。"""
        cid = params.get('id', [''])[0].strip()
        lines = params.get('lines', ['100'])[0]
        if not cid or not cid.replace('-', '').isalnum():
            self._send_json({'error': 'invalid id'}, 400)
            return
        try:
            n = max(1, min(int(lines), 500))
        except ValueError:
            n = 100
        rc, out, err = self._run_docker(['logs', '--tail', str(n), cid], timeout=15)
        # docker logs writes to stderr for actual log content in some versions
        log_text = out + (('\n' + err) if err and rc == 0 else '')
        if rc != 0:
            self._send_json({'error': err or 'docker logs failed'}, 500)
            return
        self._send_json({'id': cid, 'logs': log_text})

    def _handle_docker_action(self):
        """コンテナ操作: start / stop / restart / rm。"""
        body = self._read_body()
        try:
            data = json.loads(body)
        except Exception:
            self._send_json({'error': 'invalid JSON'}, 400)
            return
        action = data.get('action', '')
        cid    = data.get('id', '').strip()
        if not cid or not cid.replace('-', '').isalnum():
            self._send_json({'error': 'invalid id'}, 400)
            return
        if action not in ('start', 'stop', 'restart', 'rm'):
            self._send_json({'error': 'invalid action'}, 400)
            return
        args = [action]
        if action == 'rm':
            args.append('-f')
        args.append(cid)
        rc, out, err = self._run_docker(args, timeout=30)
        if rc != 0:
            self._send_json({'error': err or f'docker {action} failed'}, 500)
        else:
            self._send_json({'ok': True, 'action': action, 'id': cid})

    # ===== iCloud バックアップ管理 =============================================

    def _handle_backup_list(self):
        """iCloud Backup/ フォルダのバックアップ一覧を返す"""
        try:
            backups = []
            # Backup/ ディレクトリのバックアップ一覧
            if ICLOUD_BACKUP_DIR.exists():
                for f in sorted(ICLOUD_BACKUP_DIR.glob('attendance_backup_*.json'),
                                key=lambda p: p.stat().st_mtime, reverse=True):
                    stat = f.stat()
                    backups.append({
                        'name':    f.name,
                        'size_kb': round(stat.st_size / 1024, 1),
                        'mtime':   _dt.fromtimestamp(stat.st_mtime).isoformat(),
                    })
            # メインバックアップ情報
            main_info = None
            main_path = ICLOUD_ATT_DIR / 'attendance_backup.json'
            if main_path.exists():
                stat = main_path.stat()
                main_info = {
                    'size_kb': round(stat.st_size / 1024, 1),
                    'mtime':   _dt.fromtimestamp(stat.st_mtime).isoformat(),
                }
            # jinjer ファイル一覧
            jinjer_files = []
            if ICLOUD_JINJER_DIR.exists():
                for f in sorted(ICLOUD_JINJER_DIR.glob('*.json'),
                                key=lambda p: p.stat().st_mtime, reverse=True):
                    stat = f.stat()
                    jinjer_files.append({
                        'name':    f.name,
                        'size_kb': round(stat.st_size / 1024, 1),
                        'mtime':   _dt.fromtimestamp(stat.st_mtime).isoformat(),
                    })
            self._send_json({
                'icloud_dir':    str(ICLOUD_ATT_DIR),
                'main':          main_info,
                'backups':       backups,
                'jinjer_files':  jinjer_files,
            })
        except Exception as e:
            self._send_json({'error': str(e)}, 500)

    def _handle_backup_read(self, params: dict):
        """指定バックアップファイルの内容を返す"""
        name = params.get('file', [''])[0]
        if not name:
            self._send_json({'error': 'file パラメータが必要です'}, 400)
            return
        # パストラバーサル防止
        if '/' in name or '\\' in name or name.startswith('.'):
            self._send_json({'error': '不正なファイル名です'}, 403)
            return
        target = ICLOUD_BACKUP_DIR / name
        if not target.exists():
            # メインバックアップも試す
            target = ICLOUD_ATT_DIR / name
        if not target.exists() or not target.suffix == '.json':
            self._send_json({'error': 'ファイルが見つかりません'}, 404)
            return
        try:
            data = json.loads(target.read_text(encoding='utf-8'))
            months = list(data.get('months', {}).keys())
            self._send_json({
                'name':   name,
                'data':   data,
                'months': months,
                'mtime':  _dt.fromtimestamp(target.stat().st_mtime).isoformat(),
            })
        except Exception as e:
            self._send_json({'error': str(e)}, 500)

    def _handle_backup_now(self):
        """手動で iCloud バックアップを今すぐ実行する"""
        try:
            if not KINTAI_DATA_FILE.exists():
                self._send_json({'error': 'kintai_store.json が存在しません。先にデータを保存してください。'}, 404)
                return
            data = json.loads(KINTAI_DATA_FILE.read_text(encoding='utf-8'))
            result = _icloud_backup(data, label='manual')
            self._send_json(result)
        except Exception as e:
            self._send_json({'error': str(e)}, 500)

    def _handle_backup_restore(self):
        """バックアップから kintai データを復元する (data/kintai_store.json を上書き)"""
        body = self._read_body()
        file_name = body.get('file', '') if body else ''
        if not file_name:
            # ファイル名未指定時は attendance_backup.json (最新) から復元
            src = ICLOUD_ATT_DIR / 'attendance_backup.json'
        else:
            if '/' in file_name or '\\' in file_name:
                self._send_json({'error': '不正なファイル名です'}, 403)
                return
            src = ICLOUD_BACKUP_DIR / file_name
        if not src.exists():
            self._send_json({'error': f'バックアップファイルが見つかりません: {src.name}'}, 404)
            return
        try:
            data = json.loads(src.read_text(encoding='utf-8'))
            months = list(data.get('months', {}).keys())
            if not months:
                self._send_json({'error': 'バックアップデータに月データがありません'}, 400)
                return
            # 既存ファイルをローテーションバックアップしてから上書き
            if KINTAI_DATA_FILE.exists():
                bak = KINTAI_DATA_FILE.with_suffix(f'.bak{int(time.time())}')
                KINTAI_DATA_FILE.rename(bak)
            data['_server_saved_at']    = _dt.now().isoformat()
            data['_restored_from']      = src.name
            data['_restored_at']        = _dt.now().isoformat()
            KINTAI_DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            self._send_json({
                'ok':     True,
                'source': src.name,
                'months': months,
                'saved_at': data['_server_saved_at'],
            })
        except Exception as e:
            self._send_json({'error': str(e)}, 500)

    def _handle_system_restart(self):
        """launchd 経由でサーバー自身を1秒後に再起動する"""
        def _do_restart():
            import time as _t
            _t.sleep(1)
            uid = subprocess.run(['id', '-u'], capture_output=True, text=True).stdout.strip()
            subprocess.run(
                ['launchctl', 'kickstart', '-k', f'gui/{uid}/com.kintai.server'],
                capture_output=True,
            )
        threading.Thread(target=_do_restart, daemon=True, name='restart-thread').start()
        self._send_json({'status': 'restarting', 'message': '1秒後に再起動します'})


def _server_is_alive(port: int) -> bool:
    """ポートで自サーバーが応答するか確認 (タイムアウト 2 秒)"""
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{port}/api/structure')
        with urllib.request.urlopen(req, timeout=2):
            return True
    except Exception:
        return False


def _kill_port(port: int) -> bool:
    """指定ポートを使用している全プロセスに SIGTERM を送る。成功したら True を返す"""
    try:
        r = subprocess.run(['lsof', '-ti', f':{port}'], capture_output=True, text=True)
        pids = [p.strip() for p in r.stdout.strip().splitlines() if p.strip()]
        if not pids:
            return False
        for pid in pids:
            try:
                os.kill(int(pid), _signal.SIGTERM)
                print(f'  既存プロセス (PID {pid}) を終了しました')
            except ProcessLookupError:
                pass
        time.sleep(2.0)  # プロセス終了を待つ
        return True
    except Exception as ex:
        print(f'  プロセス終了エラー: {ex}')
        return False


_BANNER = f"""jinjer同期サーバー起動: http://0.0.0.0:{{port}}
  📱 PWA ローカルアクセス: http://localhost:{{port}}/
     ↑ Safari では GitHub Pages(HTTPS) からは localhost:8899 に接続できないため
       このURLを直接ブックマークしてください。
  GET  /api/health                      — ヘルスチェック
  GET  /api/jinjer?months=2026-02       — jinjer 勤怠データ取得
  GET  /api/reports                     — 作業報告書ファイル一覧
  GET  /api/reports/read?year=2026&month=02 — 指定月 Excel → JSON
  POST /api/reports/sync                — kintai データ → Excel 書き込み
  POST /api/reports/generate            — 翌月 Excel 自動生成
  GET  /api/structure                   — STRUCTURE.md の内容
  GET  /api/jobs?categories=1,2,3&keywords=Python — フリーランス案件フィード
Ctrl+C で終了"""


if __name__ == '__main__':
    # launchd 管理下かどうかを検出
    # 方法1: plist の EnvironmentVariables で明示設定した KINTAI_MANAGED フラグ
    # 方法2: 親プロセスが launchd (PID 1) かどうか
    _IS_LAUNCHD = bool(os.environ.get('KINTAI_MANAGED')) or (os.getppid() == 1)

    # ─── ステップ1: 既に自サーバーが稼働しているか確認 ───────────────────────
    # launchd 起動時はこのチェックをスキップ:
    #   チェックが True → sys.exit(0) → launchd が KeepAlive で再起動 → 無限ループ
    # 手動起動時のみ「既に稼働中」メッセージを表示して終了する。
    if not _IS_LAUNCHD and _server_is_alive(PORT):
        print(f'[OK] jinjer同期サーバーはポート {PORT} で既に稼働中です（launchd 常駐）。')
        print(f'  再起動が必要な場合:')
        print(f'    launchctl stop com.kintai.server')
        print(f'    launchctl unload ~/Library/LaunchAgents/com.kintai.server.plist')
        print(f'    python3 jinjer_server.py')
        sys.exit(0)

    # ─── ステップ2: launchd 起動時は旧プロセスを先に掃除 ────────────────────
    # クラッシュ直後は旧ソケットがまだ残っている場合があるため、先にポートを解放する。
    if _IS_LAUNCHD:
        _kill_port(PORT)

    # ─── ステップ3: バインドを試みる（SO_REUSEADDR で TIME_WAIT を回避）────
    server = None
    for attempt in range(3):
        try:
            server = ReuseHTTPServer(('0.0.0.0', PORT), JinjerHandler)
            break
        except OSError as e:
            if e.errno != _errno.EADDRINUSE:
                raise
            if attempt >= 2:
                print(f'[ERROR] ポート {PORT} の解放に失敗しました。')
                print(f'  手動で確認: lsof -ti :{PORT}')
                sys.exit(1)
            # ゾンビソケットや他プロセスを排除して再試行
            print(f'[警告] ポート {PORT} が使用中です。既存プロセスを終了して再試行します... ({attempt+1}/2)')
            _kill_port(PORT)

    print(_BANNER.format(port=PORT))

    # ─── ログローテーション & 日付アーカイブ ────────────────────────────────
    _LOG_MAX_BYTES        = 2 * 1024 * 1024   # 2MB 超えたらローテーション
    _LOG_KEEP             = 5                  # サイズローテーション世代数 (.1〜.5)
    _LOG_ROTATE_INTERVAL  = 3600               # 1時間ごとにチェック
    _LOG_DIR              = _HERE / 'logs'
    _LOG_ARCHIVE_DIR      = _HERE / 'logs' / 'dates'  # 日付別アーカイブ
    _LOG_ARCHIVE_KEEP_DAYS = 90               # サーバーログ保持期間（ディスク節約）

    def _rotate_one(log_path: Path):
        """1ファイルのローテーション: path.5 を削除 → .4→.5 … → path→.1"""
        try:
            if not log_path.exists() or log_path.stat().st_size < _LOG_MAX_BYTES:
                return
            # 最古世代を削除してからシフト
            oldest = log_path.with_suffix(f'.log.{_LOG_KEEP}')
            if oldest.exists():
                oldest.unlink()
            for i in range(_LOG_KEEP - 1, 0, -1):
                src = log_path.with_suffix(f'.log.{i}')
                dst = log_path.with_suffix(f'.log.{i+1}')
                if src.exists():
                    src.rename(dst)
            # 現在のファイルを .1 に移動（launchd は新しいファイルに自動で書き続ける）
            log_path.rename(log_path.with_suffix('.log.1'))
            print(f'[INFO] ログローテーション: {log_path.name} → .1 ({log_path.stat().st_size if log_path.exists() else "新規"})', flush=True)
        except Exception as ex:
            print(f'[WARN] ログローテーション失敗 ({log_path.name}): {ex}', flush=True)

    def _rotate_logs():
        """全ログファイルをローテーション"""
        for name in ('server.log', 'server_err.log', 'watchdog.log', 'watchdog_err.log',
                     'jinjer_end.log', 'jinjer_end_err.log'):
            _rotate_one(_LOG_DIR / name)

    def _rotate_loop():
        """起動時に1回 + 以降1時間ごとに実行"""
        _rotate_logs()
        while True:
            time.sleep(_LOG_ROTATE_INTERVAL)
            _rotate_logs()

    threading.Thread(target=_rotate_loop, daemon=True, name='log-rotator').start()

    # ─── 日次ログアーカイブ (SysLog HA: 90日保持) ────────────────────────────
    def _archive_logs_daily():
        """前日の server.log, watchdog.log を logs/dates/YYYY-MM-DD/ にコピー"""
        try:
            yesterday = (_dt.now() - _td(days=1)).strftime('%Y-%m-%d')
            arc_dir = _LOG_ARCHIVE_DIR / yesterday
            arc_dir.mkdir(parents=True, exist_ok=True)
            for name in ('server.log', 'watchdog.log'):
                src_f = _LOG_DIR / name
                if src_f.exists():
                    shutil.copy2(src_f, arc_dir / name)
            print(f'[INFO] 日次ログアーカイブ完了: {arc_dir}', flush=True)
            # 90日以上前のアーカイブを削除
            cutoff = _dt.now() - _td(days=_LOG_ARCHIVE_KEEP_DAYS)
            if _LOG_ARCHIVE_DIR.exists():
                for d in list(_LOG_ARCHIVE_DIR.iterdir()):
                    try:
                        if d.is_dir() and _dt.strptime(d.name, '%Y-%m-%d') < cutoff:
                            shutil.rmtree(d)
                            print(f'[INFO] 古いログアーカイブを削除: {d.name}', flush=True)
                    except Exception: pass
        except Exception as ex:
            print(f'[WARN] 日次ログアーカイブ失敗: {ex}', flush=True)

    def _daily_archive_loop():
        """起動時に1回実行、以降は毎日0時30秒に実行"""
        _archive_logs_daily()
        while True:
            now = _dt.now()
            nxt = (now + _td(days=1)).replace(hour=0, minute=0, second=30, microsecond=0)
            time.sleep(max(1, (nxt - _dt.now()).total_seconds()))
            _archive_logs_daily()

    threading.Thread(target=_daily_archive_loop, daemon=True, name='daily-archiver').start()

    # ─── バックグラウンドで STRUCTURE.md を最新化 ───────────────────────────
    def _update_structure():
        try:
            r = subprocess.run(
                [sys.executable, str(_HERE / 'generate_structure.py')],
                cwd=str(_HERE), capture_output=True, text=True, timeout=30
            )
            if r.returncode == 0:
                print('[INFO] STRUCTURE.md を更新しました', flush=True)
            else:
                print(f'[WARN] STRUCTURE.md 更新失敗: {r.stderr.strip()}', flush=True)
        except Exception as e:
            print(f'[WARN] STRUCTURE.md 更新エラー: {e}', flush=True)

    threading.Thread(target=_update_structure, daemon=True, name='structure-updater').start()

    # ─── Cloudflare Quick Tunnel 自動起動 ────────────────────────────────────
    def _start_cloudflare_tunnel():
        """cloudflared quick tunnel を起動して URL をグローバルに保存する。
        tunnel URL は trycloudflare.com の一時 URL（Mac 再起動ごとに変わる）。
        """
        global _CF_TUNNEL_URL, _CF_TUNNEL_PROC
        import shutil as _shutil
        # launchd は /usr/local/bin を PATH に含まないためフルパスを探す
        _CF_CANDIDATES = [
            '/usr/local/bin/cloudflared',
            '/opt/homebrew/bin/cloudflared',
            '/usr/bin/cloudflared',
        ]
        cloudflared = _shutil.which('cloudflared') or next(
            (p for p in _CF_CANDIDATES if Path(p).exists()), None
        )
        if not cloudflared:
            print('[INFO] cloudflared が見つかりません。Cloudflare Tunnel はスキップします。', flush=True)
            return

        # 既存プロセスが動いていれば停止
        if _CF_TUNNEL_PROC and _CF_TUNNEL_PROC.poll() is None:
            _CF_TUNNEL_PROC.terminate()

        print('[INFO] Cloudflare Quick Tunnel を起動中...', flush=True)
        try:
            proc = subprocess.Popen(
                [cloudflared, 'tunnel', '--url', f'http://localhost:{PORT}',
                 '--no-autoupdate'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
            _CF_TUNNEL_PROC = proc

            # ログを読んで URL を抽出
            url_pattern = re.compile(r'https://[a-z0-9\-]+\.trycloudflare\.com')
            for line in proc.stdout:
                line = line.rstrip()
                m = url_pattern.search(line)
                if m:
                    _CF_TUNNEL_URL = m.group(0)
                    print(f'[INFO] Cloudflare Tunnel URL: {_CF_TUNNEL_URL}', flush=True)
                    break  # URL 取得後はバックグラウンドで動かし続ける

            # stdout を引き続き読み続ける（プロセスを生かすため）
            for _ in proc.stdout:
                pass

        except Exception as ex:
            print(f'[WARN] Cloudflare Tunnel 起動失敗: {ex}', flush=True)

    threading.Thread(target=_start_cloudflare_tunnel, daemon=True, name='cf-tunnel').start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nサーバーを停止しました')
        if _CF_TUNNEL_PROC and _CF_TUNNEL_PROC.poll() is None:
            _CF_TUNNEL_PROC.terminate()
