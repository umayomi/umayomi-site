#!/usr/bin/env python3
import json, time, random, re, sys
from datetime import datetime, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9",
}

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)

session = requests.Session()
session.headers.update(HEADERS)
session.headers.update({"Referer": "https://race.netkeiba.com/"})


def fetch_with_debug(url, encoding="EUC-JP"):
    """HTML取得＋詳細デバッグ"""
    print("=" * 60)
    print("GET:", url)
    try:
        r = session.get(url, timeout=20)
        r.encoding = encoding
        r.raise_for_status()
        html = r.text
        
        print(f"  status: {r.status_code}")
        print(f"  chars: {len(html)}")
        print(f"  encoding: {r.encoding}")
        
        # タイトル抽出
        title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        if title_match:
            print(f"  title: {title_match.group(1).strip()}")
        else:
            print("  title: (no title found)")
        
        # 警告キーワード検出
        warnings = []
        for keyword in ["Cloudflare", "robot", "access denied", "メンテナンス", "中止", "ボット", "JavaScript", "javascript", "404", "エラー"]:
            if keyword.lower() in html.lower():
                warnings.append(keyword)
        if warnings:
            print(f"  ⚠️ warnings: {warnings}")
        
        # HTMLの先頭500文字
        preview = re.sub(r"\s+", " ", html[:500])
        print(f"  preview (first 500): {preview}")
        
        # race_id検索（複数パターン）
        patterns = {
            "standard": r"race_id=(\d{12})",
            "data-attr": r'data-race-id="(\d{12})"',
            "result-url": r"/race/result\.html\?race_id=(\d{12})",
            "shutuba-url": r"/race/shutuba\.html\?race_id=(\d{12})",
            "any-12-digit": r'"(\d{12})"',
        }
        for name, pattern in patterns.items():
            matches = re.findall(pattern, html)
            unique = sorted(set(matches))
            if unique:
                print(f"  pattern [{name}]: {len(unique)} unique ids - first: {unique[:3]}")
            else:
                print(f"  pattern [{name}]: 0 matches")
        
        time.sleep(random.uniform(3, 6))
        return BeautifulSoup(html, "lxml")
        
    except Exception as e:
        print(f"  failed: {e}")
        return None


def main():
    print("=" * 60)
    print(f"umayomi DEBUG version - {datetime.now()}")
    print("=" * 60)
    
    # 確実に開催される日付だけ試す
    test_dates = ["20260517", "20260524", "20260523"]
    print(f"\nTesting dates: {test_dates}\n")
    
    for date_str in test_dates:
        print(f"\n{'#' * 60}")
        print(f"# Testing date: {date_str}")
        print(f"{'#' * 60}")
        
        # race_list.html を試す
        url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date_str}"
        fetch_with_debug(url)
    
    # 比較のため、特定のレース詳細ページも試す
    # 2024年日本ダービー（既知のID）
    print(f"\n{'#' * 60}")
    print("# Known race test: 2024 Japan Derby")
    print(f"{'#' * 60}")
    fetch_with_debug("https://db.netkeiba.com/race/202405021211/")
    
    # 出力ファイル（空でOK、デバッグ目的）
    output = OUTPUT_DIR / "races.json"
    with open(output, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": datetime.now().isoformat(),
            "note": "Debug run - check Actions log for HTML preview",
        }, f, ensure_ascii=False, indent=2)
    
    print("\n" + "=" * 60)
    print("DEBUG complete - check log above")
    print("=" * 60)


main()
