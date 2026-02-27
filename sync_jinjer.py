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

JINJER_SIGN_IN  = 'https://kintai.jinjer.biz/sign_in'   # メインログインURL
JINJER_TOP      = 'https://kintai.jinjer.biz/staffs/top'
COMPANY_CODE    = os.environ.get('JINJER_COMPANY_CODE', '15733')
EMPLOYEE_CODE   = os.environ.get('JINJER_EMPLOYEE_CODE', '191')
PASSWORD        = os.environ.get('JINJER_PASSWORD', 'philia1904rops')
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
    // ヘッダーから列インデックスを動的に解決する
    const headers = Array.from(document.querySelectorAll('table thead tr th, table thead tr td'))
        .map(th => th.textContent?.replace(/\\s+/g,' ').trim());
    const idx = name => {
        const i = headers.findIndex(h => h.includes(name));
        return i >= 0 ? i : null;
    };
    // 既知の列名パターン
    const COL_DATE    = idx('日付')   ?? 1;
    const COL_ACTUAL  = idx('実績')   ?? 3;   // 実績 or 打刻実績
    const COL_STATUS  = idx('勤怠')   ?? 7;
    const COL_KYUKA   = idx('休暇')   ?? 8;
    const COL_SHUTSU  = idx('出社')   ?? 15;
    const COL_ZAITAKU = idx('在宅')   ?? 16;

    const rows = document.querySelectorAll('table tbody tr');
    const data = [];
    rows.forEach(row => {
        const cells = Array.from(row.querySelectorAll('td'))
            .map(td => td.textContent?.replace(/\\s+/g,' ').trim());
        if (!cells[COL_DATE]?.match(/月\\d+日/)) return;

        // 実績時間を全セルから広く探す（列位置が変わっても対応）
        let actualStr = cells[COL_ACTUAL] || '';
        if (!actualStr.match(/\\d{2}:\\d{2}/)) {
            // フォールバック: 先頭20列から時刻パターンを探す
            for (let i = 0; i < Math.min(cells.length, 20); i++) {
                if ((cells[i]||'').match(/\\d{2}:\\d{2}\\s*[〜~]\\s*\\d{2}:\\d{2}/)) {
                    actualStr = cells[i]; break;
                }
            }
        }
        const am = actualStr.match(/(\\d{2}:\\d{2})\\s*[〜~]\\s*(\\d{2}:\\d{2})/);

        data.push({
            date:       cells[COL_DATE],
            actual:     am ? am[1]+'~'+am[2] : null,
            workStatus: cells[COL_STATUS]  || '-',
            kyuka:      cells[COL_KYUKA]   || '-',
            shutsu:     cells[COL_SHUTSU]  || '00:00',
            zaitaku:    cells[COL_ZAITAKU] || '00:00',
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


async def _login(page) -> bool:
    """jinjer にログインする。成功したら True を返す"""
    print(f'[ログイン] {JINJER_SIGN_IN} へ移動中...')
    await page.goto(JINJER_SIGN_IN, wait_until='domcontentloaded')

    # --- 企業コードを入力 ---
    company_sel = 'input[name="company_code"], input[id="company_code"], input[placeholder*="企業"]'
    try:
        await page.wait_for_selector(company_sel, timeout=8000)
        await page.fill(company_sel, COMPANY_CODE)
    except Exception:
        # 企業コードフィールドがない場合（既にリダイレクト済みなど）はスキップ
        pass

    # --- 社員番号 / メールアドレス ---
    await page.fill('input[name="email"], input[name="employee_code"]', EMPLOYEE_CODE)

    # --- パスワード ---
    await page.fill('input[name="password"]', PASSWORD)

    # --- 次回から入力を省略（Remember Me）にチェック ---
    try:
        remember_sel = 'input[type="checkbox"]'
        cb = page.locator(remember_sel).first
        if await cb.count() > 0 and not await cb.is_checked():
            await cb.check()
            print('      ☑ 次回から入力を省略 にチェック')
    except Exception:
        pass

    # --- ログインボタン押下 ---
    await page.click('button[type="submit"]')

    try:
        await page.wait_for_url('**/staffs/top', timeout=20000)
        print('      ✅ ログイン成功')
        return True
    except Exception as e:
        print(f'      ❌ ログイン失敗: {e}')
        return False


async def scrape_months(target_months: list) -> dict:
    """複数月をまとめてスクレイプ（ログイン1回で節約）"""
    from playwright.async_api import async_playwright
    all_rows = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx     = await browser.new_context()
        page    = await ctx.new_page()

        if not await _login(page):
            await browser.close()
            raise RuntimeError('jinjer へのログインに失敗しました。認証情報を .env で確認してください。')

        # 今月のデータは 打刻修正申請 ページ経由で取得（UI経由で信頼性向上）
        today_ym = date.today().strftime('%Y-%m')

        for i, ym in enumerate(target_months):
            year, month = ym.split('-')
            print(f'[{i+1}/{len(target_months)}] {ym} を取得中...')

            if ym == today_ym:
                # 今月は staffs/top → 打刻修正申請ボタン経由
                try:
                    await page.goto(JINJER_TOP, wait_until='domcontentloaded')
                    # 「打刻修正申請」ボタンを探してクリック
                    btn = page.locator('a, button').filter(has_text=re.compile(r'打刻修正|タイムカード|勤怠一覧'))
                    if await btn.count() > 0:
                        await btn.first.click()
                        await page.wait_for_selector('table tbody tr', timeout=15000)
                    else:
                        # ボタンが見つからなければ直接URL
                        raise Exception('打刻修正申請ボタンが見つかりません')
                except Exception as ex:
                    print(f'      ⚠ UI経由失敗 ({ex})、直接URLで再試行...')
                    url = f'https://kintai.jinjer.biz/staffs/time_cards?month={year}-{int(month)}'
                    await page.goto(url, wait_until='domcontentloaded')
                    await page.wait_for_selector('table tbody tr', timeout=15000)
            else:
                # 過去月は直接URL
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
