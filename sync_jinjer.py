#!/usr/bin/env python3
"""
jinjer勤怠データを自動取得し、PWAインポート用JSONを生成するスクリプト。

使い方:
  python3 sync_jinjer.py                     # 今月1ヶ月
  python3 sync_jinjer.py 2026-02             # 指定月1ヶ月
  python3 sync_jinjer.py 2025-10 2026-02     # 範囲指定（開始月〜終了月）

必要なパッケージ:
  pip install playwright
  playwright install chromium

出力: jinjer_sync_YYYY-MM.json（単月）または jinjer_sync_YYYY-MM_to_YYYY-MM.json（複数月）
PWAの「🏢 jinjer同期」ボタンからインポートしてください。
"""
import asyncio
import json
import os
import sys
import re
from pathlib import Path
from datetime import date


# ===== 認証情報（.envから読み込み、なければデフォルト値を使用）=====
def _load_env():
    """標準ライブラリのみで .env を読み込む（python-dotenv不要）"""
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip())

_load_env()

JINJER_SIGN_IN = 'https://kintai.jinjer.biz/staffs/sign_in'
COMPANY_CODE   = os.environ.get('JINJER_COMPANY_CODE', '15733')
EMPLOYEE_CODE  = os.environ.get('JINJER_EMPLOYEE_CODE', '191')
PASSWORD       = os.environ.get('JINJER_PASSWORD', 'philia1904rops')
# ================================================================


def parse_actual(actual_str):
    """'HH:MM~HH:MM' → ('HH:MM', 'HH:MM') or (None, None)"""
    m = re.match(r'(\d{2}:\d{2})~(\d{2}:\d{2})', actual_str or '')
    return (m.group(1), m.group(2)) if m else (None, None)


def to_pwa_status(row):
    """jinjer1行 → PWAステータス (出社/在宅/休み/休日/未)"""
    kyuka   = row.get('kyuka',      '-')
    work    = row.get('workStatus', '-')
    shutsu  = row.get('shutsu',     '00:00')
    zaitaku = row.get('zaitaku',    '00:00')

    if kyuka == '法休':
        return '休日'
    if kyuka in ('所休', '有休(全日)', '有休(半日)', '振休', '代休'):
        return '休み'
    if work in ('勤務', '早退', '遅刻', '遅刻早退'):
        if shutsu != '00:00':
            return '出社'
        if zaitaku != '00:00':
            return '在宅'
        return '在宅'  # 打刻あり・場所不明はデフォルト在宅
    return '未'


def to_date_key(date_text, year, month):
    """'02月02日(月)' + '2026' + '02' → '2026-02-02'"""
    m = re.match(r'(\d{2})月(\d{2})日', date_text or '')
    if not m:
        return None
    mm = int(m.group(1))
    dd = int(m.group(2))
    return f'{year}-{mm:02d}-{dd:02d}'


JS_EXTRACT = """() => {
    const rows = document.querySelectorAll('table tbody tr');
    const data = [];
    rows.forEach(row => {
        const cells = Array.from(row.querySelectorAll('td'))
            .map(td => td.textContent?.replace(/\\s+/g,' ').trim());
        if (cells.length < 20 || !cells[1]?.match(/月\\d+日/)) return;
        const am = (cells[3]||'').match(/(\\d{2}:\\d{2})\\s*〜\\s*(\\d{2}:\\d{2})/);
        data.push({
            date:       cells[1],
            actual:     am ? am[1]+'~'+am[2] : null,
            workStatus: cells[7],
            kyuka:      cells[8],
            shutsu:     cells[15],
            zaitaku:    cells[16],
        });
    });
    return data;
}"""


def months_in_range(start: str, end: str) -> list:
    """'2025-10' 〜 '2026-02' の月リストを返す"""
    sy, sm = map(int, start.split('-'))
    ey, em = map(int, end.split('-'))
    result = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        result.append(f'{y}-{m:02d}')
        m += 1
        if m > 12:
            m = 1
            y += 1
    return result


async def scrape_months(target_months: list) -> dict:
    """複数月をまとめてスクレイプ（ログイン1回で節約）"""
    from playwright.async_api import async_playwright
    all_rows = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()

        print('[ログイン] jinjerにサインイン中...')
        await page.goto(JINJER_SIGN_IN)
        await page.fill('input[name="company_code"]', COMPANY_CODE)
        await page.fill('input[name="email"]',        EMPLOYEE_CODE)
        await page.fill('input[name="password"]',     PASSWORD)
        await page.click('button[type="submit"]')
        await page.wait_for_url('**/staffs/top')
        print('      ✅ ログイン成功')

        for i, ym in enumerate(target_months):
            year, month = ym.split('-')
            print(f'[{i+1}/{len(target_months)}] {ym} を取得中...')
            url = f'https://kintai.jinjer.biz/staffs/time_cards?month={year}-{int(month)}'
            await page.goto(url, wait_until='domcontentloaded')
            await page.wait_for_selector('table tbody tr', timeout=15000)
            rows = await page.evaluate(JS_EXTRACT)
            all_rows[ym] = rows
            print(f'      → {len(rows)} 行取得')

        await browser.close()

    return all_rows


def convert_all(all_rows: dict) -> dict:
    """全月データをPWA形式に変換"""
    months_data = {}
    for ym, rows in all_rows.items():
        year, month = ym.split('-')
        month_data  = {}
        for row in rows:
            dk = to_date_key(row['date'], year, month)
            if not dk:
                continue
            status = to_pwa_status(row)
            start, end = parse_actual(row.get('actual'))
            month_data[dk] = {
                'status': status,
                'start':  start or '',
                'end':    end   or '',
                'memo':   ''     # メモはPWA側を優先するため空
            }
        months_data[ym] = month_data
    return {'months': months_data}


ICLOUD_DIR = Path.home() / 'Library/Mobile Documents/com~apple~CloudDocs/kintai'


def main():
    args = sys.argv[1:]
    today = date.today().strftime('%Y-%m')

    if len(args) == 0:
        target_months = [today]
    elif len(args) == 1:
        target_months = [args[0]]
    elif len(args) == 2:
        target_months = months_in_range(args[0], args[1])
    else:
        print('使い方: python3 sync_jinjer.py [開始月 [終了月]]')
        print('例: python3 sync_jinjer.py 2025-10 2026-02')
        sys.exit(1)

    print(f'=== jinjer同期スクリプト ({" / ".join(target_months)}) ===')

    all_rows = asyncio.run(scrape_months(target_months))
    pwa_data = convert_all(all_rows)

    # ファイル名
    if len(target_months) == 1:
        filename = f'jinjer_sync_{target_months[0]}.json'
    else:
        filename = f'jinjer_sync_{target_months[0]}_to_{target_months[-1]}.json'

    content = json.dumps(pwa_data, ensure_ascii=False, indent=2)

    # ローカルに保存
    local = Path(__file__).parent / filename
    local.write_text(content, encoding='utf-8')
    print(f'\n✅ ローカル保存 → {local}')

    # iCloud Driveにもコピー
    try:
        ICLOUD_DIR.mkdir(parents=True, exist_ok=True)
        icloud = ICLOUD_DIR / filename
        icloud.write_text(content, encoding='utf-8')
        print(f'☁️  iCloud Drive → {icloud}')
    except Exception as e:
        print(f'⚠️  iCloud Driveへのコピー失敗: {e}')

    print(f'\n   対象月: {", ".join(target_months)}')
    print('   iPhoneのファイルアプリ → iCloud Drive → kintai フォルダ')
    print('   → PWAの「🏢 jinjer同期」からインポートしてください。')


if __name__ == '__main__':
    main()
