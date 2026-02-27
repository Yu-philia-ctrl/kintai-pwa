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

必要なパッケージ:
  pip install playwright openpyxl
  playwright install chromium
"""
import asyncio
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
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
except ImportError as e:
    print(f'[ERROR] report_sync.py のインポートに失敗: {e}')
    _REPORT_OK = False

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
_cache = {}   # cache_key → { ts, data }
CACHE_TTL = 300  # 5分
ICLOUD_DIR = Path.home() / 'Library/Mobile Documents/com~apple~CloudDocs/kintai'
STRUCTURE_MD = _HERE / 'STRUCTURE.md'


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

        # ===== /api/jinjer =====
        if path == '/api/jinjer':
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

        else:
            self._send_json({'error': 'Not found'}, 404)

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


if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', PORT), JinjerHandler)
    print(f'jinjer同期サーバー起動: http://0.0.0.0:{PORT}')
    print(f'  GET  /api/jinjer?months=2026-02       — jinjer 勤怠データ取得')
    print(f'  GET  /api/reports                     — 作業報告書ファイル一覧')
    print(f'  GET  /api/reports/read?year=2026&month=02 — 指定月 Excel → JSON')
    print(f'  POST /api/reports/sync                — kintai データ → Excel 書き込み')
    print(f'  POST /api/reports/generate            — 翌月 Excel 自動生成')
    print(f'  GET  /api/structure                   — STRUCTURE.md の内容')
    print('Ctrl+C で終了')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nサーバーを停止しました')
