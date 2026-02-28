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
from datetime import datetime as _dt
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

try:
    from sync_jinjer import scrape_months, convert_all
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
ICLOUD_DIR = Path.home() / 'Library/Mobile Documents/com~apple~CloudDocs/kintai'
STRUCTURE_MD = _HERE / 'STRUCTURE.md'

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
    """スクレイプ結果を iCloud Drive の kintai フォルダに保存する"""
    try:
        ICLOUD_DIR.mkdir(parents=True, exist_ok=True)
        if len(target_months) == 1:
            filename = f'jinjer_sync_{target_months[0]}.json'
        else:
            filename = f'jinjer_sync_{target_months[0]}_to_{target_months[-1]}.json'
        content = json.dumps(pwa_data, ensure_ascii=False, indent=2)
        icloud_path = ICLOUD_DIR / filename
        icloud_path.write_text(content, encoding='utf-8')
        print(f'☁️  iCloud Drive → {icloud_path}')
    except Exception as e:
        print(f'⚠️  iCloud Driveへの保存失敗: {e}')


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

        else:
            self._send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        # ===== /api/reports/sync =====
        if path == '/api/reports/sync':
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

        # ===== /api/system/restart =====
        elif path == '/api/system/restart':
            self._handle_system_restart()

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
            _save_to_icloud(target_months, pwa_data)
            self._send_json(pwa_data)
        except Exception as e:
            print(f'[ERROR] スクレイプ失敗: {e}')
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
        """直近ログを返す。type=server|watchdog, lines=N"""
        log_type = params.get('type', ['server'])[0]
        lines_n  = min(int(params.get('lines', ['100'])[0]), 500)
        log_file = _HERE / 'logs' / ('watchdog.log' if log_type == 'watchdog' else 'server.log')
        if not log_file.exists():
            self._send_json({'error': 'ログファイルが見つかりません', 'type': log_type}, 404)
            return
        all_lines = log_file.read_text(encoding='utf-8', errors='replace').splitlines()
        recent = all_lines[-lines_n:] if len(all_lines) > lines_n else all_lines
        self._send_json({'type': log_type, 'lines': recent, 'total': len(all_lines)})

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

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nサーバーを停止しました')
