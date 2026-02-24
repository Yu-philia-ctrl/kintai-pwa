#!/usr/bin/env python3
"""
jinjer勤怠データを自動取得し、PWAインポート用JSONを生成するスクリプト。

使い方:
  python3 sync_jinjer.py [YYYY-MM]   (省略時は今月)

必要なパッケージ:
  pip install playwright
  playwright install chromium

出力: jinjer_sync_YYYY-MM.json
このファイルをPWAの「🔄 jinjer同期」ボタンからインポートしてください。
"""
import asyncio
import json
import sys
import re
from pathlib import Path
from datetime import date

# ===== 認証情報 =====
JINJER_SIGN_IN    = 'https://kintai.jinjer.biz/staffs/sign_in'
COMPANY_CODE      = '15733'
EMPLOYEE_CODE     = '191'
PASSWORD          = 'philia1904rops'
# ====================


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


async def scrape(target_month: str) -> list:
    from playwright.async_api import async_playwright
    year, month = target_month.split('-')

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()

        print('[1/3] jinjerにログイン中...')
        await page.goto(JINJER_SIGN_IN)
        await page.fill('input[name="company_code"]', COMPANY_CODE)
        await page.fill('input[name="email"]',        EMPLOYEE_CODE)
        await page.fill('input[name="password"]',     PASSWORD)
        await page.click('button[type="submit"]')
        await page.wait_for_url('**/staffs/top')

        print(f'[2/3] {target_month} の実績ページを取得中...')
        url = f'https://kintai.jinjer.biz/staffs/time_cards?month={year}-{int(month)}'
        await page.goto(url, wait_until='domcontentloaded')
        # テーブルが描画されるまで待つ（最大15秒）
        await page.wait_for_selector('table tbody tr', timeout=15000)

        print('[3/3] テーブルデータを抽出中...')
        rows = await page.evaluate(JS_EXTRACT)
        await browser.close()
        return rows


def convert(rows: list, target_month: str) -> dict:
    year, month = target_month.split('-')
    month_data = {}

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
            'memo':   ''   # メモはPWA側のものを優先するため空
        }

    return {'months': {f'{year}-{month}': month_data}}


ICLOUD_DIR = Path.home() / 'Library/Mobile Documents/com~apple~CloudDocs/kintai'


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime('%Y-%m')
    print(f'=== jinjer同期スクリプト ({target}) ===')

    rows     = asyncio.run(scrape(target))
    pwa_data = convert(rows, target)

    filename = f'jinjer_sync_{target}.json'
    content  = json.dumps(pwa_data, ensure_ascii=False, indent=2)

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

    print('\n   iPhoneのファイルアプリ → iCloud Drive → kintai フォルダ')
    print('   → PWAの「🏢 jinjer同期」からインポートしてください。')


if __name__ == '__main__':
    main()
