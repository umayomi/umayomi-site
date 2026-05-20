#!/usr/bin/env python3
"""ウマヨミ v2 - db.netkeiba.com経由"""

import json
import time
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("pip install requests beautifulsoup4 lxml")
    sys.exit(1)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}
TIMEOUT = 20
OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)

session = requests.Session()
session.headers.update(HEADERS)
session.headers.update({"Referer": "https://db.netkeiba.com/"})


def fetch(url, encoding="EUC-JP"):
    print(f"  GET {url}")
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.encoding = encoding
        r.raise_for_status()
        # デバッグ: 取得した文字数を表示
        print(f"  -> {len(r.text)} chars, status {r.status_code}")
        time.sleep(random.uniform(3, 6))
        return BeautifulSoup(r.text, "lxml")
    except Exception as e:
        print(f"  failed: {e}")
        return None


def get_target_dates():
    today = datetime.now()
    dates = []
    for day_offset in range(-21, 8):  # 過去3週間〜未来1週間
        d = today + timedelta(days=day_offset)
        if d.weekday() in [5, 6]:
            dates.append(d.strftime("%Y%m%d"))
    return dates


def find_race_ids_for_date(date_str):
    """race_listページからrace_id抽出"""
    url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date_str}"
    soup = fetch(url, encoding="EUC-JP")
    if not soup:
        return []
    text = str(soup)
    race_ids = sorted(set(re.findall(r"race_id=(\d{12})", text)))
    return race_ids


def fetch_race_via_db(race_id):
    """db.netkeiba.com 経由でレース結果を取得"""
    url = f"https://db.netkeiba.com/race/{race_id}/"
    soup = fetch(url)
    if not soup:
        return None
    
    # デバッグ: race_table_01 があるか確認
    table = soup.select_one("table.race_table_01")
    if not table:
        # 別のテーブルクラスも試す
        table = soup.select_one("table.nk_tb_common")
        if not table:
            print(f"    no race table found")
            # ページ内のtable数を確認
            tables = soup.select("table")
            print(f"    page has {len(tables)} tables")
            return None
    
    rows = table.find_all("tr")
    if len(rows) < 2:
        return None
    
    header = [th.text.strip() for th in rows[0].find_all("th")]
    horses = []
    for row in rows[1:]:
        cols = [td.text.strip() for td in row.find_all("td")]
        if len(cols) == len(header):
            horses.append(dict(zip(header, cols)))
    
    if not horses:
        return None
    
    return {
        "race_id": race_id,
        "race_name": f"race_{race_id}",
        "horses": horses,
        "url": url,
    }


def main():
    print(f"\n{'=' * 60}")
    print(f"  umayomi v2 - {datetime.now()}")
    print(f"{'=' * 60}\n")
    
    dates = get_target_dates()
    print(f"target dates: {dates}\n")
    
    all_races = {}
    
    for date_str in dates:
        print(f"\n[{date_str}]")
        race_ids = find_race_ids_for_date(date_str)
        
        if not race_ids:
            print(f"  no race ids")
            continue
        
        print(f"  found {len(race_ids)} race ids: {race_ids[:5]}...")
        
        # 11Rを優先、3つだけ
        main_races = [rid for rid in race_ids if rid.endswith("11")]
        target = main_races[:2] if main_races else race_ids[:2]
        
        for race_id in target:
            print(f"\n  race_id={race_id}")
            race_data = fetch_race_via_db(race_id)
            if race_data:
                race_data["date"] = date_str
                all_races[race_id] = race_data
                print(f"    OK {len(race_data['horses'])} horses")
            else:
                print(f"    NG")
    
    output = OUTPUT_DIR / "races.json"
    with open(output, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": datetime.now().isoformat(),
            "race_count": len(all_races),
            "races": all_races,
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\nDONE:
