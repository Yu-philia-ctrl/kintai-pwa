#!/usr/bin/env python3
"""
jinjer同期サーバー — PWAからのTailscale経由リクエストに応答する軽量HTTPサーバー。

使い方:
  python3 jinjer_server.py          # ポート 8899 で起動
  python3 jinjer_server.py 9000     # ポート指定

PWAの「Macから自動同期」ボタンから http://{MacIP}:8899/api/jinjer?months=2026-02 が呼ばれる。
このサーバーは sync_jinjer.py を実行してJSONを返す。

必要なパッケージ（sync_jinjer.pyと同じ）:
  pip install playwright
  playwright install chromium
"""
import asyncio
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path

# このファイルと同じディレクトリの sync_jinjer.py を import
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

try:
    from sync_jinjer import scrape_months, convert_all
    _SCRAPER_OK = True
except ImportError as e:
    print(f'[ERROR] sync_jinjer.py のインポートに失敗: {e}')
    _SCRAPER_OK = False

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
_cache = {}   # month → { ts, data }
CACHE_TTL = 300  # 5分

class JinjerHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f'[jinjer_server] {fmt % args}')

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        # CORS — allow PWA (GitHub Pages or local) to fetch
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != '/api/jinjer':
            self._send_json({'error': 'Not found'}, 404)
            return

        if not _SCRAPER_OK:
            self._send_json({'error': 'sync_jinjer.py のインポートに失敗しています'}, 500)
            return

        params = parse_qs(parsed.query)
        months_str = params.get('months', [''])[0]
        if not months_str:
            from datetime import date
            months_str = date.today().strftime('%Y-%m')

        target_months = [m.strip() for m in months_str.split(',') if m.strip()]
        cache_key = ','.join(sorted(target_months))

        # キャッシュ確認
        if cache_key in _cache and time.time() - _cache[cache_key]['ts'] < CACHE_TTL:
            print(f'[cache hit] {cache_key}')
            self._send_json(_cache[cache_key]['data'])
            return

        print(f'[scrape] {target_months}')
        try:
            all_rows = asyncio.run(scrape_months(target_months))
            pwa_data = convert_all(all_rows)
            _cache[cache_key] = {'ts': time.time(), 'data': pwa_data}
            self._send_json(pwa_data)
        except Exception as e:
            print(f'[ERROR] スクレイプ失敗: {e}')
            self._send_json({'error': str(e)}, 500)


if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', PORT), JinjerHandler)
    print(f'jinjer同期サーバー起動: http://0.0.0.0:{PORT}')
    print(f'  GET /api/jinjer?months=2026-02  — 今月を取得')
    print(f'  GET /api/jinjer?months=2025-12,2026-01,2026-02  — 複数月')
    print('Ctrl+C で終了')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nサーバーを停止しました')
