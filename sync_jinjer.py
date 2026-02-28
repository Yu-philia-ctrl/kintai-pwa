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


LOGS_DIR = Path(__file__).parent / 'logs'

# jinjerタイムカードURLの候補（jinjerのUIバージョンによって異なる場合がある）
def _time_card_urls(year: str, month: str) -> list:
    """試行するタイムカードURL一覧（優先順）"""
    m_int = int(month)
    return [
        f'https://kintai.jinjer.biz/staffs/time_cards?month={year}-{m_int:02d}',
        f'https://kintai.jinjer.biz/staffs/time_cards?month={year}-{m_int}',
        f'https://kintai.jinjer.biz/staffs/attendances?month={year}-{m_int:02d}',
    ]

# 打刻修正申請ボタンを示す可能性のある文字列パターン
_TIMECLOCK_BTN_PATTERNS = re.compile(
    r'打刻修正|タイムカード|勤怠一覧|勤怠修正|出勤|time.?card|attendance', re.IGNORECASE
)


async def _goto_month(page, year: str, month: str, screenshot_prefix: str = '') -> bool:
    """
    指定月のタイムカードページに移動してテーブルを待つ。
    成功したら True、失敗したら False を返す。
    """
    urls = _time_card_urls(year, month)
    for url in urls:
        try:
            print(f'      URL試行: {url}')
            await page.goto(url, wait_until='domcontentloaded', timeout=20000)
            # テーブルの待機（複数パターン）
            for selector in ('table tbody tr', 'table tr', '.time-card', '.attendance-table'):
                try:
                    await page.wait_for_selector(selector, timeout=10000)
                    print(f'      ✅ テーブル検出: {selector}')
                    return True
                except Exception:
                    continue
        except Exception as ex:
            print(f'      ⚠ {url} → {ex}')
    # 全URL失敗 → スクリーンショット保存
    if screenshot_prefix:
        try:
            LOGS_DIR.mkdir(exist_ok=True)
            ss = LOGS_DIR / f'{screenshot_prefix}_{year}-{month}.png'
            await page.screenshot(path=str(ss))
            print(f'      📸 スクリーンショット保存: {ss}')
        except Exception:
            pass
    return False


async def scrape_months(target_months: list) -> dict:
    """複数月をまとめてスクレイプ（ログイン1回で節約）"""
    from playwright.async_api import async_playwright
    all_rows = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx     = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page    = await ctx.new_page()
        page.set_default_timeout(30000)

        if not await _login(page):
            # ログイン失敗時スクリーンショット
            try:
                LOGS_DIR.mkdir(exist_ok=True)
                await page.screenshot(path=str(LOGS_DIR / 'jinjer_login_fail.png'))
                print('      📸 ログイン失敗スクリーンショット → logs/jinjer_login_fail.png')
            except Exception:
                pass
            await browser.close()
            raise RuntimeError('jinjer へのログインに失敗しました。認証情報を .env で確認してください。')

        today_ym = date.today().strftime('%Y-%m')

        for i, ym in enumerate(target_months):
            year, month = ym.split('-')
            print(f'[{i+1}/{len(target_months)}] {ym} を取得中...')

            fetched = False

            # ── 今月: staffs/top → 打刻修正申請ボタン経由（複数パターン対応）──
            if ym == today_ym:
                try:
                    await page.goto(JINJER_TOP, wait_until='domcontentloaded', timeout=20000)
                    btn = page.locator('a, button, [role="button"]').filter(
                        has_text=_TIMECLOCK_BTN_PATTERNS
                    )
                    cnt = await btn.count()
                    print(f'      打刻修正申請ボタン候補: {cnt}件')
                    if cnt > 0:
                        await btn.first.click()
                        for sel in ('table tbody tr', 'table tr'):
                            try:
                                await page.wait_for_selector(sel, timeout=15000)
                                fetched = True
                                print(f'      ✅ UI経由でテーブル取得')
                                break
                            except Exception:
                                continue
                except Exception as ex:
                    print(f'      ⚠ UI経由失敗 ({ex})')

            # ── 直接URLフォールバック ──
            if not fetched:
                fetched = await _goto_month(page, year, month, screenshot_prefix='jinjer_fail')

            if not fetched:
                print(f'      ❌ {ym}: テーブル取得に失敗しました。スキップします。')
                all_rows[ym] = []
                continue

            rows = await page.evaluate(JS_EXTRACT)
            all_rows[ym] = rows
            print(f'      → {len(rows)} 行取得')

            # ── 生データをデバッグ保存（初回のみ） ──
            if i == 0:
                try:
                    LOGS_DIR.mkdir(exist_ok=True)
                    raw_file = LOGS_DIR / f'jinjer_raw_{ym}.json'
                    raw_file.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(f'      📄 生データ保存: {raw_file}')
                except Exception:
                    pass

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


# iCloud Drive パス: :root/attendance/jinjer/ に統合
_ICLOUD_ROOT = Path.home() / 'Library/Mobile Documents/com~apple~CloudDocs/:root'
ICLOUD_DIR = _ICLOUD_ROOT / 'attendance' / 'jinjer'  # jinjer同期ファイル置き場


def save_to_icloud_and_local(target_months: list, pwa_data: dict) -> str:
    """
    スクレイプ結果を iCloud Drive とローカルの両方に保存する。
    保存したファイル名を返す。
    """
    if len(target_months) == 1:
        filename = f'jinjer_sync_{target_months[0]}.json'
    else:
        filename = f'jinjer_sync_{target_months[0]}_to_{target_months[-1]}.json'

    content = json.dumps(pwa_data, ensure_ascii=False, indent=2)

    # ローカルに保存
    local = Path(__file__).parent / filename
    local.write_text(content, encoding='utf-8')
    print(f'✅ ローカル保存 → {local}')

    # iCloud Driveにもコピー (attendance/jinjer/ フォルダ)
    try:
        ICLOUD_DIR.mkdir(parents=True, exist_ok=True)
        icloud = ICLOUD_DIR / filename
        icloud.write_text(content, encoding='utf-8')
        print(f'☁️  iCloud Drive → {icloud}')
        print(f'   iPhoneのファイルアプリ → iCloud Drive → :root → attendance → jinjer フォルダ で確認できます')
    except Exception as e:
        print(f'⚠️  iCloud Driveへのコピー失敗: {e}')

    return filename


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

    filename = save_to_icloud_and_local(target_months, pwa_data)

    # サマリー表示
    total_days = sum(len(v) for v in pwa_data.get('months', {}).values())
    print(f'\n=== 同期完了 ===')
    print(f'   対象月: {", ".join(target_months)}')
    print(f'   合計  : {total_days}日分のデータ')
    print(f'   ファイル: {filename}')
    print('   PWAの「🏢 jinjer同期」→「📂 ファイルから同期」でインポートしてください。')


if __name__ == '__main__':
    main()
