#!/usr/bin/env python3
"""
create_monthly_report.py — 翌月作業報告書 Excel 自動生成スクリプト

launchd (com.kintai.monthly) により毎月1日 08:00 に自動実行される。
前月のExcelをテンプレートとして翌月のExcelを生成し、
日付・曜日・祝日を自動入力する。

手動実行:
  python3 create_monthly_report.py             # 今月 → 翌月を生成
  python3 create_monthly_report.py 2026-02     # 指定月の翌月を生成
"""
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from report_sync import create_next_month_report, list_reports


def main():
    args = sys.argv[1:]
    today = date.today()

    if len(args) == 0:
        # 今月の翌月を生成
        year  = today.strftime('%Y')
        month = today.strftime('%m')
    elif len(args) == 1:
        try:
            year, month = args[0].split('-')
            month = month.zfill(2)
        except ValueError:
            print(f'エラー: 日付の形式が正しくありません（例: 2026-02）: {args[0]}')
            sys.exit(1)
    else:
        print('使い方: python3 create_monthly_report.py [YYYY-MM]')
        sys.exit(1)

    print(f'=== 翌月作業報告書自動生成 (基準月: {year}-{month}) ===')

    # 現在のファイル一覧を表示
    reports = list_reports()
    if reports:
        print(f'\n既存の報告書:')
        for r in reports:
            print(f'  {r["year"]}-{r["month"]} | {r["status"]:10} | {r["filename"]}')

    print(f'\n翌月の Excel を生成中...')
    result = create_next_month_report(year, month)

    if result['ok']:
        print(f'\n✅ 生成完了!')
        print(f'   ファイル名: {result["filename"]}')
        print(f'   保存先: {result["path"]}')
        print(f'   対象月: {result["year"]}年{int(result["month"])}月')
        print(f'\n   iCloud Drive (Work_Report) に同期されます。')
    else:
        print(f'\n⚠️  生成スキップ: {result["error"]}')


if __name__ == '__main__':
    main()
