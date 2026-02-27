#!/usr/bin/env python3
"""
kintai watchdog — ポート 8899 の死活監視と自動再起動

launchd (com.kintai.watchdog) から 60 秒ごとに呼び出される。
サーバーが応答しない場合は `launchctl kickstart -k` で即時再起動する。

使い方:
  python3 watchdog.py        # 1回チェックして終了
"""
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

PORT      = 8899
LABEL     = 'com.kintai.server'
LOG_FILE  = Path(__file__).parent / 'logs' / 'watchdog.log'
MAX_LOG_LINES = 500   # ログが肥大化しないよう上限管理


def _log(msg: str) -> None:
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    # launchd (KINTAI_MANAGED) 環境では stdout が StandardOutPath (watchdog.log) に直結しているため
    # print のみ使う。それ以外（手動実行）では直接ファイルにも書く。
    print(line, flush=True)
    if not os.environ.get('KINTAI_MANAGED'):
        try:
            LOG_FILE.parent.mkdir(exist_ok=True)
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass


def _is_alive() -> bool:
    """ポート 8899 の /api/health が 5 秒以内に 200 を返すか確認"""
    try:
        with urllib.request.urlopen(
            f'http://127.0.0.1:{PORT}/api/health', timeout=5
        ):
            return True
    except Exception:
        return False


def _kickstart() -> tuple[bool, str]:
    """launchctl kickstart -k で com.kintai.server を強制再起動"""
    uid = subprocess.run(
        ['id', '-u'], capture_output=True, text=True
    ).stdout.strip()
    r = subprocess.run(
        ['launchctl', 'kickstart', '-k', f'gui/{uid}/{LABEL}'],
        capture_output=True, text=True
    )
    return r.returncode == 0, (r.stdout + r.stderr).strip()


if __name__ == '__main__':
    if _is_alive():
        _log(f'[OK] port {PORT} responding')
        sys.exit(0)

    _log(f'[WARN] port {PORT} unresponsive — kickstarting {LABEL}')
    ok, out = _kickstart()
    if ok:
        _log(f'[OK] kickstart succeeded: {out or "(no output)"}')
    else:
        _log(f'[ERROR] kickstart failed: {out}')
        sys.exit(1)
